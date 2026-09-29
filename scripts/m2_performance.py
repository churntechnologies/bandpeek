#!/usr/bin/env python3
"""Warm visible -> hidden -> destroyed-webview tray, same app and collector.
Cumulative CPU deltas, 20s settling (90s for standalone tray) + >=60s. No Vite. Captures
new WebKit helper candidates; verify commands in private evidence.
"""
import argparse, json, queue, statistics, subprocess, threading, time
from m2_common import ROOT, OUT, save

def table():
    result={}
    for line in subprocess.check_output(['ps','-axo','pid=,ppid=,rss=,time=,command='],text=True).splitlines():
        f=line.strip().split(None,4)
        if len(f)!=5:continue
        pid,ppid,rss,cpu,command=f;parts=cpu.split(':')
        seconds=sum(float(x)*60**i for i,x in enumerate(reversed(parts)))
        result[int(pid)]={'ppid':int(ppid),'rss_kib':int(rss),'cpu_seconds':seconds,'command':command}
    return result

def measure(pids, seconds=60):
    samples=[]
    for _ in range(seconds//5+1):
        t=time.monotonic();rows=table()
        samples.append({'at':t,'processes':{p:rows[p] for p in pids if p in rows}})
        if len(samples)<seconds//5+1:time.sleep(5)
    wall=samples[-1]['at']-samples[0]['at'];metrics=[]
    for p in sorted(pids):
        seen=[s['processes'][p] for s in samples if p in s['processes']]
        if not seen:continue
        stable=all(p in s['processes'] for s in samples)
        metrics.append({'pid':p,'command':seen[0]['command'],'stable':stable,
            'cpu_percent':100*(seen[-1]['cpu_seconds']-seen[0]['cpu_seconds'])/wall if stable else None,
            'mean_rss_mib':statistics.mean(s['processes'].get(p,{}).get('rss_kib',0) for s in samples)/1024,
            'peak_rss_mib':max(x['rss_kib'] for x in seen)/1024})
    return {'seconds':wall,'metrics':metrics,'cpu_percent':sum(m['cpu_percent'] for m in metrics) if all(m['stable'] for m in metrics) else None,
        'mean_rss_mib':sum(m['mean_rss_mib'] for m in metrics),'samples':samples}

def desktop(profile, visible_only=False, tray_only=False):
    before=table();messages=queue.Queue();pids=set()
    app=subprocess.Popen([str(ROOT/f'src-tauri/target/{profile}/bandpeek'),'--validation-mode','tray' if tray_only else 'visible' if visible_only else 'sequence']+(['--validation-seconds','180' if tray_only else '90'] if (visible_only or tray_only) else []),stdout=subprocess.PIPE,stderr=(OUT/f'perf-{profile}.stderr').open('w'),text=True)
    def read():
        for line in app.stdout:
            if line.startswith('validation-state='):messages.put(line.strip().split('=',1)[1])
    threading.Thread(target=read,daemon=True).start()
    try:
        for mode in (('tray',) if tray_only else ('visible',) if visible_only else ('visible','hidden','tray')):
            state=messages.get(timeout=110);assert state==mode
            time.sleep(90 if tray_only else 20)
            current=table();assert app.pid in current
            pids.add(app.pid)
            pids.update(p for p,r in current.items() if r['ppid']==app.pid)
            # Freeze the launch cohort: new Safari tabs later in the run are not ours.
            helper_labels={}
            if mode=='visible' or tray_only:
                for p,r in current.items():
                    if p not in before and 'com.apple.WebKit.' in r['command']:
                        label=subprocess.check_output(['lsappinfo','info','-only','name,pid',str(p)],text=True)
                        helper_labels[p]=label
                        if 'bandpeek' in label.lower():pids.add(p)
            result=measure(pids);result.update({'helper_labels':helper_labels,'settling_seconds':90 if tray_only else 20,'cold_tray':tray_only})
            result.update({'profile':profile,'mode':mode,'app_pid':app.pid})
            save(f'perf-{profile}-{mode}',result)
        app.wait(timeout=30)
    finally:
        # Sequence exits itself through Tauri, which stops/reaps the collector.
        if app.poll() is None:app.wait(timeout=280)

def core(interval):
    path=OUT/f'perf-core-{interval}.jsonl'
    with path.open('w') as log:
        app=subprocess.Popen([str(ROOT/'src-tauri/target/release/bandpeek-probe'),'90',str(interval)],stdout=log)
        try:
            time.sleep(20);rows=table();pids={app.pid}|{p for p,r in rows.items() if r['ppid']==app.pid}
            result=measure(pids);result['interval']=interval;save(f'perf-core-{interval}',result)
        finally:app.wait(timeout=100)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--profile',choices=['debug','release']);parser.add_argument('--interval',type=int,choices=[2,5]);parser.add_argument('--visible-only',action='store_true');parser.add_argument('--tray-only',action='store_true');args=parser.parse_args()
    if args.profile:desktop(args.profile,args.visible_only,args.tray_only)
    elif args.interval:core(args.interval)
    else:parser.error('choose --profile or --interval')
