#!/usr/bin/env python3
"""Check P0 resources and recompute baseline latency from normalized CSV/JSON."""
import argparse,csv,json,heapq,math
from pathlib import Path

def get_rows(path):
    with path.open(newline='',encoding='utf-8') as f:return list(csv.DictReader(f))

def check(root, update_flows=False):
    services=json.loads((root/'services.json').read_text())['application_context']['services']
    flows=json.loads((root/'flows.json').read_text())
    nodes={r['node_id']:r for r in get_rows(root/'nodes.csv')}
    background={r['node_id']:r for r in get_rows(root/'background_loads.csv')}
    adj={n:[] for n in nodes}
    for r in get_rows(root/'links.csv'):
        bw=float(r['bandwidth_mbps']); d=float(r['propagation_delay_ms'])
        if bw<=0:raise ValueError('Nonpositive bandwidth on link '+str(r))
        adj[r['source_node']].append((r['target_node'],bw,d))
        # The supplied links.csv stores each undirected link once.
        adj[r['target_node']].append((r['source_node'],bw,d))
    usages={n:{'cpu_cores':float(background[n]['background_cpu_cores']), 'ram_gb':float(background[n]['background_ram_gb']), 'storage_gb':float(background[n]['background_storage_gb']), 'mips':float(background[n]['background_mips'])} for n in nodes}
    for s in services:
        u=usages[s['current_node']];u['cpu_cores']+=float(s['requirements']['cpu_cores']);u['ram_gb']+=float(s['requirements']['ram_gb']);u['storage_gb']+=float(s['storage_gb']);u['mips']+=float(s['mips_reservation'])
    bad=[];margin=[]
    for n,u in usages.items():
        for k,v in u.items():
            capacity=float(nodes[n][k]);delta=capacity-v
            if delta < -1e-7:bad.append((n,k,capacity,v))
            margin.append((delta,n,k))
    def comm(a,b,payload):
        if a==b:return 0.
        heap=[(0.,a)];dist={a:0.}
        while heap:
            d,n=heapq.heappop(heap)
            if d>dist[n]+1e-10:continue
            if n==b:return d
            for nxt,bw,prop in adj[n]:
                nd=d+1000*payload/bw+prop
                if nd<dist.get(nxt,float('inf')):
                    dist[nxt]=nd;heapq.heappush(heap,(nd,nxt))
        return float('inf')
    by_name={s['service_id']:s for s in services}
    computed={}
    for f in flows['application_context']['flows']:
        path=f['path']
        processing=sum(1000*float(by_name[c]['workload_mi'])/float(nodes[by_name[c]['current_node']]['mips']) for c in path)
        networking=sum(comm(by_name[u]['current_node'],by_name[v]['current_node'],float(by_name[u]['output_size'])) for u,v in zip(path,path[1:]))
        computed[f['flow_id']]=round(processing+networking,6)
        if update_flows:f['baseline_estimated_latency_ms']=computed[f['flow_id']]
    if update_flows:(root/'flows.json').write_text(json.dumps(flows,indent=2,ensure_ascii=False)+'\n')
    return {'p0_resource_feasible':not bad,'resource_violations':bad,'minimum_remaining_resource_margin':min(margin),'baseline_latencies_ms':computed,'baseline_budgets_ms':{f['flow_id']:f['requirements']['latency'] for f in flows['application_context']['flows']},'flow_budgets_satisfied':{f:computed[f]<=next(x['requirements']['latency'] for x in flows['application_context']['flows'] if x['flow_id']==f) for f in computed}}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--system-model',type=Path,required=True);p.add_argument('--refresh-flow-latencies',action='store_true');a=p.parse_args();print(json.dumps(check(a.system_model,a.refresh_flow_latencies),indent=2))
