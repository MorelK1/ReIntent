"""Re-runs ONLY the scenarios that ended up as an error placeholder
({"error": "see console log above"}) in a given eval/results/*.jsonl file
from the 2026-11-03 batch campaign, and rewrites that same file in place --
every scenario_id keeps its original position, successes get a real record,
and anything that still fails keeps an error placeholder (with the fresh
exception message this time, not a stale "see console log above").

Exists because run_batch_full_graph.py only knows how to run the first N
scenarios of benchmark -- there's no built-in way to target an explicit,
sparse set of scenario_ids, which is what re-running just the failures needs.

Reuses run_with_retry from run_batch_full_graph.py (same retry/backoff
behavior for transient OpenRouter errors), so this isn't a second
implementation of that logic.

Usage (from the project root):
    PYTHONPATH=src venv/bin/python3 eval/rerun_failed_full_graph.py \\
        eval/results/openai-gpt-oss-120b__few_shot__20261103T220001Z.jsonl \\
        --model openai/gpt-oss-120b --prompt-strategy few_shot [--delay SECONDS]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "system_model"))

from agent.graph import build_graph  # noqa: E402
from run_batch_full_graph import DEFAULT_DELAY_S, run_with_retry  # noqa: E402

DEFAULT_EXPLANATION_MODEL = None


def main(
    path: Path, model: str | None, prompt_strategy: str, explanation_model: str | None, delay_s: float,
    skip_necessity_checker: bool = False,
) -> None:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    failed_ids = [r["scenario_id"] for r in records if "error" in r]
    if not failed_ids:
        print(f"{path}: no error records found, nothing to do.")
        return

    print(f"{path}: {len(failed_ids)} failed scenario(s) to retry -- {failed_ids}")
    graph = build_graph(
        model=model, prompt_strategy=prompt_strategy, explanation_model=explanation_model,
        skip_necessity_checker=skip_necessity_checker,
    )

    by_id = {r["scenario_id"]: r for r in records}
    n_fixed = 0
    n_still_failing = 0
    for i, scenario_id in enumerate(failed_ids, start=1):
        record = run_with_retry(
            scenario_id, graph,
            model=model, prompt_strategy=prompt_strategy, explanation_model=explanation_model,
            skip_necessity_checker=skip_necessity_checker,
        )
        if record is None:
            print(f"[{i}/{len(failed_ids)}] {scenario_id}: still failing, keeping error placeholder")
            n_still_failing += 1
        else:
            by_id[scenario_id] = record
            n_fixed += 1
            print(f"[{i}/{len(failed_ids)}] {scenario_id}: fixed")
        if i < len(failed_ids) and delay_s > 0:
            time.sleep(delay_s)

    # Rewrite in place, same scenario_id order as before -- atomically, via a
    # temp file, so a crash mid-write never leaves a half-written results file.
    tmp_path = path.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w") as out:
        for r in records:
            out.write(json.dumps(by_id[r["scenario_id"]], ensure_ascii=False) + "\n")
    tmp_path.replace(path)

    print(f"=== {path.name}: {n_fixed} fixed, {n_still_failing} still failing ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl_path", type=Path)
    parser.add_argument("--model", default=None)
    parser.add_argument("--prompt-strategy", default="zero_shot", choices=["zero_shot", "one_shot", "few_shot"])
    parser.add_argument("--explanation-model", default=DEFAULT_EXPLANATION_MODEL)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S)
    parser.add_argument(
        "--skip-necessity-checker", action="store_true",
        help="Ablation: builds the graph WITHOUT necessity_checker, matching the file being "
             "repaired (see run_batch_full_graph.py's own flag of the same name)",
    )
    args = parser.parse_args()
    main(args.jsonl_path, args.model, args.prompt_strategy, args.explanation_model, args.delay,
         skip_necessity_checker=args.skip_necessity_checker)
