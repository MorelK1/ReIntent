"""Runs ONE eval/experiment_500 scenario through a single-LLM baseline
(either eval/baselines/"direct llm"/ or eval/baselines/cot/, picked via
--baseline) -- the LLM does extraction + decision + placement +
explanation (and, for cot, reasoning) in ONE call, unlike the real
pipeline's four-node graph (see eval/run_sample_full_graph.py).

Uses the SAME system model loader as the real pipeline
(eval/system_model/loader.py) -- no separate baseline-specific loader, the
infrastructure/services/flows data is identical either way, only the
prompt/decision mechanism differs.

Loads the baseline's prompt.py/schema.py via importlib rather than a plain
sys.path + import: "direct llm" (its own directory name) contains a space,
and both sibling directories define modules with the SAME names (prompt,
schema) -- a plain sys.path trick risks one baseline's modules shadowing
the other's if a future batch wrapper ever needs both loaded in the same
process.

Usage (from the project root):
    PYTHONPATH=src venv/bin/python3 "eval/baselines/run_sample.py" ARVR-000001 \\
        --baseline direct [--model MODEL] [--prompt-strategy zero_shot]
"""

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "system_model"))

from loader import load_flows, load_intent, load_links, load_nodes, load_scenario, load_services  # noqa: E402
from runner import run_baseline  # noqa: E402

_BASELINE_DIRS = {"direct": "direct llm", "cot": "cot"}
_SCHEMA_NAMES = {"direct": "BaselineResult", "cot": "CoTBaselineResult"}


def _load_baseline_module(name: str, baseline: str) -> types.ModuleType:
    directory = Path(__file__).resolve().parent / _BASELINE_DIRS[baseline]
    spec = importlib.util.spec_from_file_location(f"{baseline}_{name}", directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_record(scenario: dict, outcome: dict, run_config: dict) -> dict:
    """Captures the model's result AS RETURNED, whatever shape its schema
    happens to be (direct llm/'s decision+placement vs cot/'s reasoning+
    requirements+outcome+new_placement) -- no field picked out by name, no
    verification run here. Same "capture raw, score later" split as
    eval/run_sample_full_graph.py and run_reference_decision.py: a scoring
    pass reads ground_truth + result from the record and decides what to
    check, instead of this function assuming one fixed schema shape."""
    result = outcome["result"]
    return {
        "run_config": run_config,
        "scenario_id": scenario["id"],
        "ground_truth": {
            "requirements": scenario["requirements"],
            "decision": scenario["decision"],
            "moved_components": scenario["moved_components"],
            "p1": scenario["p1"],
        },
        "model": outcome["model"],
        "elapsed_s": outcome["elapsed_s"],
        "token_usage": outcome["token_usage"],
        "parsing_error": outcome["parsing_error"],
        # The model's response text EXACTLY as received -- before the
        # fence-strip/JSON-parse/_normalize() repairs "result" went
        # through (see runner.py's own docstring). Kept so a repair (e.g.
        # ">=" -> "gte") never silently erases what the model actually wrote.
        "raw_response": outcome["raw_response"],
        "result": result.model_dump() if result is not None else None,
    }


def run(
    scenario_id: str,
    baseline: str,
    model: str | None = None,
    prompt_strategy: str | None = None,
    verbose: bool = True,
) -> dict:
    prompt_module = _load_baseline_module("prompt", baseline)
    schema_module = _load_baseline_module("schema", baseline)
    schema_cls = getattr(schema_module, _SCHEMA_NAMES[baseline])

    scenario = load_scenario(scenario_id)
    intent_entry = load_intent(scenario_id)

    nodes = load_nodes()
    links = load_links()
    services = load_services(scenario)
    flows = load_flows(scenario)

    outcome = run_baseline(
        intent_entry["intent"], services, flows, nodes, links,
        prompt_module, schema_cls, model=model, prompt_strategy=prompt_strategy,
    )
    run_config = {"baseline": baseline, "model": model, "prompt_strategy": prompt_strategy}
    record = build_record(scenario, outcome, run_config)

    if verbose:
        print(f"=== {scenario_id} [{baseline}] ({record['elapsed_s']:.1f}s) ===")
        print(f"intent: {intent_entry['intent']}")
        print(f"ground truth: decision={scenario['decision']} moved={scenario['moved_components']} "
              f"p1={scenario['p1']}")
        if record["result"] is None:
            print(f"  PARSING FAILED -- {record['parsing_error']}")
        else:
            print(f"result: {record['result']}")
        print(f"model={record['model']} elapsed={record['elapsed_s']:.2f}s tokens={record['token_usage']}")

    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario_id", nargs="?", default="ARVR-000001")
    parser.add_argument("--baseline", choices=["direct", "cot"], required=True)
    parser.add_argument("--model", default=None, help="Overrides OPENROUTER_MODEL")
    parser.add_argument("--prompt-strategy", default=None, choices=["zero_shot", "one_shot", "few_shot"])
    args = parser.parse_args()
    record = run(
        args.scenario_id, args.baseline, model=args.model,
        prompt_strategy=args.prompt_strategy, verbose=True,
    )
    print("\n=== full record (JSON) ===")
    print(json.dumps(record, indent=2, ensure_ascii=False, default=str))
