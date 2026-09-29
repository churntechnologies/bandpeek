#!/usr/bin/env python3
"""Live integration comparison. Writes private raw samples to ignored .validation/."""
import json, os, pathlib, pty, select, subprocess, threading, time
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation'
OUT.mkdir(exist_ok=True)
raw = open(OUT / 'independent-nettop.csv', 'w')
master, slave = pty.openpty()
independent = subprocess.Popen(['/usr/bin/nettop','-P','-L','0','-s','5','-n','-x','-J','bytes_in,bytes_out'], stdin=subprocess.PIPE, stdout=slave, stderr=subprocess.PIPE)
os.close(slave)
frames=[]
def read():
    buf=b''; frame=None
    while True:
        try: chunk=os.read(master, 65536)
        except OSError: break
        if not chunk: break
        buf += chunk
        while b'\n' in buf:
            line,buf=buf.split(b'\n',1); line=line.decode().rstrip('\r'); raw.write(line+'\n'); raw.flush()
            if line==',bytes_in,bytes_out,':
                if frame is not None: frames.append(frame)
                frame={'time':time.monotonic(),'rows':{}}
            elif frame is not None:
                try:
                    label,down,up=line.rstrip(',').rsplit(',',2)
                    name,pid=label.rsplit('.',1)
                    frame['rows'][int(pid)]={'name':name,'download':int(down),'upload':int(up)}
                except ValueError: pass
thread=threading.Thread(target=read,daemon=True); thread.start()
log=open(OUT/'validation-probe.jsonl','w')
probe=subprocess.Popen([str(ROOT/'src-tauri/target/release/bandpeek-probe'),'75'],stdout=log)
children=[]
try:
    time.sleep(10)
    server=subprocess.Popen(['node',str(ROOT/'scripts/traffic.mjs'),'server'],stdout=subprocess.PIPE,text=True);children.append(server)
    info=json.loads(server.stdout.readline())
    client=subprocess.Popen(['node',str(ROOT/'scripts/traffic.mjs'),str(info['port'])],stdout=subprocess.PIPE,text=True);children.append(client)
    client_out=client.communicate(timeout=45)[0]
    probe.wait(timeout=85); log.close()
    snapshots=[json.loads(x) for x in (OUT/'validation-probe.jsonl').read_text().splitlines()]
    comparisons=[]
    for pid in (client.pid,server.pid):
        observed=[r for s in snapshots for r in s['rows'] if r['id']['pid']==pid]
        refs=[f['rows'][pid] for f in frames if pid in f['rows']]
        band=max(observed,key=lambda r:r['total_bytes']) if observed else None
        expected={d:max((r[d] for r in refs),default=0) for d in ('download','upload')}
        comparisons.append({'pid':pid,'bandpeek':band,'independent_max_counters':expected,
          'difference':{d:band['bytes'][d]-expected[d] for d in expected} if band else None})
    # Independent arithmetic over matching complete frame counts. Newly appearing
    # PIDs have their full first counter counted here; BandPeek intentionally
    # baselines pre-existing processes whose first network row arrives later.
    base={}; totals={}
    aligned=frames[:snapshots[-1]['sample_sequence']]
    for index, frame in enumerate(aligned):
        for pid,row in frame['rows'].items():
            current=(row['download'],row['upload'])
            previous=base.get(pid,current if index==0 else (0,0))
            delta=tuple(max(0,a-b) for a,b in zip(current,previous))
            totals[pid]=tuple(a+b for a,b in zip(totals.get(pid,(0,0)),delta))
            base[pid]=current
    actual={r['id']['pid']:(r['bytes']['download'],r['bytes']['upload']) for r in snapshots[-1]['rows']}
    overall={'complete_frames':len(aligned),'bandpeek':snapshots[-1]['session_bytes'],
      'independent_delta_sums':{'download':sum(v[0] for v in totals.values()),'upload':sum(v[1] for v in totals.values())},
      'differing_processes':[{'pid':pid,'bandpeek':actual.get(pid,(0,0)),'independent':v,
        'difference':[a-b for a,b in zip(actual.get(pid,(0,0)),v)]} for pid,v in totals.items() if actual.get(pid,(0,0))!=v],
      'unresolved_observations':snapshots[-1]['unresolved_rows'],'rejected_samples':snapshots[-1]['rejected_samples']}
    (OUT/'overall-comparison.json').write_text(json.dumps(overall,indent=2))
    result={'client_output':client_out,'comparisons':comparisons,'last_snapshot':snapshots[-1], 'independent_frames':len(frames),'overall':overall}
    (OUT/'validation-result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({'client_output':client_out,'comparisons':comparisons,'independent_frames':len(frames)},indent=2))
finally:
    for child in children+[probe,independent]:
        if child.poll() is None: child.terminate()
        child.wait()
    os.close(master);raw.close()
