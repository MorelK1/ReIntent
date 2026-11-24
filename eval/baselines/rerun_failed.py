"""Re-runs ONLY the scenarios that ended up as an error placeholder
({"error": "see console log above"}) in a given eval/baselines/results*/*.jsonl
file, and rewrites that same file in place -- every scenario_id keeps its
original position, successes get a real record, and anything that still
fails keeps an error placeholder (with the fresh exception message this
time, not a stale "see console log above").

Mirrors eval/rerun_failed_full_graph.py, but for a single-LLM baseline run
(direct/cot) instead of the full LangGraph pipeline -- reuses run_with_retry
from run_batch.py (same retry/backoff behavior for transient OpenRouter
errors), so this isn't a second implementation of that logic.

The baseline/model/prompt_strategy to retry with are read from the first
record's own "run_config" field, so this doesn't need them passed again on
the command line.

Usage (from the project root):
    PYTHONPATH=src venv/bin/python3 eval/baselines/rerun_failed.py \\
        eval/baselines/results_v3/cot__google-gemini-2.5-flash-lite__few_shot__20261123T211335Z.jsonl \\
        [--delay SECONDS]
"""

import argparse
import json
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_batch import DEFAULT_DELAY_S, run_with_retry  # noqa: E402


def main(path: Path, delay_s: float) -> None:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    failed = [r for r in records if "error" in r]
    if not failed:
        print(f"{path}: no error records found, nothing to do.")
        return

    run_config = failed[0]["run_config"]
    baseline = run_config["baseline"]
    model = run_config["model"]
    prompt_strategy = run_config["prompt_strategy"]
    failed_ids = [r["scenario_id"] for r in failed]
    print(f"{path}: {len(failed_ids)} failed scenario(s) to retry -- {failed_ids}")
    print(f"baseline={baseline} model={model} prompt_strategy={prompt_strategy}")

    by_id = {r["scenario_id"]: r for r in records}
    n_fixed = 0
    n_still_failing = 0
    for i, scenario_id in enumerate(failed_ids, start=1):
        record = run_with_retry(scenario_id, baseline, model=model, prompt_strategy=prompt_strategy)
        if record is None:
            print(f"[{i}/{len(failed_ids)}] {scenario_id}: still failing, keeping error placeholder")
            n_still_failing += 1
        else:
            by_id[scenario_id] = record
            n_fixed += 1
            print(f"[{i}/{len(failed_ids)}] {scenario_id}: fixed")
        if i < len(failed_ids) and delay_s > 0:
            time.sleep(delay_s)

    tmp_path = path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w") as out:
        for r in records:
            out.write(json.dumps(by_id[r["scenario_id"]], ensure_ascii=False, default=str) + "\n")
    tmp_path.replace(path)

    print(f"=== {path.name}: {n_fixed} fixed, {n_still_failing} still failing ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl_path", type=Path)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S)
    args = parser.parse_args()
    main(args.jsonl_path, args.delay)
