"""Loader: eval/system_model's ARVR benchmark (scenario-driven) onto our real
schemas (Node, Link, Service, Flow) -- field renaming/adaptation only, no
engine logic duplicated here. necessity_checker_node/decision_engine_node/
intent_grounding_node run UNMODIFIED on the result, same pattern as
expérimentations/public_safety/loader.py.

`fixed_placement` (C1/C5 in services.json) is passed through to Service and
enforced by both solvers (see utils/csp_solver.py:solve_placement and
utils/csp_constraints_cp_sat.py:add_fixed_placement_constraints_cp_sat) --
neither will ever move C1 or C5.

Known, NOT-yet-handled gap: `background_loads.csv` is handled by
SUBTRACTING each node's background cpu/ram from its capacity at load time
(the simplest faithful option -- no phantom services invented), not by the
richer "background service" modeling that would be needed for
background_mips to matter too.

Which campaign's data this reads is controlled by 3 independent env vars
(EVAL_SYSTEM_MODEL_DIR / EVAL_EXPERIMENT_DIR / EVAL_INTENT_FILE, see their
definitions just below) -- unset, this is exactly today's experiment_500
behavior. Set all three to run against the v3 benchmark instead:
    EVAL_SYSTEM_MODEL_DIR=eval/system_model_normalized
    EVAL_EXPERIMENT_DIR=eval/benchmark_v3
    EVAL_INTENT_FILE=intent_v3/intents_v3.json
"""

import csv
import json
import math
import os
from pathlib import Path

from agent.model.schemas import Flow, Link, Node, Service

_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parents[1]

# Three independent axes, each overridable on its own so a campaign can mix
# and match (e.g. the normalized system model against its own benchmark_v3
# scenarios, or any other combination) -- same env-var-override pattern as
# intent_v3/intent_store.py's INTENT_SET/INTENT_SCENARIOS. Unset, every
# default below reproduces today's experiment_500 behavior exactly.
#
# EVAL_SYSTEM_MODEL_DIR: nodes/links/services/flows source (this directory
#   by default; eval/system_model_normalized for the 2-decimal-rounded
#   variant).
# EVAL_EXPERIMENT_DIR: scenarios.json + summary.json (baseline_p0) source
#   (eval/experiment_500 by default; eval/benchmark_v3 for the v3 campaign
#   -- same scenario schema, independently regenerated against the
#   normalized system model, see eval/benchmark_v3/summary.json).
# EVAL_INTENT_FILE: the natural-language intents file load_intent() reads
#   (intent/intents_500.json by default; intent_v3/intents_v3.json for v3
#   -- a different filename AND a different top-level directory, so this
#   is a full path, not just a directory to join a fixed name onto).
DATA_DIR = Path(os.environ.get("EVAL_SYSTEM_MODEL_DIR", str(_HERE)))
# experiment_500 supersedes experiment_150 as of 2026-11-03 -- same system
# model, same scenario schema, a larger independently-drawn stratified
# sample (500 vs 150). NOT a superset: a given scenario id (e.g.
# "ARVR-000001") resolves to a DIFFERENT generated scenario in each set --
# true of benchmark_v3 too, against the SAME scenario_id space.
EXPERIMENT_DIR = Path(os.environ.get("EVAL_EXPERIMENT_DIR", str(_HERE.parent / "experiment_500")))
INTENT_FILE = Path(os.environ.get("EVAL_INTENT_FILE", str(_REPO_ROOT / "intent" / "intents_500.json")))

# Scenario requirements use "e2e_latency" for what our schema calls "latency"
# -- cpu/ram already match our own kpi_type spelling, so only this one entry
# is needed. See eval/CONSIGNES_GENERATION_INTENTS_FR.md section 3.1.
_SCENARIO_KPI_TO_NODE_FIELD = {"cpu": "cpu_cores", "ram": "ram_gb"}

# README.json's placement_eligibility: "C1 fixed on N12 and C5 on N8; C2-C4
# may be placed on compute-capable access points N1,N2, edge N3-N5 or cloud
# N6,N7 ... Other IoT endpoints N9-N15 are not general offloading hosts."
# Excluded from `nodes` entirely (not placement candidates for ANY service)
# -- but N8/N12 are deliberately KEPT, since C5/C1's fixed_placement needs
# them to exist as real candidate nodes (both solvers would otherwise have
# no node to pin them to). Filtering happens here, not in nodes.csv itself,
# so the source file stays the untouched ECSP-Sim-derived reference; the
# excluded nodes remain in links.csv and can still serve as network relays
# for multi-hop routing (shortest_path_delays_ms reads links independently
# of `nodes`), just never as compute hosts.
_EXCLUDED_HOST_NODES = {"N9", "N10", "N11", "N13", "N14", "N15"}


def _read_csv(name: str) -> list[dict]:
    with open(DATA_DIR / name) as f:
        return list(csv.DictReader(f))


def load_nodes() -> dict[str, Node]:
    background = {row["node_id"]: row for row in _read_csv("background_loads.csv")}
    nodes: dict[str, Node] = {}
    for row in _read_csv("nodes.csv"):
        if row["node_id"] in _EXCLUDED_HOST_NODES:
            continue
        bg = background.get(row["node_id"])
        bg_cpu = float(bg["background_cpu_cores"]) if bg else 0.0
        bg_ram = float(bg["background_ram_gb"]) if bg else 0.0
        # floor(), not round() -- same rationale as public_safety/loader.py:
        # rounding up could make a node look like it has room it doesn't.
        nodes[row["node_id"]] = Node(
            node_id=row["node_id"],
            node_name=row["node_id"],  # source has no node_name column
            tier=row["tier"],
            node_type=row["node_type"],
            cpu_cores=math.floor(float(row["cpu_cores"]) - bg_cpu),
            ram_gb=math.floor(float(row["ram_gb"]) - bg_ram),
            storage_gb=math.floor(float(row["storage_gb"])),
            mips=float(row["mips"]),
        )
    return nodes


def load_links() -> list[Link]:
    return [
        Link(
            # Source has no link_id/link_type columns -- synthesized.
            link_id=f"L-{row['source_node']}-{row['target_node']}",
            source_node=row["source_node"],
            target_node=row["target_node"],
            link_type="direct",
            bandwidth_mbps=round(float(row["bandwidth_mbps"])),
            propagation_delay_ms=float(row["propagation_delay_ms"]),
            # Unknown in source (empty CSV values) -- 0.0/1.0 are also
            # currently unused by any check in pipeline.py.
            packet_loss_rate=0.0,
            reliability=1.0,
        )
        for row in _read_csv("links.csv")
    ]


def load_scenario(scenario_id: str) -> dict:
    with open(EXPERIMENT_DIR / "scenarios.json") as f:
        scenarios = json.load(f)
    return next(s for s in scenarios if s["id"] == scenario_id)


def load_intent(scenario_id: str) -> dict:
    """The INTENT_FILE entry for scenario_id -- "intent" is the
    natural-language text to feed intent_grounding_node; the rest (plan,
    mentions, n_words, review_status) is generation metadata, never fed to
    the model itself (see intent/intent_generation_report_500.md /
    intent_v3/intent_generation_report_v3.md)."""
    with open(INTENT_FILE) as f:
        intents = json.load(f)
    return next(i for i in intents if i["scenario_id"] == scenario_id)


def _baseline_placement() -> dict[str, str]:
    """P0 (the eval's deliberately-diverse starting placement), not
    services.json's own current_node -- see eval/system_model/README.json's
    "initial_placement" note."""
    with open(EXPERIMENT_DIR / "summary.json") as f:
        p0 = json.load(f)["baseline_p0"]
    return {service_id: f"N{node_num}" for service_id, node_num in p0.items()}


def _service_requirement_overrides(service_id: str, scenario: dict | None) -> dict[str, float]:
    """A scenario's own cpu/ram requirement entries carry a baseline_value --
    what the component's footprint was BEFORE this scenario's new intent.
    Overrides services.json's static requirements for exactly the (service,
    kpi) pairs this scenario's requirements array names; everything else
    keeps its static baseline."""
    overrides: dict[str, float] = {}
    for r in (scenario or {}).get("requirements", []):
        if r["target_type"] == "service" and r["target_id"] == service_id:
            field = _SCENARIO_KPI_TO_NODE_FIELD.get(r["kpi_type"])
            if field:
                overrides[field] = r["baseline_value"]
    return overrides


def load_services(scenario: dict | None = None) -> dict[str, Service]:
    with open(DATA_DIR / "services.json") as f:
        raw = json.load(f)["application_context"]["services"]
    placement = _baseline_placement()
    services: dict[str, Service] = {}
    for s in raw:
        requirements = {"cpu_cores": s["requirements"]["cpu_cores"], "ram_gb": s["requirements"]["ram_gb"]}
        requirements.update(_service_requirement_overrides(s["service_id"], scenario))
        services[s["service_id"]] = Service(
            service_id=s["service_id"],
            name=s["name"],
            description=s["description"],
            current_node=placement.get(s["service_id"], s["current_node"]),
            requirements=requirements,
            fixed_placement=s.get("fixed_placement", False),
            workload_mi=s["workload_mi"],
            output_size=s["output_size"],
        )
    return services


def _flow_latency_baseline(flow_id: str, static_latency: float | None, scenario: dict | None) -> float | None:
    for r in (scenario or {}).get("requirements", []):
        if r["target_type"] == "flow" and r["target_id"] == flow_id:
            return r["baseline_value"]
    return static_latency


def load_flows(scenario: dict | None = None) -> dict[str, Flow]:
    with open(DATA_DIR / "flows.json") as f:
        raw = json.load(f)["application_context"]["flows"]
    flows: dict[str, Flow] = {}
    for f in raw:
        static_latency = (f.get("requirements") or {}).get("latency")
        flows[f["flow_id"]] = Flow(
            flow_id=f["flow_id"],
            name=f["name"],
            description=f["description"],
            path=f["path"],
            requirements={"latency": _flow_latency_baseline(f["flow_id"], static_latency, scenario)},
        )
    return flows
