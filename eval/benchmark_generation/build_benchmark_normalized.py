#!/usr/bin/env python3
"""Unified reproducible AR/VR scenario generator.

One JSON and one CSV, with a configurable scenario count and outcome mix.
Outcome ratios are intentionally stratified, not representative prevalence.
"""
import argparse, collections, csv, json, math, random
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import benchmark_model_normalized as model

ROOT = Path(__file__).resolve().parent
CATEGORIES = ('single_service','cross_service','multiservice','single_flow','both_flows','service_plus_flow','multi_service_both_flows')
WEIGHTS = (2, 2, 1, 2, 1, 3, 2)

def counts_for(n, ratios):
    exact = {k:n*v/sum(ratios.values()) for k,v in ratios.items()}
    counts = {k:int(math.floor(v)) for k,v in exact.items()}
    for k in sorted(counts, key=lambda k:(-(exact[k]-counts[k]),k))[:n-sum(counts.values())]:
        counts[k]+=1
    return counts

# Public-facing numeric granularity. Internal memory units are MB (1000 MB = 1 GB).
CPU_STEP_VCPU = Decimal('0.5')
RAM_STEP_GB = Decimal('0.5')
LATENCY_STEP_MS = Decimal('1')


def quantize_to_step(value, step):
    """Round to the nearest multiple of step, with half-up ties."""
    value, step = Decimal(str(value)), Decimal(str(step))
    return (value / step).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * step


def changed_rounded_value(baseline, proposed, step):
    """Round the new target and retain the intended direction of change."""
    baseline_d, proposed_d, step_d = map(lambda x: Decimal(str(x)),
                                         (baseline, proposed, step))
    result = quantize_to_step(proposed_d, step_d)
    if proposed_d > baseline_d and result <= baseline_d:
        result = (baseline_d // step_d + 1) * step_d
    elif proposed_d < baseline_d and result >= baseline_d:
        result = ((baseline_d / step_d).to_integral_value(rounding='ROUND_CEILING') - 1) * step_d
    return float(result)


def req_change(cid, res, factor):
    key = {'CPU': 'cpuVcpu', 'RAM': 'memory'}[res]
    baseline = model.base[cid][key]
    if res == 'CPU':
        new_value = changed_rounded_value(baseline, baseline * factor, CPU_STEP_VCPU)
    else:
        # Quantize in GB; convert back to MB for model.resource_feasible().
        new_value_gb = changed_rounded_value(baseline / 1000.0,
                                              baseline * factor / 1000.0,
                                              RAM_STEP_GB)
        new_value = new_value_gb * 1000.0
    return {'target_type': 'service', 'target_id': f'C{cid}', 'component_id': cid,
            'component_name': model.names[cid], 'kpi_type': res.lower(),
            'resource': key, 'comparator': 'gte',
            'baseline_value': baseline, 'new_value': new_value,
            'unit': 'vCPU' if res == 'CPU' else 'MB',
            'factor': round(new_value / baseline, 8)}

def sample(rng):
    category=rng.choices(CATEGORIES,weights=WEIGHTS,k=1)[0]
    intensity=rng.random()
    if intensity<.47: rlo,rhi=1.02,1.55; llo,lhi=.91,1.06; level='mild'
    elif intensity<.86: rlo,rhi=1.35,2.85; llo,lhi=.76,1.01; level='moderate'
    else: rlo,rhi=2.1,4.5; llo,lhi=.58,.92; level='strong'
    rf=lambda:rng.uniform(rlo,rhi)
    lf=lambda:rng.uniform(llo,lhi)
    reqs=[]; factors={}
    if category=='single_service':
        reqs=[req_change(rng.choice(model.mobile),rng.choice(('CPU','RAM')),round(rf(),5))]
    elif category=='cross_service':
        a,b=rng.sample(model.mobile,2)
        t=rng.choice((('CPU','RAM'),('CPU','CPU'),('RAM','RAM')))
        reqs=[req_change(a,t[0],round(rf(),5)),req_change(b,t[1],round(rf(),5))]
    elif category=='multiservice':
        reqs=[req_change(c,rng.choice(('CPU','RAM')),round(rf(),5)) for c in model.mobile]
    elif category=='single_flow':
        factors={rng.choice(tuple(model.flows)):lf()}
    elif category=='both_flows':
        factors={f:lf() for f in model.flows}
    elif category=='service_plus_flow':
        reqs=[req_change(rng.choice(model.mobile),rng.choice(('CPU','RAM')),round(rf(),5))]
        factors={rng.choice(tuple(model.flows)):lf()}
    else:
        a,b=rng.sample(model.mobile,2)
        reqs=[req_change(a,'CPU',round(rf(),5)),req_change(b,'RAM',round(rf(),5))]
        factors={f:lf() for f in model.flows}
    # Latency budgets are integer milliseconds (or multiples of LATENCY_STEP_MS).
    budgets = {f: changed_rounded_value(model.baseline_budget[f],
                                         model.baseline_budget[f] * v,
                                         LATENCY_STEP_MS)
               for f, v in factors.items()}
    budgets = {f: int(v) if LATENCY_STEP_MS == 1 else v for f, v in budgets.items()}
    flows = [{'target_type': 'flow', 'target_id': f,
              'flow_name': model.flows[f]['name'], 'path': model.flows[f]['path'],
              'kpi_type': 'e2e_latency', 'comparator': 'lte',
              'baseline_value': model.baseline_budget[f], 'new_value': budgets[f],
              'unit': 'ms',
              'budget_factor': round(budgets[f] / model.baseline_budget[f], 8)}
             for f in factors]
    # Keep memory in MB internally for resource-feasibility calculations,
    # but export every RAM requirement in GB, matching system_model/services.json.
    exported_reqs = []
    for req in reqs:
        r = dict(req)
        if r['kpi_type'] == 'ram':
            r['baseline_value'] = round(r['baseline_value'] / 1000.0, 9)
            r['new_value'] = round(r['new_value'] / 1000.0, 9)
            r['unit'] = 'GB'
        exported_reqs.append(r)
    return {'category':category,'level':level,'origin':'synthetic_outcome_stratified',
            'requirements':exported_reqs+flows,'flow_budgets_ms':dict(model.baseline_budget,**budgets)},reqs,budgets

def signature(item):
    # Independent of id/order and of factor fields: same requirements => same scenario.
    requirements=sorted(((r['target_type'],r['target_id'],r['kpi_type'],round(r['new_value'],5)) for r in item['requirements']))
    return tuple(requirements)

def write_csv(path,rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f)
        writer.writerow(['id','category','level','origin','requirements_json','decision','feasible_placements','minimum_migrations','moved_components','p1_json','p1_latency_ms_json'])
        for s in rows:
            writer.writerow([s['id'],s['category'],s['level'],s['origin'],json.dumps(s['requirements'],ensure_ascii=False),s['decision'],s['feasible_placements'],s['minimum_migrations'],json.dumps(s['moved_components']),json.dumps(s['p1']),json.dumps(s['p1_latency_ms'])])

def main():
    parser=argparse.ArgumentParser(description='Single unified AR/VR benchmark (500 ms flow SLOs, shortest network paths).')
    parser.add_argument('--num-scenarios','-n',type=int,default=240)
    parser.add_argument('--offload-ratio',type=float,default=.50)
    parser.add_argument('--stay-ratio',type=float,default=.375)
    parser.add_argument('--infeasible-ratio',type=float,default=.125)
    parser.add_argument('--seed',type=int,default=20261024)
    parser.add_argument('--max-attempts',type=int,default=100000)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'output'/'unified')
    args=parser.parse_args()
    if args.num_scenarios<=0 or args.max_attempts<=0:parser.error('Counts must be positive')
    ratios={'STAY':args.stay_ratio,'OFFLOAD':args.offload_ratio,'INFEASIBLE':args.infeasible_ratio}
    if any(v<0 for v in ratios.values()) or sum(ratios.values())<=0:parser.error('Ratios must be nonnegative and total > 0')
    targets=counts_for(args.num_scenarios,ratios)
    rng=random.Random(args.seed); accepted=[]; seen=set(); actual=collections.Counter(); attempted=0
    while len(accepted)<args.num_scenarios and attempted<args.max_attempts:
        attempted+=1
        item,service_reqs,budgets=sample(rng)
        sig=signature(item)
        if sig in seen:continue
        seen.add(sig)
        result=model.classify(service_reqs,budgets)
        label=result['decision']
        if actual[label]>=targets[label]:continue
        actual[label]+=1
        accepted.append({**item,**result})
    if len(accepted)<args.num_scenarios:
        raise RuntimeError(f'Could only generate {len(accepted)}/{args.num_scenarios} after {attempted} tries; found {dict(actual)} vs targets {targets}. Try more attempts, a smaller count, or adjusted ratios.')
    rng.shuffle(accepted)
    for i,item in enumerate(accepted,1):item['id']=f'ARVR-{i:06d}'
    dest=args.output_dir.resolve();dest.mkdir(parents=True,exist_ok=True)
    (dest/'scenarios.json').write_text(json.dumps(accepted,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    write_csv(dest/'scenarios.csv',accepted)
    summary={'total':len(accepted),'seed':args.seed,'requested_ratios':ratios,'target_counts':targets,
        'actual_counts':dict(actual),'candidate_attempts':attempted,'unique_candidate_signatures':len(seen),
        'selection':'stratified after independent classification; NOT an unbiased estimate of real-world frequency',
        'numeric_granularity':{'cpu_vcpu':str(CPU_STEP_VCPU), 'ram_gb':str(RAM_STEP_GB), 'latency_ms':str(LATENCY_STEP_MS)},
        'categories':{k:dict(collections.Counter(x['decision'] for x in accepted if x['category']==k)) for k in CATEGORIES},
        'baseline_p0':{f'C{c}':model.p0[c] for c in model.p0},
        'baseline_budgets_ms':model.baseline_budget,'baseline_latencies_ms':model.ref_latency,
        'latency_formula':'processing: 1000*workload_mi/mips; networking: shortest path by sum(1000*output_size_Mb/bandwidth_mbps + RTT_ms/2)',
        'background_applications':[2,3,4]}
    (dest/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(dest),'total':len(accepted),'distribution':dict(actual),'attempts':attempted},indent=2))

if __name__=='__main__':main()
