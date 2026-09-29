#!/usr/bin/env python3
"""Release desktop measurement. CPU = change in cumulative process CPU / wall time.
Includes new WebKit helpers; inspect saved PID commands for attribution. No estimates.
"""
import json, pathlib, subprocess, time, statistics, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'.validation';OUT.mkdir(exist_ok=True)
def table():
    result={}
    text=subprocess.check_output(['ps','-axo','pid=,ppid=,rss=,time=,command='],text=True)
    for line in text.splitlines():
        fields=line.strip().split(None,4)
        if len(fields)!=5:continue
        pid,ppid,rss,cpu,command=fields
        parts=cpu.split(':'); seconds=float(parts[-1])+int(parts[-2])*60
        if len(parts)==3:seconds+=int(parts[0])*3600
        result[int(pid)]={'ppid':int(ppid),'rss_kib':int(rss),'cpu_seconds':seconds,'command':command}
    return result
if len(sys.argv)>1 and sys.argv[1]=='--pids':
    pids={int(value) for value in sys.argv[2].split(',')}
    app_pid=int(sys.argv[2].split(',')[0])
    initial=table()
else:
    before=table()
    binary=pathlib.Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'src-tauri/target/release/bandpeek'
    app=subprocess.Popen([str(binary)],stdout=open(OUT/'desktop-stdout.txt','w'),stderr=open(OUT/'desktop-stderr.txt','w'))
    app_pid=app.pid
    (OUT/'desktop-pid.txt').write_text(str(app_pid))
    time.sleep(20)
    initial=table()
    if app.poll() is not None: raise RuntimeError(f'Desktop exited {app.returncode}')
    pids={app_pid}
    for pid,row in initial.items():
        if pid not in before and ('com.apple.WebKit.' in row['command'] or row['ppid']==app_pid):pids.add(pid)
print('Measuring PIDs',sorted(pids),flush=True)
samples=[{'at':time.monotonic(),'processes':{p:initial[p] for p in pids if p in initial}}]
for _ in range(12):
    time.sleep(5);now=table()
    missing=pids-now.keys()
    if missing:raise RuntimeError(f'Processes disappeared during measurement: {missing}')
    samples.append({'at':time.monotonic(),'processes':{p:now[p] for p in pids}})
wall=samples[-1]['at']-samples[0]['at']
metrics=[]
for p in sorted(pids):
    begin=samples[0]['processes'][p];end=samples[-1]['processes'][p]
    metrics.append({'pid':p,'command':end['command'],'average_cpu_percent':100*(end['cpu_seconds']-begin['cpu_seconds'])/wall,
      'mean_rss_mib':statistics.mean(s['processes'][p]['rss_kib'] for s in samples)/1024,
      'max_rss_mib':max(s['processes'][p]['rss_kib'] for s in samples)/1024})
result={'duration_seconds':wall,'app_pid':app_pid,'metrics':metrics,'combined_average_cpu_percent':sum(x['average_cpu_percent'] for x in metrics),
 'combined_mean_rss_mib':sum(x['mean_rss_mib'] for x in metrics),'samples':samples}
(OUT/'performance.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='samples'},indent=2),flush=True)
# Leave the measured app open for manual/native UI inspection.
