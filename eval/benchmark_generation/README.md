# AR/VR Benchmark generator — normalized system model

Generates `eval/benchmark/scenarios.json` and the data behind `eval/system_model/` from a raw infrastructure/placement description and a normalized (half-vCPU, half-GB, whole-ms) system model.

## Reference configuration

Application 1, components and versions kept from the GA placement:

| Component | Version | Node | Tier |
|---|---|---|---|
| C1 Vision Capture | V1 | N12 | User/IoT |
| C2 Object Recognition | V3 | N4 | Edge |
| C3 Scene Processing | V2 | N6 | Cloud |
| C4 AR Rendering | V5 | N4 | Edge |
| C5 Display Output | V1 | N8 | Helper/IoT |

Applications 2-4 keep their GA versions and placements, and make up the background load. CPU (MIPS), vCPU, RAM, and storage capacities checked for all applications together.

**Reference SLOs:** f1 = 500 ms, f2 = 500 ms (`baseline_budget` in `benchmark_model_normalized.py`).

## Files

- `services.json`, `nodes.json`, `users.json`, `helpers.json`, `infraConnections.json`, `componentsConnections.json`, `placement_P0_app1_V1.json` — the raw simulator inputs: topology, application dependencies, component versions, packet sizes, and the initial placement. Kept only as the source of this non-numeric information.
- `system_model_normalized/` — the canonical, already-normalized system model (`nodes.csv`, `links.csv`, `services.json`, `flows.json`, `background_loads.csv`, matching `eval/system_model/`). `benchmark_model_normalized.py` overrides every numeric quantity (vCPU, RAM, MIPS, bandwidth, propagation delay, workload) from these files, never from the raw simulator JSONs above.
- `benchmark_model_normalized.py` — on import, cross-checks the raw simulator inputs against `system_model_normalized/` (placement, resource values, flow latencies, link bandwidth/delay, background load — raises on any mismatch), then builds the candidate-placement space, shortest-path latencies, and the `classify()` function that turns a set of requirement changes into a STAY/OFFLOAD/INFEASIBLE decision with the minimal migration set.
- `build_benchmark_normalized.py` — the generator: samples a requirement change (one of 7 categories: single/cross/multi-service, single/both-flow, service+flow, multi-service+both-flows), rounds it to the normalized step (0.5 vCPU, 0.5 GB, 1 ms), classifies it with `benchmark_model_normalized.classify()`, and keeps sampling until the requested STAY/OFFLOAD/INFEASIBLE mix is reached.
- `check_normalized_system.py` — standalone entry point for the same validation `benchmark_model_normalized.py` already runs on import.

## Reproducing the generation

Run from this folder:

```bash
python3 build_benchmark_normalized.py -n 500 \
    --stay-ratio 0.38 --offload-ratio 0.49 --infeasible-ratio 0.13 --seed 42 \
    --output-dir <some-dir>
```

Verified result: STAY 190, OFFLOAD 245, INFEASIBLE 65 (726 candidate attempts), reproducing `eval/benchmark/scenarios.json` and `eval/system_model/` byte-for-byte. The mix is fixed *after classification*, not drawn from frequencies observed in production.

## Conventions and limitations

- Original RAM and storage are in MB; the normalized system model exports GB = MB/1000; the model itself works in MB internally.
- Data sizes are in **Mb**, bandwidth in **Mbps**. Source RTT is in seconds; one-way propagation delay is RTT/2.
- Shortest-path communication delay is weighted by `dataSize_Mb / bandwidth_Mbps + RTT_seconds / 2` for each hop. Processing time = `cpuWorkloadMI / node_cpuMIPS`.
- Changes to CPU/RAM requirements alter the reserved demand, **not** the processing workload (MI), the component version, or the topology.
- The initial placement is **built under an Edge-Cloud diversity constraint**, not a direct optimal output of the GA.
- The model does not simulate congestion or queuing; results do not demonstrate the real-world performance of an actual deployment.
- Budget choices and generation ranges are adaptations of the ECSP-Sim context for this benchmark.
