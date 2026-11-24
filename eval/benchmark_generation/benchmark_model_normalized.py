#!/usr/bin/env python3
"""Reproducible AR/VR multi-component + two-flow benchmark (Python stdlib only).

IMPORTANT MODEL ASSUMPTIONS:
- link triple [bandwidth_Mbps, RTT_seconds, ...] (second entry is RTT in seconds)
- dataSize interpreted as Mb; transmission_seconds = Mb/Mbps + RTT_seconds/2
- processing_seconds = cpuWorkloadMI / node.cpu_MIPS
- additive path delay; no contention, queues, bandwidth sharing or return journey
- CPU/RAM deltas alter reservations, not cpuWorkloadMI or software version
"""
import csv, itertools, json, random, math, heapq
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parent
# Model-only module: importing must not generate files
load=lambda p:json.loads((ROOT/p).read_text())
services={s['serviceID']:s for s in load('services.json')}
nodes={n['nodeID']:n for t in ('nodes','users','helpers') for n in load(t+'.json')}
links=load('infraConnections.json');adj=load('componentsConnections.json')
placement={(int(s),int(c)):(int(v),int(n)) for s,c,v,n in load('placement_P0_app1_V1.json')}
comps={s:{c['componentID']:c for c in obj['components']} for s,obj in services.items()}
val=lambda s,c,v:next(x['characteristics'] for x in comps[s][c]['versions'] if x['versionNumber']==v)
base={c:dict(val(1,c,placement[1,c][0])) for c in range(1,6)}
p0={c:placement[1,c][1] for c in range(1,6)}
mobile=(2,3,4)
compute=tuple(n for n in nodes if nodes[n]['nodeTier'] in (1,2,3))
keys=('cpu','cpuVcpu','memory','disk')
edges=[(i+1,j+1) for i,row in enumerate(adj) for j,b in enumerate(row) if b]
flows={'f1':{'name':'Object Recognition Flow','path':[1,2,3,5]},'f2':{'name':'AR Rendering Flow','path':[1,3,4,5]}}
names={1:'Vision Capture',2:'Object Recognition',3:'Scene Processing',4:'AR Rendering',5:'Display Output'}
background=defaultdict(lambda:defaultdict(float))
for (s,c),(v,n) in placement.items():
    if s!=1:
        for k in keys:background[n][k]+=val(s,c,v)[k]


# Use the normalized system model as the canonical source of numeric constraints.
# The original simulator JSONs remain the source of topology, app dependencies,
# application versions and packet sizes only; workload MI comes from the
# normalized system model.
NORMALIZED_DIR = ROOT / 'system_model_normalized'

with (NORMALIZED_DIR / 'services.json').open(encoding='utf-8') as handle:
    normalized_services = json.load(handle)['application_context']['services']
for record in normalized_services:
    c = int(record['service_id'][1:])
    if record['current_node'] != f'N{p0[c]}':
        raise ValueError(f'Placement mismatch for service C{c}')
    base[c]['cpuVcpu'] = float(record['requirements']['cpu_cores'])
    base[c]['memory'] = float(record['requirements']['ram_gb']) * 1000.0
    base[c]['disk'] = float(record['storage_gb']) * 1000.0
    base[c]['cpu'] = float(record['mips_reservation'])
    # Use normalized workload in processing-time and flow-latency calculations.
    base[c]['cpuWorkloadMI'] = float(record['workload_mi'])
    if abs(float(record['output_size']) - base[c]['dataSize']) > 1e-6:
        raise ValueError(f'Payload changed for service C{c}')

with (NORMALIZED_DIR / 'nodes.csv').open(newline='', encoding='utf-8') as handle:
    for record in csv.DictReader(handle):
        n = int(record['node_id'][1:])
        chars = nodes[n]['characteristics']
        chars['cpuVcpu'] = float(record['cpu_cores'])
        chars['memory'] = float(record['ram_gb']) * 1000.0
        chars['disk'] = float(record['storage_gb']) * 1000.0
        chars['cpu'] = float(record['mips'])

# Reconstruct link values from the canonical undirected link list. Each line
# corresponds to both directions in the original 15x15 simulator matrix.
link_lookup = {}
with (NORMALIZED_DIR / 'links.csv').open(newline='', encoding='utf-8') as handle:
    for record in csv.DictReader(handle):
        i, j = int(record['source_node'][1:]), int(record['target_node'][1:])
        bandwidth = float(record['bandwidth_mbps'])
        rtt_seconds = 2 * float(record['propagation_delay_ms']) / 1000.0
        for a, b in ((i, j), (j, i)):
            if (a, b) in link_lookup:
                raise ValueError(f'Duplicate undirected network edge {(a, b)}')
            link_lookup[a, b] = (bandwidth, rtt_seconds)
actual_edges = {(i, j) for i in nodes for j in nodes
                if i != j and links[i-1][j-1][0] > 0}
if actual_edges != set(link_lookup):
    raise ValueError('Normalized link topology differs from simulator topology')
for (i, j), (bandwidth, rtt_seconds) in link_lookup.items():
    original = links[i-1][j-1]
    original[0] = bandwidth
    original[1] = rtt_seconds

background.clear()
with (NORMALIZED_DIR / 'background_loads.csv').open(newline='', encoding='utf-8') as handle:
    for record in csv.DictReader(handle):
        n = int(record['node_id'][1:])
        background[n].update({
            'cpuVcpu': float(record['background_cpu_cores']),
            'memory': float(record['background_ram_gb']) * 1000.0,
            'disk': float(record['background_storage_gb']) * 1000.0,
            'cpu': float(record['background_mips'])
        })

# Multi-hop networking is permitted whenever a finite-cost route exists.
# Shortest paths minimize sum(dataSize_Mb / bandwidth_Mbps + RTT_seconds / 2)
# independently for each application's communication edge.
def shortest_link_seconds(source, dest, payload_mb):
    if source == dest: return 0.0
    dist={source:0.0}; heap=[(0.0,source)]
    while heap:
        d,n=heapq.heappop(heap)
        if d > dist[n]+1e-12:continue
        if n==dest:return d
        for nxt in nodes:
            bw,rtt,*_=links[n-1][nxt-1]
            if nxt==n or bw<=0:continue
            nd=d+payload_mb/bw+rtt/2.0
            if nd < dist.get(nxt,math.inf)-1e-12:
                dist[nxt]=nd;heapq.heappush(heap,(nd,nxt))
    return math.inf

# Cache shortest-path link costs as they depend on the payload of the sender.
link_cost={(c,a,b):shortest_link_seconds(a,b,base[c]['dataSize'])
           for c in base for a in nodes for b in nodes}
def connectivity(p):
    return all(math.isfinite(link_cost[u,p[u],p[v]]) for u,v in edges)

candidates=[{1:p0[1],2:x,3:y,4:z,5:p0[5]} for x,y,z in itertools.product(compute,repeat=3)]
candidates=[p for p in candidates if connectivity(p)]

def resource_feasible(p,d):
    totals=defaultdict(lambda:defaultdict(float))
    for n,items in background.items():totals[n].update(items)
    for c,n in p.items():
        for k in keys:totals[n][k]+=d[c][k]
    return all(totals[n][k]<=nodes[n]['characteristics'][k]+1e-7 for n in totals for k in keys)

# Processing + shortest-hop-by-latency communication for each application arc.
def path_delay_ms(path,p):
    seconds=sum(base[c]['cpuWorkloadMI']/nodes[p[c]]['characteristics']['cpu'] for c in path)
    for u,v in zip(path,path[1:]):
        seconds+=link_cost[u,p[u],p[v]]
    return round(seconds*1000,6)

latencies={tuple(p[c] for c in mobile):{f:path_delay_ms(d['path'],p) for f,d in flows.items()} for p in candidates}
ref_latency={f:path_delay_ms(d['path'],p0) for f,d in flows.items()}
# Reference SLO: 500 ms for each flow, matching normalized flows.json
baseline_budget={f:500.0 for f in flows}
assert resource_feasible(p0,base)

# Check the ECSP-Sim raw inputs against the standardized System Model.
# This prevents silent changes of units, node tiers, placement, or baseline demands.
def verify_standardized_system_model():
    sm=NORMALIZED_DIR
    node_rows=list(csv.DictReader((sm/'nodes.csv').open()))
    assert len(node_rows)==len(nodes)
    valid_tiers={'iot','edge','cloud'}
    node_types={'access_point','edge_server','cloud_server','helper_device','user_device'}
    for row in node_rows:
        n=int(row['node_id'][1:]); c=nodes[n]['characteristics']
        assert row['tier'] in valid_tiers and row['node_type'] in node_types
        assert abs(float(row['cpu_cores'])-c['cpuVcpu'])<1e-6
        assert abs(float(row['ram_gb'])*1000-c['memory'])<.002
        assert abs(float(row['storage_gb'])*1000-c['disk'])<.002
        assert abs(float(row['mips'])-c['cpu'])<1e-6
        expected={1:('iot','access_point'),2:('edge','edge_server'),3:('cloud','cloud_server'),4:('iot','helper_device'),0:('iot','user_device')}[nodes[n]['nodeTier']]
        assert (row['tier'],row['node_type'])==expected
    service_rows=json.loads((sm/'services.json').read_text(encoding='utf-8'))['application_context']['services']
    for row in service_rows:
        c=int(row['service_id'][1:]);b=base[c]
        assert row['current_node']==f'N{p0[c]}'
        assert abs(row['requirements']['cpu_cores']-b['cpuVcpu'])<1e-6
        assert abs(row['requirements']['ram_gb']*1000-b['memory'])<.002
        assert abs(row['workload_mi']-b['cpuWorkloadMI'])<1e-6
        assert abs(row['output_size']-b['dataSize'])<1e-6 # Mb, NOT MB
    flow_rows=json.loads((sm/'flows.json').read_text(encoding='utf-8'))['application_context']['flows']
    for row in flow_rows:
        f=row['flow_id']; lat=ref_latency[f]
        assert abs(lat-row['baseline_estimated_latency_ms'])<.002, (f,lat,row['baseline_estimated_latency_ms'])
        assert abs(baseline_budget[f]-row['requirements']['latency'])<.002
    link_rows=list(csv.DictReader((sm/'links.csv').open()))
    for row in link_rows:
        a,b=int(row['source_node'][1:]),int(row['target_node'][1:])
        bw,rtt,*_=links[a-1][b-1]
        assert abs(float(row['bandwidth_mbps'])-bw)<1e-6
        assert abs(float(row['propagation_delay_ms'])-500*rtt)<.001 # RTT seconds / 2 -> ms
    bg_rows=list(csv.DictReader((sm/'background_loads.csv').open()))
    for row in bg_rows:
        n=int(row['node_id'][1:]);v=background[n]
        for col,key,scale in [('background_cpu_cores','cpuVcpu',1),('background_ram_gb','memory',1000),('background_storage_gb','disk',1000),('background_mips','cpu',1)]:
            assert abs(float(row[col])*scale-v[key])<.005, (n,col)
    print('Validated normalized iot/edge/cloud System Model and Mb / RTT/2 units.')
verify_standardized_system_model()

def classify(changes, budget_changes):
    demands={c:dict(v) for c,v in base.items()}
    for change in changes:demands[change['component_id']][change['resource']]=change['new_value']
    budgets=dict(baseline_budget);budgets.update(budget_changes)
    allowed=[]
    for p in candidates:
        if not resource_feasible(p,demands):continue
        latency=latencies[tuple(p[c] for c in mobile)]
        if all(latency[f]<=budgets[f]+1e-7 for f in flows):allowed.append(p)
    stayed=next((p for p in allowed if all(p[c]==p0[c] for c in mobile)),None)
    if stayed:status='STAY';chosen=stayed
    elif allowed:
        status='OFFLOAD';chosen=min(allowed,key=lambda p:(sum(p[c]!=p0[c] for c in mobile),tuple(p[c] for c in mobile)))
    else:status='INFEASIBLE';chosen=None
    moved=[] if chosen is None else [c for c in mobile if chosen[c]!=p0[c]]
    return {'decision':status,'feasible_placements':len(allowed),'minimum_migrations':None if chosen is None else len(moved),'moved_components':moved,'p1':None if chosen is None else {f'C{c}':n for c,n in chosen.items()},'p1_latency_ms':None if chosen is None else latencies[tuple(chosen[c] for c in mobile)]}

