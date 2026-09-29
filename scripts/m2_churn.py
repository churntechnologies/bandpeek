#!/usr/bin/env python3
"""Known loopback payload oracle; reap exits immediately (no zombie identity aid).
80 clients/interval/trial: 10 per payload/lifetime cell, seeded random order.
Counts client upload only, never server + client double counting. Reference
nettop is diagnostic; acknowledged payload is the byte-loss denominator.
"""
import argparse, json, random, socket, subprocess, sys, threading, time
from m2_common import OUT, PROBE, Nettop, save, snapshots
CLIENT=r'''
import socket,sys,time,json,os
start=time.monotonic();n=int(sys.argv[2]);s=socket.create_connection(('127.0.0.1',int(sys.argv[1])))
s.sendall(b'x'*n);s.shutdown(socket.SHUT_WR)
assert s.recv(1)==b'!';s.close()
time.sleep(float(sys.argv[3]))
print(json.dumps({'pid':os.getpid(),'bytes':n,'lifetime':time.monotonic()-start}))
'''
def serve(listener):
    while True:
        try: c,_=listener.accept()
        except OSError:return
        def receive(c):
            with c:
                while c.recv(65536):pass
                c.sendall(b'!')
        threading.Thread(target=receive,args=(c,),daemon=True).start()
def trial(interval, trial_number):
    name=f'churn-{interval}s-{trial_number}'
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    threading.Thread(target=serve,args=(listener,),daemon=True).start()
    ref=Nettop(name+'-reference',extra=['-J','bytes_in,bytes_out'])
    path=OUT/f'{name}.jsonl';log=path.open('w')
    probe=subprocess.Popen([str(PROBE),'65',str(interval)],stdout=log)
    clients=[];records=[];threads=[]
    try:
        time.sleep(interval*2+1)
        assert snapshots(path)[-1]['sample_sequence']>=1
        jobs=[(size,hold) for size in (4096,1048576) for hold in (0,1,3,7) for _ in range(10)]
        rng=random.Random(928+trial_number);rng.shuffle(jobs)
        def reap(p,size,hold,spawn_at):
            output,_=p.communicate(timeout=20)
            assert p.returncode==0,output
            item=json.loads(output);item.update({'hold':hold,'exit_at':time.monotonic()});item['spawn_to_reap_seconds']=item['exit_at']-spawn_at;records.append(item)
        for size,hold in jobs:
            spawn_at=time.monotonic()
            p=subprocess.Popen([sys.executable,'-c',CLIENT,str(listener.getsockname()[1]),str(size),str(hold)],stdout=subprocess.PIPE,text=True)
            clients.append(p)
            t=threading.Thread(target=reap,args=(p,size,hold,spawn_at));t.start();threads.append(t)
            time.sleep(rng.uniform(.08,.32))
        for t in threads:t.join()
        assert len(records)==len(jobs)
        time.sleep(interval*3+2)
        samples=snapshots(path);last=samples[-1]
        assert last['sample_sequence']>3 and last['collector_generation']==1
        rows={r['id']['pid']:r for r in last['rows']}
        for r in records:
            row=rows.get(r['pid']);r['observed_bytes']=row['bytes']['upload'] if row else 0
            r['identified']=row is not None
            refs=[f for f in ref.frames if r['pid'] in f['rows']]
            r['reference_max_upload']=max((f['rows'][r['pid']]['upload'] for f in refs),default=0)
            r['terminal_frames']=sum(f['at']>r['exit_at'] for f in refs)
            assert 0<=r['observed_bytes']<=r['bytes'],r
        cells=[]
        for size in (4096,1048576):
            for hold in (0,1,3,7):
                group=[r for r in records if r['bytes']==size and r['hold']==hold]
                cells.append({'payload':size,'hold_seconds':hold,'n':len(group),
                    'mean_lifetime':sum(r['lifetime'] for r in group)/len(group),
                    'process_missed_pct':100*sum(not r['identified'] for r in group)/len(group),
                    'byte_loss_pct':100*(1-sum(r['observed_bytes'] for r in group)/sum(r['bytes'] for r in group))})
        result={'interval':interval,'trial':trial_number,'processes':len(records),'pid_reuse':len(records)-len({r['pid'] for r in records}),
            'process_missed_pct':100*sum(not r['identified'] for r in records)/len(records),
            'byte_loss_pct':100*(1-sum(r['observed_bytes'] for r in records)/sum(r['bytes'] for r in records)),
            'terminal_processes':sum(r['terminal_frames']>0 for r in records),
            'unresolved_observations':last['unresolved_rows'],'cells':cells,'records':records}
        save(name,result)
    finally:
        for p in clients:
            if p.poll() is None:p.kill();p.wait()
        # Let the probe exit normally so its RAII guard reaps nettop.
        probe.wait(timeout=70);log.close();ref.close();listener.close()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--trials',type=int,default=3);args=parser.parse_args()
    for n in range(args.trials):
        for interval in ((5,2) if n%2==0 else (2,5)):trial(interval,n)
