"""Runs necessity_checker + decision_engine (CP-SAT) ONCE per scenario, on
the scenario's OWN ground_truth.requirements -- not on anything extracted
by a model. No LLM call anywhere in this script; it's deterministic, so it
runs all 500 scenarios locally in a few seconds.

Why this exists: so far, "the decision a model should have reached" meant
scenarios.json's own decision/p1/moved_components fields, produced by an
external benchmark generator -- already checked to agree with our own
check_candidate_placement on 487/500 scenarios (the other 13 are a known
floor()-rounding edge case in loader.py, not a methodology gap). Running
OUR OWN necessity_checker_node/decision_engine_node_cp_sat on the true
requirements removes that remaining dependency: the "reference" decision
and a model's "predicted" decision (built the normal way, from LLM
extraction) then come out of the exact same function, so any difference
between them is attributable to extraction, not to two different solvers
disagreeing about what "satisfied" means.

Writes eval/results_reference/reference_decisions.jsonl -- one record per
scenario: {scenario_id, necessity_result, decision_result, ref_decision,
ref_moved}. Also prints a sanity-check comparison against scenarios.json's
own decision/moved_components as it goes (expect ~97-98% agreement, per
the above). The exact same sanity check is a cheap, free, no-LLM first
step before launching an actual campaign against a NEW scenarios.json
(e.g. benchmark_v3) -- it would surface a solver/ground-truth disagreement
rate before spending any OpenRouter budget on it.

Reads eval/experiment_500/scenarios.json and eval/system_model/ by
default; set EVAL_EXPERIMENT_DIR, EVAL_SYSTEM_MODEL_DIR and EVAL_OUT_PATH
(same EVAL_* pattern as loader.py / run_batch_full_graph.py) to check
another campaign instead, e.g.:
    EVAL_SYSTEM_MODEL_DIR=eval/system_model_normalized \\
    EVAL_EXPERIMENT_DIR=eval/benchmark_v3 \\
    EVAL_OUT_PATH=eval/results_reference/reference_decisions_v3.jsonl \\
    PYTHONPATH=src venv/bin/python3 eval/run_reference_decision.py

Usage (from the project root):
    PYTHONPATH=src venv/bin/python3 eval/run_reference_decision.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "system_model"))

from agent.model.schemas import Requirement  # noqa: E402
from agent.nodes.decision_engine_cp_sat import decision_engine_node_cp_sat  # noqa: E402
from agent.nodes.necessity_checker import necessity_checker_node  # noqa: E402
from loader import load_flows, load_links, load_nodes, load_services  # noqa: E402

SCENARIOS_PATH = (
    Path(os.environ.get("EVAL_EXPERIMENT_DIR", str(Path(__file__).parent / "experiment_500"))) / "scenarios.json"
)
OUT_PATH = Path(
    os.environ.get("EVAL_OUT_PATH", str(Path(__file__).parent / "results_reference" / "reference_decisions.jsonl"))
)

# scenarios.json's own requirements[].kpi_type spelling -> our schema's.
KPI_REMAP = {"e2e_latency": "latency"}


def ground_truth_requirements(scenario: dict) -> list[Requirement]:
    reqs = []
    for i, req in enumerate(scenario["requirements"]):
        kpi = KPI_REMAP.get(req["kpi_type"], req["kpi_type"])
        reqs.append(Requirement(
            kpi_type=kpi, comparator=req["comparator"], target_value=req["new_value"],
            unit=req["unit"], target_type=req["target_type"], target_id=req["target_id"],
            source_span="", requirement_id=f"gt-{i}",
        ))
    return reqs


def ref_decision_label(necessity_result, decision_result) -> str:
    """Same reduction explanation.py/the scoring notebook already use for a
    model's own predicted_decision -- applied here to the reference run
    instead, so both sides are comparable on equal terms."""
    if necessity_result.status == "reconfiguration_not_required":
        return "STAY"
    if decision_result is None:
        return "STAY"
    if decision_result.outcome == "INFEASIBLE":
        return "INFEASIBLE"
    if not decision_result.decision:
        return "STAY"
    return "OFFLOAD"


def main() -> None:
    with open(SCENARIOS_PATH) as f:
        scenarios = json.load(f)

    nodes = load_nodes()
    links = load_links()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    n_decision_match = 0
    n_moved_match = 0
    n_offload_gt = 0

    with open(OUT_PATH, "w") as out:
        for i, scenario in enumerate(scenarios, start=1):
            services = load_services(scenario)
            flows = load_flows(scenario)
            requirements = ground_truth_requirements(scenario)

            state = {
                "services": services, "nodes": nodes, "links": links,
                "flows": flows, "requirements": requirements,
            }

            necessity_result = necessity_checker_node(state)["necessity_result"]
            decision_result = None
            if necessity_result.status == "reconfiguration_required":
                decision_result = decision_engine_node_cp_sat(state)["decision_result"]

            ref_decision = ref_decision_label(necessity_result, decision_result)
            ref_moved = sorted(decision_result.decision.keys()) if (decision_result and decision_result.decision) else []

            gt_decision = scenario["decision"]
            gt_moved = sorted(f"C{n}" for n in scenario["moved_components"])

            if ref_decision == gt_decision:
                n_decision_match += 1
            else:
                print(f"{scenario['id']}: DECISION MISMATCH ref={ref_decision} gt={gt_decision}")
            if gt_decision == "OFFLOAD":
                n_offload_gt += 1
                if ref_moved == gt_moved:
                    n_moved_match += 1
                elif ref_decision == gt_decision:
                    print(f"{scenario['id']}: same label OFFLOAD but different moved set -- "
                          f"ref={ref_moved} gt={gt_moved} (feasible_placements={scenario.get('feasible_placements')})")

            record = {
                "scenario_id": scenario["id"],
                "necessity_result": necessity_result.model_dump(),
                "decision_result": decision_result.model_dump() if decision_result else None,
                "ref_decision": ref_decision,
                "ref_moved": ref_moved,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

            if i % 100 == 0:
                print(f"[{i}/{len(scenarios)}] done")

    n = len(scenarios)
    print(f"\n=== {n} scenarios, written to {OUT_PATH} ===")
    print(f"decision label agreement with scenarios.json: {n_decision_match}/{n} ({n_decision_match/n:.3f})")
    print(f"moved-set agreement on OFFLOAD scenarios: {n_moved_match}/{n_offload_gt} ({n_moved_match/n_offload_gt:.3f})")


if __name__ == "__main__":
    main()
