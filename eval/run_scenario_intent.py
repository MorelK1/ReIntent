"""Runs ONE real natural-language intent through the REAL, FULL graph
(intent_grounding -> necessity_checker -> decision_engine -> explanation)
against one eval/experiment_500 scenario's system model -- a single manual
sanity check before building out the full 500-scenario campaign.

Usage:
    source venv/bin/activate
    PYTHONPATH=src python3 eval/run_scenario_intent.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "system_model"))

from agent.graph import build_graph  # noqa: E402
from loader import load_flows, load_links, load_nodes, load_scenario, load_services  # noqa: E402

SCENARIO_ID = "ARVR-000001"
# FEASIBLE/OFFLOAD test: 2 ram floors (C4 AR Rendering, C2 Object
# Recognition), ground truth decision=OFFLOAD, minimum_migrations=1,
# moved_components=[4] (AR Rendering) -- see
# eval/experiment_500/scenarios.json. Used to check the FEASIBLE
# explanation now that explain_moved_services (pipeline.py) emits raw
# "MOVE: ..." facts instead of pre-written sentences -- the model should
# compose its own "why this service moved" wording from that data plus
# RULES, not echo a fixed template.
INTENT = (
    "Heads-up from the ops review: AR Rendering needs 7109.220038 MB of RAM "
    "at minimum, and C2 needs a minimum of 4.729380463 GB."
)


def main() -> None:
    if not INTENT.strip():
        raise SystemExit("Remplis la constante INTENT avant de lancer ce script.")

    scenario = load_scenario(SCENARIO_ID)
    nodes = load_nodes()
    links = load_links()
    services = load_services(scenario)
    flows = load_flows(scenario)

    graph = build_graph()
    result = graph.invoke(
        {
            "intent_text": INTENT,
            "services": services,
            "flows": flows,
            "nodes": nodes,
            "links": links,
            "requirements": [],
            "necessity_result": None,
            "decision_result": None,
            "explanation": None,
        }
    )

    print("=== Scenario ground truth ===")
    print(f"  id={scenario['id']}  category={scenario['category']}  decision={scenario['decision']}")
    print(f"  moved_components={scenario['moved_components']}  p1={scenario['p1']}")
    print(f"  requirements={scenario['requirements']}")

    print("\n=== Extracted requirements ===")
    for req in result["requirements"]:
        print(
            f"  {req.requirement_id}: {req.target_type}:{req.target_id} "
            f"{req.kpi_type} {req.comparator} {req.target_value}{req.unit} "
            f'(source: "{req.source_span}")'
        )

    print("\n=== Necessity Checker ===")
    print(result["necessity_result"].model_dump_json(indent=2))

    if result["decision_result"]:
        print("\n=== Decision Engine ===")
        print(result["decision_result"].model_dump_json(indent=2))

    print("\n=== Explanation ===")
    print(result["explanation"])


if __name__ == "__main__":
    main()
