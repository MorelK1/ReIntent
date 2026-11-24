"""Prompt for the Trace & Explanation Generation node.

Produces a natural, readable synthesis of the pipeline run -- grounded in
the actual trace (intent, extracted requirements, necessity check,
decision engine's resolution_trace/search_trace when relevant), but written
the way a person would actually explain a decision to a colleague, not as a
line-by-line restatement of every trace entry.

Services and nodes are never referred to by their bare internal id in the
final text: service_id (e.g. "T3") is replaced by the service's own name
(e.g. "the Detection service"), node_id (e.g. "N7") is qualified with its
tier (e.g. "the cloud node N7") -- the end user knows service names, not
internal ids, and a tier gives a node meaning without requiring one.
_translate_ids performs this substitution directly on the trace text fed to
the model, rather than leaving it to infer the mapping itself.
"""

import re

from agent.model.schemas import DecisionResult, Flow, Link, NecessityCheckResult, Node, Requirement, Service
from agent.utils.pipeline import KPI_TO_NODE_FIELD, effective_service_capacity, flow_latency_ms
from agent.utils.pipeline import fmt_value as _fmt


_ROLE = """You are an explanation assistant for an intent-driven
service reconfiguration system operating across the
Cloud-Edge-IoT Continuum.

You communicate directly with the operator who
submitted the request.
"""

_TASK = """Explain the outcome of the operator's request
in clear and concise natural language.

Using the operator's intent, the extracted requirements,
the necessity assessment, and the decision result,
explain whether the current configuration can satisfy
the updated requirements and, when applicable, what
reconfiguration is proposed and why.
"""

_GLOSSARY = """Definitions:
- Service: an application component hosted on a
  computing node.
- Flow: an end-to-end sequence of application services.
- Current placement: the configuration before the request.
- Proposed placement: the configuration calculated to
  satisfy the updated requirements.
- Relocation: moving a service from its current node
  to another node.

Necessity check:
- Determines whether the current placement already
  satisfies the updated requirements. If it does, no
  reconfiguration is attempted and there is no decision
  result at all.

Decision outcomes (only exist when reconfiguration was
attempted):
- FEASIBLE: a new placement satisfying the modeled
  requirements has been found.
- INFEASIBLE: no placement can satisfy all modeled
  requirements.
"""

_RULES = """Follow these instructions :

1. FACTUAL ACCURACY
- Use only information provided in the execution context.
- Never invent a reason, a resource shortage, a latency
  violation, or a service movement that isn't explicitly
  supported by that information.

2. EXPLANATION CONTENT
- Briefly explain the requested change.
- State whether the current configuration satisfies
  the updated requirements.
- If reconfiguration is proposed, identify the moved
  services and their destinations.
- Explain the main reasons for reconfiguration when
  they are supported by the provided information.

3. OUTCOME-SPECIFIC RULES
- NO RECONFIGURATION NEEDED:
  Explain that the existing configuration already
  satisfies the updated requirements.
  Do not describe any relocation.
  If current latency and/or current resource usage values
  are supplied, use them to make this concrete instead of
  only asserting it: state the actual margin for the
  requirement(s) that were checked -- e.g. how much of a
  node's capacity is used versus free, or how the current
  latency compares to the requested target -- rather than
  a bare "requirements are already satisfied" with no
  numbers behind it.

- FEASIBLE:
  Describe the proposed placement changes.
  Explain that the new configuration satisfies the
  modeled requirements.
  Only claim minimum relocations when optimality
  has been established by the solver.
  If a per-service move reason is supplied, phrase it
  according to what it reports, not according to a fixed
  wording: when it reports that staying on the current
  node would have been feasible, explain that this
  particular move was not strictly required by any
  constraint -- it happened because the search preferred a
  different overall configuration, not because the service
  had to move. When it reports that staying would have been
  infeasible, name the concrete reason supplied (which
  constraint would have been violated) as why that move was
  necessary.

- INFEASIBLE:
  State the sequence of events plainly, in this order: (1)
  the current configuration does not satisfy the updated
  requirements, (2) a new configuration was therefore
  searched for, and (3) no configuration was found that
  satisfies all of the updated requirements under the
  modeled constraints. Never collapse this into only "the
  current configuration doesn't satisfy the requirements,
  so nothing was applied" -- that skips the fact that a
  search was actually attempted and failed, as opposed to
  never being attempted at all.
  If a conflicting requirement group is supplied, name
  it in plain language as the concrete reason -- and
  phrase it according to its actual size: a single
  requirement reported as infeasible on its own must be
  described as impossible by itself, never framed as a
  conflict "between" two requirements or as something
  that fails only "all at once"; only a group of two or
  more requirements that is reported together should be
  described as a joint conflict that only arises when
  they are combined.
  If current latency and/or current resource usage values
  are supplied, you may state them alongside the requested
  targets so the operator can see both the requested and
  current value for each affected flow or resource, not
  only for the one that fails -- but these current values
  are context only, showing how far today's placement is
  from a target; neither is ever itself the thing that is
  feasible or infeasible, and neither should be presented
  as the reason reconfiguration failed -- only the
  conflicting requirement group (or singleton) reported
  above is the actual, proven reason. A service is free to
  move to another node unless it is explicitly fixed in
  place, so a current-placement resource shortfall on the
  service's present node is never, by itself, "the"
  blocking issue -- the real constraint, when one is
  reported, is the conflict between requirements, which a
  single node's free capacity does not resolve on its own.
  Whenever you say a target is impossible, name the
  REQUESTED value (the operator's own ceiling/floor), never
  the current measured value in its place.
  If a list of requirements not involved in any conflict
  is supplied, state plainly that those targets can still
  be met.
  If no conflicting requirement group is supplied, give a
  concrete reason only if it is explicitly supported by
  the supplied information.
  Make clear that, as a result, no new configuration was
  applied and the current one remains in place.

4. LANGUAGE AND STYLE
- Address the operator directly.
- Do not mention internal components, algorithms, or
  solver terminology.
- Refer to services and nodes by descriptive names
  when available, rather than bare identifiers.
- Use clear, natural language suitable for an
  human operator without optimization expertise.
- Always state a numeric requirement or diagnosis value
  using the value and unit given in the Extracted
  requirements / Decision sections. A quoted source span
  shows the operator's own original wording for context
  only -- never restate or recompute a value in a
  different unit derived from it.

5. OUTPUT
- Write a short paragraph for simple cases.
- Return only the explanation text.
"""

SYSTEM_PROMPT = "\n\n".join([
    _ROLE.strip(),
    _TASK.strip(),
    _GLOSSARY.strip(),
    _RULES.strip(),
])



def _translate_ids(
    text: str, services: dict[str, Service], nodes: dict[str, Node], flows: dict[str, Flow]
) -> str:
    """Replaces every whole-word service_id/node_id/flow_id in text with a
    natural reference -- "the <Name> service", "the <tier> node <id>", "the
    <Name> flow" -- so the model never has to guess the mapping itself, and
    never has a reason to fall back to a bare id in its own prose.

    Some datasets (public_safety) use bare-digit node ids ("0", "1", "2").
    \\b alone isn't enough there -- "\\b0\\b" also matches the "0" in a
    decimal number like "32.0" or "0.5" (a period is a non-word character,
    so a word boundary exists on either side of that digit too), corrupting
    numbers in the resolution_trace text ("32.0" -> "32.the edge node 0").
    The lookaround guards below exclude a match immediately preceded by
    "<digit>." or followed by ".<digit>" -- i.e. the id looks like it's
    actually a decimal's integer or fractional part, not a standalone id."""
    def _sub(pattern_id: str, replacement: str, text: str) -> str:
        pattern = rf"(?<!\d\.)\b{re.escape(pattern_id)}\b(?!\.\d)"
        return re.sub(pattern, replacement, text)

    for sid, service in sorted(services.items(), key=lambda kv: -len(kv[0])):
        # Some datasets (public_safety) name services "X Service" already --
        # appending " service" unconditionally would read "the X Service
        # service". Only append it when the name doesn't already end with it.
        label = service.name if service.name.strip().lower().endswith("service") else f"{service.name} service"
        text = _sub(sid, f"the {label}", text)
    for nid, node in sorted(nodes.items(), key=lambda kv: -len(kv[0])):
        text = _sub(nid, f"the {node.tier} node {nid}", text)
    for fid, flow in sorted(flows.items(), key=lambda kv: -len(kv[0])):
        text = _sub(fid, f"the {flow.name} flow", text)
    return text


def _current_latency_section(
    requirements: list[Requirement],
    flows: dict[str, Flow],
    services: dict[str, Service],
    nodes: dict[str, Node],
    links: list[Link],
) -> str:
    """Actual, current-placement latency for every flow an active latency
    requirement targets -- computed directly here rather than read off
    necessity_result.violated_constraints, which only ever keeps a FAILING
    check (necessity_checker_node drops every check that already passes);
    a flow that already meets its target would otherwise never have its
    actual value surface anywhere, making it impossible for the model to
    state "it currently runs at X ms" for that flow, only for the one that
    overshoots. Silently skips a flow whose latency can't be evaluated
    (flow_latency_ms raises ValueError on a disconnected infrastructure
    graph) -- same "drop rather than guess" policy as elsewhere, and no
    worse than the current total absence of this section."""
    current_placement = {sid: s.current_node for sid, s in services.items()}
    lines = []
    for r in requirements:
        if r.target_type != "flow" or r.kpi_type != "latency":
            continue
        flow = flows.get(r.target_id)
        if flow is None:
            continue
        try:
            actual = flow_latency_ms(flow, links, current_placement, services, nodes)
        except ValueError:
            continue
        lines.append(f"  - {r.target_id}: currently {_fmt(actual)}{r.unit} (requested {_fmt(r.target_value)}{r.unit})")
    return "Current latency under the existing placement:\n" + "\n".join(lines) + "\n\n" if lines else ""


def _current_capacity_section(
    requirements: list[Requirement],
    services: dict[str, Service],
    nodes: dict[str, Node],
) -> str:
    """The cpu/ram counterpart of _current_latency_section: for every node
    hosting a service an active cpu/ram requirement targets, how much of
    that resource is actually in use right now (summed across every
    co-located service, via the same effective_service_capacity used by
    pipeline.py:capacity_satisfied) against the node's capacity -- so a
    NO RECONFIGURATION NEEDED outcome can be explained with a concrete
    margin ("14 of 16 cores used, 2 free") instead of a bare, unsupported
    "requirements are already satisfied". Computed directly for the same
    reason as _current_latency_section: necessity_checker's
    violated_constraints only ever keeps a FAILING check, so a node that
    already has enough headroom would otherwise never have its usage
    surface anywhere. One line per (node, resource) pair actually targeted,
    not every node in the infrastructure -- deduplicated so two
    requirements landing on the same node/resource don't repeat it."""
    current_placement = {sid: s.current_node for sid, s in services.items()}
    seen: set[tuple[str, str]] = set()
    lines = []
    for r in requirements:
        if r.target_type != "service" or r.kpi_type not in KPI_TO_NODE_FIELD:
            continue
        node_id = current_placement.get(r.target_id)
        node = nodes.get(node_id)
        if node is None:
            continue
        node_field = KPI_TO_NODE_FIELD[r.kpi_type]
        if (node_id, node_field) in seen:
            continue
        seen.add((node_id, node_field))
        capacity = getattr(node, node_field)
        hosted = [sid for sid in services if current_placement.get(sid) == node_id]
        used = sum(effective_service_capacity(sid, node_field, services, requirements) for sid in hosted)
        unit = "vCPU" if r.kpi_type == "cpu" else "GB"
        lines.append(
            f"  - {node_id} {r.kpi_type}: {_fmt(used)} of {_fmt(capacity)}{unit} used, "
            f"{_fmt(capacity - used)}{unit} free"
        )
    return "Current resource usage under the existing placement:\n" + "\n".join(lines) + "\n\n" if lines else ""


def build_user_prompt(
    intent_text: str,
    requirements: list[Requirement],
    necessity_result: NecessityCheckResult | None,
    decision_result: DecisionResult | None,
    services: dict[str, Service],
    nodes: dict[str, Node],
    flows: dict[str, Flow],
    links: list[Link] | None = None,
) -> str:
    links = links or []
    requirements_desc = "\n".join(
        f"- {r.requirement_id}: {r.target_type}:{r.target_id} {r.kpi_type} {r.comparator} "
        f"{_fmt(r.target_value)}{r.unit} (source: \"{r.source_span}\")"
        for r in requirements
    ) or "(none extracted)"

    current_latency_section = _current_latency_section(requirements, flows, services, nodes, links)
    current_capacity_section = _current_capacity_section(requirements, services, nodes)

    # graph.py's skip_necessity_checker ablation: decision_engine ran
    # directly on the extracted requirements, with no necessity check at
    # all beforehand. The "Necessity check:" section is omitted entirely
    # below rather than described as skipped -- it's an internal pipeline
    # detail, irrelevant to a reader of the explanation; decision_desc's
    # own "No reconfiguration was performed" wording already says what
    # matters (current placement was/is already sufficient) whether or not
    # this section exists.
    necessity_section = ""
    if necessity_result is not None:
        necessity_desc = (
            f"status={necessity_result.status}\n"
            f"violated_constraints={necessity_result.violated_constraints}"
        )
        necessity_section = f"Necessity check:\n{necessity_desc}\n\n"

    if decision_result is None or (decision_result.outcome == "FEASIBLE" and not decision_result.decision):
        # Same bare fields whether necessity_checker short-circuited before
        # decision_engine ever ran (decision_result is None) or
        # decision_engine ran anyway and confirmed zero moves were needed
        # (decision_result.decision == {}) -- see graph.py's
        # skip_necessity_checker. Kept to raw data, no pre-written
        # conclusion: (1) so this node's own latency/content doesn't become
        # a confound when measuring necessity_checker's end-to-end time
        # contribution, (2) so the model derives "nothing needs to move"
        # itself from placement_changes={} rather than being handed the
        # sentence to repeat.
        decision_desc = "outcome=FEASIBLE\nplacement_changes={}"
    elif decision_result.outcome == "INFEASIBLE":
        # search_trace dropped: today it's always exactly one bare string
        # ("CP-SAT solver status: INFEASIBLE"/"OPTIMAL"), strictly redundant
        # with decision_result.outcome -- zero marginal information, and
        # RULES section 4 tells the model never to use solver terminology
        # anyway, so there's no reason to feed it solver terminology as
        # input either. infeasibility_diagnosis (utils/infeasibility_diagnosis.py)
        # is the real, concrete reason this outcome used to lack entirely:
        # which requirement(s) jointly conflict (a MUS) -- diagnosis only,
        # no relaxation/correction suggestion (deliberately out of scope,
        # see that module's docstring).
        req_by_id = {r.requirement_id: r for r in requirements}

        def _describe(rid: str) -> str:
            r = req_by_id.get(rid)
            if r is None:
                return rid
            return f"{r.target_type}:{r.target_id} {r.kpi_type} {r.comparator} {_fmt(r.target_value)}{r.unit}"

        diagnosis_desc = "(no further diagnosis available)"
        diagnosis = decision_result.infeasibility_diagnosis
        if diagnosis is not None and diagnosis.mus:
            # Raw facts only -- a group's size (1 vs 2+) is data, not a
            # pre-written sentence; RULES section 3 (INFEASIBLE) is what
            # tells the model how to phrase a singleton versus a joint
            # conflict, so the model composes the explanation itself
            # instead of being handed the wording to repeat verbatim.
            mus_lines = [
                "  - group of " + str(len(group)) + ": " + " ; ".join(_describe(rid) for rid in group)
                for group in diagnosis.mus
            ]

            # Every requirement NOT named in any MUS group is, by
            # construction, not a source of the infeasibility.
            mus_ids = {rid for group in diagnosis.mus for rid in group}
            achievable_lines = [f"  - {_describe(r.requirement_id)}" for r in requirements if r.requirement_id not in mus_ids]

            sections = [
                "Minimal infeasible requirement group(s) found:\n" + "\n".join(mus_lines),
            ]
            if achievable_lines:
                sections.append(
                    "Requirement(s) not part of any infeasible group above:\n" + "\n".join(achievable_lines)
                )
            diagnosis_desc = "\n".join(sections)
        decision_desc = (
            f"outcome=INFEASIBLE\n"
            f"current_configuration={decision_result.new_configuration}\n"
            f"{diagnosis_desc}"
        )
    else:
        # resolution_trace mixes per-service move-reason lines with
        # general per-node/per-flow capacity/latency checks (every node,
        # every flow, mostly "ok"). Only the move-reason lines are kept --
        # see pipeline.py:explain_moved_services (shared by both solver
        # backends), which tags each one with a "MOVE:" prefix precisely so
        # this filter doesn't depend on matching English wording. The "ok"
        # checks are dropped entirely: they're the one piece of
        # information necessity_checker's violated_constraints can't
        # provide (which SPECIFIC service moved and why, including a
        # service that moved with no violation of its own, purely as a
        # side effect of the search) is move_reasons; the "ok" checks add
        # nothing an explanation needs, and RULES already says not to list
        # every check -- no reason to send what we then tell it to ignore.
        # The kept lines are raw facts ("service X->Y; staying was
        # feasible/infeasible: ..."), never a pre-written sentence -- RULES
        # section 3 (FEASIBLE) is what tells the model how to phrase each
        # case, same pattern as the INFEASIBLE branch's MUS groups.
        move_reasons = [
            line[len("MOVE: ") :] for line in decision_result.resolution_trace if line.startswith("MOVE: ")
        ]
        move_reasons_desc = "\n".join(f"  - {line}" for line in move_reasons) or "  (none)"
        decision_desc = (
            f"outcome=FEASIBLE\n"
            f"placement_changes={decision_result.decision}\n"
            f"new_configuration={decision_result.new_configuration}\n"
            f"Per-service move reasons:\n{move_reasons_desc}"
        )

    prompt = (
        f"User intent:\n\"{intent_text}\"\n\n"
        f"Extracted requirements:\n{requirements_desc}\n\n"
        f"{current_latency_section}"
        f"{current_capacity_section}"
        f"{necessity_section}"
        f"Decision:\n{decision_desc}"
    )
    # Translated last, over the whole assembled prompt, so every id --
    # wherever it came from (requirements, necessity, raw trace lines) --
    # gets the same natural substitution in one pass.
    return _translate_ids(prompt, services, nodes, flows)
