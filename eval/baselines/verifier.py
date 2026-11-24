"""Independently verifies a baseline LLM's proposed placement against our
REAL capacity/latency/fixed-placement checks (agent/utils/pipeline.py) --
the LLM's own claim that its placement is valid is never trusted at face
value, exactly like the real pipeline never trusts an unverified placement
(decision_engine always checks candidates before returning one). Shared by
both "direct llm"/ and cot/ baselines -- the verification logic is
identical regardless of which prompting strategy produced the candidate.

Unlike expérimentations/public_safety/baseline/verifier.py (which this is
modeled on, and which never had this concept), this also checks
fixed_placement: pipeline.py's capacity_satisfied/latency_satisfied cover
capacity and latency only -- fixed_placement is enforced by the SOLVERS
themselves (csp_solver.py / csp_constraints_cp_sat.py), never by a
pipeline.py helper, so a baseline LLM violating it (moving a
fixed_placement=True service despite the prompt's explicit rule) would
otherwise pass verification silently.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agent.model.schemas import Flow, Link, Node, Requirement, Service  # noqa: E402
from agent.utils.pipeline import capacity_satisfied, latency_satisfied  # noqa: E402


def verify_placement(
    nodes: dict[str, Node],
    services: dict[str, Service],
    flows: dict[str, Flow],
    links: list[Link],
    requirements: list[Requirement],
    placement: dict[str, str],
) -> tuple[bool, list[tuple[bool, str]]]:
    """placement: the LLM's proposed new_placement (service_id -> node_id),
    already backfilled with current_node for any service it omitted (see
    run_sample.py) -- an incomplete placement from the LLM is a formatting
    problem, not grounds to skip verification of what IS given."""
    checks: list[tuple[bool, str]] = []
    checks.extend(capacity_satisfied(nodes, services, requirements, placement))
    checks.extend(latency_satisfied(flows, services, links, requirements, placement, nodes))
    for sid, service in services.items():
        if service.fixed_placement:
            ok = placement.get(sid) == service.current_node
            checks.append((
                ok,
                f"fixed_placement: {sid} must stay on {service.current_node} -- "
                f"{'ok' if ok else f'VIOLATED, moved to {placement.get(sid)!r}'}",
            ))
    ok = all(c_ok for c_ok, _ in checks)
    return ok, checks
