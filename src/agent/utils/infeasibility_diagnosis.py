"""Diagnoses WHY decision_engine_cp_sat.py found no feasible placement, by
testing feasibility of every 2**N subset of the active requirements -- N is
a handful per intent (never the size of the infrastructure, see the
scalability discussion this module's companion notes refer to), so this
exhaustive enumeration is cheap (at most a few dozen CP-SAT solves) and
gives an EXACT, COMPLETE answer -- every MUS -- rather than one arbitrary
representative, unlike an assumption-literal / unsat-core approach (which
would need the model itself rewired to be reifiable, and only ever returns
one, not-necessarily-minimal, core).

Only meaningful once the full-requirements solve has already returned
INFEASIBLE -- this module does not re-check that itself.

Deliberately diagnosis-only: this module reports every MUS (minimal
infeasible subset), never a correction set or a relaxation value. The
explanation built from it is meant to be a faithful trace of what the
pipeline found, not a recommendation engine.

Removing a requirement from a tested subset never removes its service/flow
from the model: effective_service_capacity/effective_flow_latency_target
(pipeline.py) already fall back to the static baseline when no active
requirement overrides them, so "subset" here means exactly "which
requirements are allowed to override their baseline", never "which
services exist". Baseline requirements themselves are therefore never
togglable: a conflict against an untouched baseline (e.g. this intent's new
ask on one flow against another, unrelated flow's own pre-existing budget)
can only be reported as "incompatible with the existing configuration" --
never pinned on that other baseline element specifically. A deliberate,
documented limitation of this approach, not an oversight.
"""

from itertools import combinations

from ortools.sat.python import cp_model

from agent.model.schemas import Flow, InfeasibilityDiagnosis, Link, Node, Requirement, Service
from agent.utils.csp_constraints_cp_sat import (
    add_capacity_constraints_cp_sat,
    add_fixed_placement_constraints_cp_sat,
    add_latency_constraints_cp_sat,
)

# Per-probe time limit: a probe only needs to prove SAT/UNSAT, never to
# optimize anything, so this is far more generous than any single probe
# should need on this problem size -- a probe that still hits it is
# reported via unknown_subsets, never silently treated as infeasible.
_SOLVE_TIME_LIMIT_S = 5.0


def _is_feasible(
    requirement_subset: list[Requirement],
    services: dict[str, Service],
    nodes: dict[str, Node],
    links: list[Link],
    flows: dict[str, Flow],
) -> bool | None:
    """One feasibility probe: a fresh CP-SAT model, same constraint families
    as decision_engine_node_cp_sat, minus the disruption-minimizing
    objective -- only SAT/UNSAT matters here, not which placement. Returns
    True/False, or None if the solver's time limit was hit without a
    conclusive answer (cp_model.UNKNOWN) -- never equated with INFEASIBLE,
    which would silently misreport a slow-but-feasible subset as a proven
    conflict."""
    service_ids = list(services.keys())
    node_ids = list(nodes.keys())

    model = cp_model.CpModel()
    x = {s: {n: model.NewBoolVar(f"x_{s}_{n}") for n in node_ids} for s in service_ids}
    for s in service_ids:
        model.Add(sum(x[s][n] for n in node_ids) == 1)

    add_capacity_constraints_cp_sat(model, x, nodes, services, requirement_subset)
    add_latency_constraints_cp_sat(model, x, flows, services, links, requirement_subset, node_ids, nodes)
    add_fixed_placement_constraints_cp_sat(model, x, services)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = _SOLVE_TIME_LIMIT_S
    status = solver.Solve(model)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return True
    if status == cp_model.INFEASIBLE:
        return False
    return None  # UNKNOWN or MODEL_INVALID: inconclusive.


def diagnose_infeasibility(
    requirements: list[Requirement],
    services: dict[str, Service],
    nodes: dict[str, Node],
    links: list[Link],
    flows: dict[str, Flow],
) -> InfeasibilityDiagnosis:
    """Only meaningful when `requirements` (the full active set) has
    already been proven INFEASIBLE -- this does not re-check that itself.
    Enumerates all 2**N subsets to find every MUS (minimal infeasible
    subset)."""
    by_id = {r.requirement_id: r for r in requirements}
    ids = list(by_id.keys())

    feasible: dict[frozenset[str], bool | None] = {}
    unknown_subsets: list[list[str]] = []
    for size in range(len(ids) + 1):
        for combo in combinations(ids, size):
            subset_ids = frozenset(combo)
            result = _is_feasible([by_id[i] for i in combo], services, nodes, links, flows)
            feasible[subset_ids] = result
            if result is None:
                unknown_subsets.append(sorted(combo))

    # MUS: proven-infeasible subsets with no proven-infeasible proper
    # subset. A subset whose feasibility is UNKNOWN is never treated as
    # infeasible, so it can never anchor or block a MUS.
    infeasible_sets = {s for s, ok in feasible.items() if ok is False}
    mus = [s for s in infeasible_sets if not any(other < s for other in infeasible_sets)]

    return InfeasibilityDiagnosis(
        mus=[sorted(s) for s in mus],
        unknown_subsets=unknown_subsets,
    )
