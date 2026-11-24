from agent.model.schemas import DecisionResult, Flow, NecessityCheckResult, Node, Requirement, Service
from agent.nodes.decision_engine_cp_sat import decision_engine_node_cp_sat
from agent.prompts.explanation import _fmt, _translate_ids, build_user_prompt


def _node(node_id: str, tier: str) -> Node:
    return Node(
        node_id=node_id,
        node_name=node_id,
        tier=tier,
        node_type=f"{tier}_node",
        cpu_cores=8,
        ram_gb=16,
        storage_gb=100,
    )


def _service(service_id: str, name: str, current_node: str) -> Service:
    return Service(service_id=service_id, name=name, description="", current_node=current_node)


def test_translate_ids_replaces_each_id_with_its_own_service_name():
    services = {
        "T1": _service("T1", "Alpha", "N1"),
        "T10": _service("T10", "Beta", "N1"),
    }
    text = "T1 and T10 are both on N1"

    result = _translate_ids(text, services, {}, {})

    assert result == "the Alpha service and the Beta service are both on N1"


def test_translate_ids_qualifies_nodes_with_tier():
    nodes = {"N5": _node("N5", "fog")}

    result = _translate_ids("service moved to N5", {}, nodes, {})

    assert result == "service moved to the fog node N5"


def test_move_reason_is_kept_other_checks_and_search_trace_are_dropped():
    # Mirrors the real data shape from pipeline.py's explain_moved_services:
    # a forced move is tagged "MOVE: <service> <from>->ux<to>; staying was
    # infeasible: <reason>" -- raw facts, not a pre-written sentence (RULES
    # is what tells the model how to phrase a forced move).
    decision = DecisionResult(
        outcome="FEASIBLE",
        decision={"T2": "N5"},
        eligible_nodes_considered=["N5", "N7"],
        requirements_satisfied=["req-001"],
        new_configuration={"T2": "N5", "T3": "N7"},
        resolution_trace=[
            "cpu_cores on N7: 12.0 of 96 used by ['T3'] ok",
            "MOVE: T2 N1->N5; staying was infeasible: no direct link between N1 and N7",
        ],
        search_trace=["CP-SAT solver status: OPTIMAL"],
    )

    prompt = build_user_prompt(
        "intent text",
        [],
        necessity_result=NecessityCheckResult(
            status="reconfiguration_required", violated_constraints=["req-001"]
        ),
        decision_result=decision,
        services={"T2": _service("T2", "Preprocessing", "N1"), "T3": _service("T3", "Detection", "N7")},
        nodes={"N1": _node("N1", "iot"), "N5": _node("N5", "fog"), "N7": _node("N7", "cloud")},
        flows={},
    )

    assert "staying was infeasible" in prompt
    assert "Preprocessing" in prompt
    # The unrelated capacity-ok line and the solver's search_trace are both
    # dropped from the prompt entirely -- not just separated into another
    # section, never sent at all (see build_user_prompt's own comments on
    # why: zero marginal information, and RULES already tells the model to
    # ignore this kind of content anyway).
    assert "cpu_cores on" not in prompt
    assert "CP-SAT" not in prompt
    assert "OPTIMAL" not in prompt


def test_optional_move_reason_is_labeled_distinctly_from_forced():
    decision = DecisionResult(
        outcome="FEASIBLE",
        decision={"T2": "N5"},
        eligible_nodes_considered=["N5"],
        requirements_satisfied=["req-001"],
        new_configuration={"T2": "N5"},
        resolution_trace=["MOVE: T2 N1->N5; staying was feasible"],
        search_trace=[],
    )

    prompt = build_user_prompt(
        "intent text",
        [],
        necessity_result=NecessityCheckResult(
            status="reconfiguration_required", violated_constraints=["req-001"]
        ),
        decision_result=decision,
        services={"T2": _service("T2", "Preprocessing", "N1")},
        nodes={"N1": _node("N1", "iot"), "N5": _node("N5", "fog")},
        flows={},
    )

    why_section = prompt.split("Per-service move reasons:")[1].split("Other checks")[0]
    assert "staying was feasible" in why_section


def test_infeasible_prompt_carries_the_real_mus_not_a_placeholder():
    # Same genuinely-infeasible fixture as test_infeasibility_diagnosis.py:
    # req-001 and req-002 each fit alone, never together (9+9 > N7's 10).
    nodes = {
        "N5": _node("N5", "fog"),  # 8 cores
        "N7": Node(node_id="N7", node_name="N7", tier="fog", node_type="fog_node", cpu_cores=10, ram_gb=16, storage_gb=100),
    }
    services = {
        "T3": _service("T3", "Detection", "N5"),
        "T4": _service("T4", "Tracking", "N7"),
    }
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T3", source_span="9 vCPU"),
        Requirement(requirement_id="req-002", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T4", source_span="9 vCPU"),
    ]

    decision_result = decision_engine_node_cp_sat({
        "requirements": requirements, "services": services, "flows": {}, "nodes": nodes, "links": [],
    })["decision_result"]
    assert decision_result.outcome == "INFEASIBLE"
    assert decision_result.infeasibility_diagnosis is not None  # the real thing, not a stub

    prompt = build_user_prompt(
        "intent text", requirements, necessity_result=None, decision_result=decision_result,
        services=services, nodes=nodes, flows={},
    )

    # Both conflicting requirements are named concretely, by the service's
    # own name (translated by _translate_ids) -- not left as a generic
    # "something is infeasible" sentence, and not bare service_ids either.
    # The prompt hands the model raw facts (a group of 2), never a
    # pre-written sentence -- RULES is what tells the model how to phrase
    # a group of that size, so it composes the explanation itself.
    assert "  - group of 2:" in prompt
    assert "the Detection service" in prompt
    assert "the Tracking service" in prompt
    # Diagnosis-only: no relaxation/correction suggestion is ever surfaced.
    assert "would have been enough" not in prompt
    assert "relaxing" not in prompt


def test_singleton_mus_is_reported_as_a_group_of_one_not_a_group_of_two():
    # One requirement, infeasible entirely on its own (200 vCPU exceeds
    # every node outright). The prompt must hand the model a group of 1 --
    # never dressed up as a 2+-element group -- so RULES's own
    # singleton-vs-joint phrasing instructions apply correctly; the actual
    # "infeasible on its own" wording is the model's to compose, not
    # pre-written into the prompt data.
    nodes = {"N5": _node("N5", "fog")}
    services = {"T3": _service("T3", "Detection", "N5")}
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="cpu", comparator="gte", target_value=200, unit="vCPU", target_type="service", target_id="T3", source_span="200 vCPU"),
    ]

    decision_result = decision_engine_node_cp_sat({
        "requirements": requirements, "services": services, "flows": {}, "nodes": nodes, "links": [],
    })["decision_result"]
    assert decision_result.outcome == "INFEASIBLE"

    prompt = build_user_prompt(
        "intent text", requirements, necessity_result=None, decision_result=decision_result,
        services=services, nodes=nodes, flows={},
    )

    assert "  - group of 1:" in prompt
    assert "  - group of 2:" not in prompt


def test_fmt_rounds_away_scenario_generated_noise_digits():
    assert _fmt(448.184205) == "448"
    assert _fmt(0.420151) == "0.42"


def test_no_reconfiguration_prompt_reports_the_concrete_resource_margin():
    # NO_RECONFIGURATION NEEDED (decision_result is None, exactly like
    # necessity_checker_node short-circuiting before decision_engine ever
    # runs): C1's cpu requirement is comfortably within N4's capacity, with
    # no other service co-located -- the prompt should carry the concrete
    # margin (14 of 16 used, 2 free), not just a bare "already satisfied"
    # with nothing behind it.
    nodes = {"N4": Node(node_id="N4", node_name="N4", tier="fog", node_type="fog_node", cpu_cores=16, ram_gb=32, storage_gb=100)}
    services = {"C1": _service("C1", "Ingest", "N4")}
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="cpu", comparator="gte", target_value=14, unit="vCPU", target_type="service", target_id="C1", source_span="14 vCPUs"),
    ]

    prompt = build_user_prompt(
        "intent text", requirements,
        necessity_result=NecessityCheckResult(status="reconfiguration_not_required", violated_constraints=[]),
        decision_result=None,
        services=services, nodes=nodes, flows={},
    )

    assert "Current resource usage" in prompt
    assert "14 of 16vCPU used" in prompt
    assert "2vCPU free" in prompt


def test_infeasible_prompt_reports_current_latency_for_both_flows_and_the_achievable_one():
    # Mirrors ARVR-000002 in shape: two flows, each with its own active
    # latency requirement -- F1 (10ms wcet) easily meets its loose 448ms
    # ask, F2 (also 10ms wcet, no links so forced co-located, hop cost 0)
    # cannot meet its impossibly tight ask no matter where it's placed.
    nodes = {
        "N5": _node("N5", "fog"),
        "N7": Node(node_id="N7", node_name="N7", tier="fog", node_type="fog_node", cpu_cores=10, ram_gb=16, storage_gb=100),
    }
    services = {
        "T1": Service(service_id="T1", name="Capture", description="d", current_node="N5", wcet_ms=5),
        "T2": Service(service_id="T2", name="Detect", description="d", current_node="N5", wcet_ms=5),
        "T3": Service(service_id="T3", name="Overlay", description="d", current_node="N5", wcet_ms=5),
        "T4": Service(service_id="T4", name="Render", description="d", current_node="N5", wcet_ms=5),
    }
    flows = {
        "F1": Flow(flow_id="F1", name="ObjectRecognition", description="d", path=["T1", "T2"], requirements={"latency": 500}),
        "F2": Flow(flow_id="F2", name="AugmentedOverlay", description="d", path=["T3", "T4"], requirements={"latency": 500}),
    }
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="latency", comparator="lte", target_value=448, unit="ms", target_type="flow", target_id="F1", source_span="448 ms"),
        Requirement(requirement_id="req-002", kpi_type="latency", comparator="lte", target_value=0.0001, unit="ms", target_type="flow", target_id="F2", source_span="0.420151 seconds"),
    ]

    decision_result = decision_engine_node_cp_sat({
        "requirements": requirements, "services": services, "flows": flows, "nodes": nodes, "links": [],
    })["decision_result"]
    assert decision_result.outcome == "INFEASIBLE"

    prompt = build_user_prompt(
        "intent text", requirements, necessity_result=None, decision_result=decision_result,
        services=services, nodes=nodes, flows=flows, links=[],
    )

    # Both flows' actual current-placement latency are shown, not just the
    # failing one -- necessity_result is None here (same as the ablation
    # path), so this can only come from build_user_prompt computing it
    # itself, not from violated_constraints.
    assert "currently 10ms" in prompt
    # req-002 (F2) is the sole MUS (a group of 1); req-001 (F1) is reported
    # as not part of any infeasible group -- raw facts, not a pre-written
    # sentence (see the "group of" comment in build_user_prompt).
    assert "  - group of 1:" in prompt
    assert "Requirement(s) not part of any infeasible group above" in prompt
    assert "the AugmentedOverlay flow" in prompt
    assert "the ObjectRecognition flow" in prompt
