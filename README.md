# ReIntent

ReIntent: Intent-Driven Reconfiguration for Evolving QoS Requirements in the Cloud-Edge Continuum

Anonymous artefact for double-blind review.

Given a natural-language operator intent (e.g. "object recognition now needs
4.5 GB of memory"), the pipeline extracts its structured requirements,
checks whether the current placement of services across nodes still
satisfies them, and — only if not — searches for a new placement that does,
using an exact CP-SAT solver (provably optimal, or a minimal set of
conflicting constraints if no placement exists). A final step turns the
decision into a short natural-language explanation.

The pipeline is built as a **LangGraph** graph with four stages: intent
grounding (requirement extraction) → necessity checker → decision engine →
explanation.

## Contents

| Folder | What it contains |
|---|---|
| `src/agent/` | The pipeline itself: intent grounding (`nodes/intent_grounding.py`), necessity checker (`nodes/necessity_checker.py`), decision engine (`nodes/decision_engine_cp_sat.py`, CP-SAT — provably optimal placement, or a minimal unsatisfiable subset via `utils/infeasibility_diagnosis.py` when no placement exists), and explanation generation (`nodes/explanation.py`). Assembled as a LangGraph graph in `graph.py`. |
| `eval/system_model/` | The system model (nodes, links, services, flows — `loader.py` adapts them onto the schemas in `src/agent/model/schemas.py`) shared by every script below. |
| `eval/benchmark/` | The 500-scenario benchmark (`scenarios.json`): each scenario pairs a natural-language-intent-worthy requirement change with its ground-truth decision, placement, and minimal number of migrations. |
| `eval/benchmark_generation/` | The generator that produces `eval/benchmark/` and `eval/system_model/` (`build_benchmark_normalized.py` + `benchmark_model_normalized.py`, `-n 500 --stay-ratio 0.38 --offload-ratio 0.49 --infeasible-ratio 0.13 --seed 42` reproduces `eval/benchmark/scenarios.json` exactly), its raw infrastructure/placement inputs, and `check_normalized_system.py` for validating the system model it builds on. |
| `intent/` | The natural-language intent written for each of the 500 scenarios (`intents_v3.json`), the generation methodology (`intent_generation_report_v3.md`, and the fuller `methodologie_generation_intents.pdf`/`.tex`) and validation reports, and the validator itself (`intent_store.py`). `sota/` keeps background reading on data-to-text generation, unrelated to the pipeline's code. |
| `eval/baselines/` | The two LLM-only baselines compared against the pipeline — Direct (one-shot decision) and Chain-of-Thought — their prompts (`direct llm/`, `cot/`), batch runner, and independent verifier (`verifier.py`, reuses the pipeline's own feasibility checks, never trusts the LLM's self-reported placement). |
| `eval/run_batch_full_graph.py`, `run_sample_full_graph.py` | Run the full pipeline over a batch (or one) scenario(s), model, and prompting strategy; `--skip-necessity-checker` runs the ablation (ties directly into the same runner, lands in `eval/results_ablation/`). |
| `eval/run_reference_decision.py` | Computes the deterministic optimum for every scenario — necessity checker + decision engine run directly on the scenario's ground-truth requirements, no LLM anywhere — used as the reference every approach's migration count is compared against. |
| `eval/rerun_failed_full_graph.py`, `eval/baselines/rerun_failed.py` | Re-run only the scenarios that failed (rate-limit, transient API error) in a given results file, in place. |
| `eval/run_scenario_intent.py` | A single manual sanity check of one real intent against one scenario. |
| `eval/results/`, `eval/results_ablation/`, `eval/baselines/results/` | Raw per-scenario records — full pipeline, pipeline without the necessity checker, and the two baselines — for every one of 5 models × 3 prompting strategies (15,000 records each pair of folders). |
| `eval/results_reference/` | The deterministic reference decision for all 500 scenarios (see `run_reference_decision.py` above). |
| `eval/scoring.ipynb` | The scoring notebook, **executed**: extraction performance (precision/recall/F1/exact-match, recall by requirement explicitness) and end-to-end reconfiguration performance (decision accuracy, real feasibility, optimality gap, reconfiguration cost, latency, token cost), for every model × strategy × approach — the code that produces every table and figure in `eval/eval_outputs/`, not a one-off script. |
| `eval/eval_outputs/` | The tables (CSV/JSON/MD) and figures (PNG) produced by `scoring.ipynb`. |
| `tests/` | Unit tests for the pipeline's structured nodes (necessity checker, decision engine, infeasibility diagnosis, requirement validation) and the explanation prompt — fixtures are built inline, no external data. |

## Setup

```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in OPENROUTER_API_KEY at minimum
```

## How to run

```bash
# tests (no network)
PYTHONPATH=src venv/bin/python3 -m pytest tests -q

# the full pipeline on N scenarios of the benchmark
PYTHONPATH=src venv/bin/python3 eval/run_batch_full_graph.py N \
    --model <openrouter-model> --prompt-strategy {zero_shot,one_shot,few_shot}

# the same, without the necessity checker (ablation)
PYTHONPATH=src venv/bin/python3 eval/run_batch_full_graph.py N \
    --model <openrouter-model> --prompt-strategy few_shot --skip-necessity-checker

# a baseline on the same scenarios/models
PYTHONPATH=src venv/bin/python3 "eval/baselines/run_batch.py" --baseline {direct,cot} N \
    --model <openrouter-model> --prompt-strategy {zero_shot,one_shot,few_shot}

# the deterministic reference optimum (no LLM, runs in a few seconds)
PYTHONPATH=src venv/bin/python3 eval/run_reference_decision.py

# regenerate every table/figure in eval/eval_outputs/ from the raw results above
cd eval && jupyter nbconvert --to notebook --execute --inplace scoring.ipynb

# regenerate eval/benchmark/scenarios.json from scratch (reproduces it exactly)
cd eval/benchmark_generation && python3 build_benchmark_normalized.py \
    -n 500 --stay-ratio 0.38 --offload-ratio 0.49 --infeasible-ratio 0.13 --seed 42 \
    --output-dir /tmp/benchmark_check
```

## Not included, and why

- **`.env`** — holds `OPENROUTER_API_KEY`; never committed. Copy `.env.example` and fill it in (see Setup above).

## Data sources

`eval/system_model/nodes.csv` and `links.csv` are adapted from an ECSP-Sim-derived reference infrastructure (see `eval/system_model/loader.py`'s own notes on the adaptation).
