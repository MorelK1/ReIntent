from agent.model.schemas import Flow, Node, Requirement, Service
from agent.nodes.decision_engine_cp_sat import decision_engine_node_cp_sat
from agent.utils.infeasibility_diagnosis import diagnose_infeasibility


def _two_small_nodes() -> dict[str, Node]:
    # Deliberately small and asymmetric: a requirement of 9 cores fits N7
    # (10 cores) but not N5 (8 cores) alone, and N7 can't hold two of them
    # at once (9 + 9 = 18 > 10).
    return {
        "N5": Node(node_id="N5", node_name="Fog5", tier="fog", node_type="fog_node", cpu_cores=8, ram_gb=16, storage_gb=512),
        "N7": Node(node_id="N7", node_name="Fog7", tier="fog", node_type="fog_node", cpu_cores=10, ram_gb=16, storage_gb=512),
    }


def test_two_requirements_form_the_sole_mus():
    services = {
        "T3": Service(service_id="T3", name="Detection", description="d", current_node="N5"),
        "T4": Service(service_id="T4", name="Tracking", description="d", current_node="N7"),
        "T5": Service(service_id="T5", name="Logging", description="d", current_node="N5"),
    }
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T3", source_span="9 vCPU"),
        Requirement(requirement_id="req-002", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T4", source_span="9 vCPU"),
        # Always-satisfiable: must never appear in any MUS.
        Requirement(requirement_id="req-003", kpi_type="cpu", comparator="gte", target_value=1, unit="vCPU", target_type="service", target_id="T5", source_span="1 vCPU"),
    ]

    state = {
        "requirements": requirements,
        "services": services,
        "flows": {},
        "nodes": _two_small_nodes(),
        "links": [],
    }
    result = decision_engine_node_cp_sat(state)["decision_result"]

    assert result.outcome == "INFEASIBLE"
    diagnosis = result.infeasibility_diagnosis
    assert diagnosis is not None
    assert diagnosis.unknown_subsets == []
    assert diagnosis.mus == [["req-001", "req-002"]]


def test_diagnose_infeasibility_directly_without_the_solver_wrapper():
    services = {
        "T3": Service(service_id="T3", name="Detection", description="d", current_node="N5"),
        "T4": Service(service_id="T4", name="Tracking", description="d", current_node="N7"),
    }
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T3", source_span="9 vCPU"),
        Requirement(requirement_id="req-002", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T4", source_span="9 vCPU"),
    ]

    diagnosis = diagnose_infeasibility(requirements, services, _two_small_nodes(), [], {})

    assert diagnosis.mus == [["req-001", "req-002"]]


def test_singleton_mus_when_a_single_requirement_exceeds_every_node():
    # 200 vCPU exceeds both nodes outright -- infeasible all by itself,
    # independent of any other requirement.
    services = {"T3": Service(service_id="T3", name="Detection", description="d", current_node="N5")}
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="cpu", comparator="gte", target_value=200, unit="vCPU", target_type="service", target_id="T3", source_span="200 vCPU"),
    ]

    diagnosis = diagnose_infeasibility(requirements, services, _two_small_nodes(), [], {})

    assert diagnosis.mus == [["req-001"]]


def test_flow_latency_mus_and_relaxation_uses_the_flows_own_baseline():
    # wcet_ms is a placement-independent constant (Section 7): with no links
    # at all, T1/T2 can only ever end up on the SAME node (no path exists
    # between different ones, so the model forbids splitting them), making
    # hop cost always 0 -- so the flow's cumulative delay is pinned at
    # exactly 5+5=10ms regardless of placement or of this requirement's own
    # target. A target below 10ms is therefore infeasible no matter what;
    # a target at/above 10ms is always feasible -- a crossover known in
    # advance, not just empirically observed.
    services = {
        "T1": Service(service_id="T1", name="Capture", description="d", current_node="N5", wcet_ms=5),
        "T2": Service(service_id="T2", name="Alert", description="d", current_node="N5", wcet_ms=5),
    }
    flows = {
        "F1": Flow(flow_id="F1", name="Pipeline", description="d", path=["T1", "T2"], requirements={"latency": 500}),
    }
    requirements = [
        Requirement(requirement_id="req-001", kpi_type="latency", comparator="lte", target_value=0.0001, unit="ms", target_type="flow", target_id="F1", source_span="tight"),
    ]

    diagnosis = diagnose_infeasibility(requirements, services, _two_small_nodes(), [], flows)

    assert diagnosis.mus == [["req-001"]]


def test_two_independent_mus_groups_are_both_reported():
    # Mirrors ARVR-000031's real shape: one requirement (the flow latency)
    # is infeasible entirely on its own -- a singleton MUS, independent of
    # anything else -- while the other two (T3/T4 cpu) only conflict with
    # EACH OTHER, forming a second, separate MUS.
    services = {
        "T1": Service(service_id="T1", name="Capture", description="d", current_node="N5", wcet_ms=5),
        "T2": Service(service_id="T2", name="Alert", description="d", current_node="N5", wcet_ms=5),
        "T3": Service(service_id="T3", name="Detection", description="d", current_node="N5"),
        "T4": Service(service_id="T4", name="Tracking", description="d", current_node="N7"),
    }
    flows = {
        "F1": Flow(flow_id="F1", name="Pipeline", description="d", path=["T1", "T2"], requirements={"latency": 500}),
    }
    requirements = [
        Requirement(requirement_id="req-lat", kpi_type="latency", comparator="lte", target_value=0.0001, unit="ms", target_type="flow", target_id="F1", source_span="tight"),
        Requirement(requirement_id="req-cpu-a", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T3", source_span="9 vCPU"),
        Requirement(requirement_id="req-cpu-b", kpi_type="cpu", comparator="gte", target_value=9, unit="vCPU", target_type="service", target_id="T4", source_span="9 vCPU"),
    ]

    diagnosis = diagnose_infeasibility(requirements, services, _two_small_nodes(), [], flows)

    assert sorted(diagnosis.mus) == [["req-cpu-a", "req-cpu-b"], ["req-lat"]]
