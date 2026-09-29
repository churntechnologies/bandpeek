#!/usr/bin/env python3
"""60s sustained HTTP download, exact collector-source and native-title audit.
Local fixture deliberately exposes both endpoints: RX+TX sum is twice payload (the client RX and server TX).
An optional --url can replace it with a public sustained download.
"""
import argparse, json, os, pathlib, sqlite3, subprocess, tempfile, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from m5_window_cycles import App
ROOT=pathlib.Path(__file__).resolve().parents[1]

def serve(port_file):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            self.send_response(200); self.end_headers()
            try:
                chunk=b'x'*262144
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    self.wfile.write(chunk); self.wfile.flush(); time.sleep(1/64)
            except (BrokenPipeError, ConnectionResetError): pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    pathlib.Path(port_file).write_text(str(server.server_port));server.serve_forever()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--binary',default=str(ROOT/'src-tauri/target/release/bandpeek'))
    ap.add_argument('--label',default='fixed');ap.add_argument('--interval',default='2');ap.add_argument('--url')
    args=ap.parse_args();out=ROOT/'.validation/beta2';out.mkdir(parents=True,exist_ok=True)
    trace=out/f'live-{args.label}.jsonl';trace.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='bandpeek-live-') as tmp:
        work=pathlib.Path(tmp);env=dict(os.environ,BANDPEEK_DB_PATH=str(work/'db'),BANDPEEK_SETTINGS_PATH=str(work/'settings'),BANDPEEK_LIVE_TRACE_PATH=str(trace))
        server=None;download=None
        app=App(args.binary,env,out/f'live-{args.label}.stderr',extra_args=['--interval',args.interval])
        try:
            assert app.wait_for('validation-state=tray') is not None
            time.sleep(12)
            if args.url: url=args.url
            else:
                server=subprocess.Popen(['python3',__file__,'serve',str(work/'port')])
                while not (work/'port').exists(): time.sleep(.05)
                url='http://127.0.0.1:'+ (work/'port').read_text()
            started=time.monotonic();start_unix=time.time()
            download=subprocess.Popen(['python3',str(ROOT/'scripts/beta2_download.py'),url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            payload=download.stdout.readline().strip();err='';ended=time.monotonic();end_unix=time.time()
            assert json.loads(payload)['seconds'] >= 59, payload
            time.sleep(12)
        finally:
            if download and download.poll() is None:download.terminate();download.wait()
            if server:server.terminate();server.wait()
            app.send('quit');app.proc.wait(timeout=20)
            with sqlite3.connect(work/'db') as db:
                integrity=db.execute('PRAGMA integrity_check').fetchone()[0];assert integrity=='ok'
                durable_rx,durable_tx=db.execute('SELECT COALESCE(SUM(download_bytes),0),COALESCE(SUM(upload_bytes),0) FROM traffic_buckets').fetchone()
    rows=[json.loads(l) for l in trace.read_text().splitlines()];samples=[r for r in rows if r['stage']=='sample'];statuses={r['sequence']:r for r in rows if r['stage']=='status'}
    # Independent delta reconstruction from raw rows of the SAME consumed stream.
    baselines={};previous=None;errors=[];audit=[]
    for sample in samples:
        rx=tx=0
        for raw in sample['raw']:
            pid=raw['pid'];old=baselines.get(pid)
            if old:rx+=max(0,raw['rx']-old['rx']);tx+=max(0,raw['tx']-old['tx'])
            baselines[pid]=raw
        if previous is not None:
            elapsed=(sample['sample_ms']-previous)/1000
            expected=(rx/elapsed,tx/elapsed)
            # unresolved rows documented separately; high-traffic retained fixture must agree.
            status=statuses.get(sample['sequence'])
            if not status:errors.append(f"no status for {sample['sequence']}")
            elif (status['delivered_rx_bps'],status['delivered_tx_bps']) != (sample['rx_bps'],sample['tx_bps']):errors.append(f"propagation {sample['sequence']}")
            if status and (status['native']['down']!=status['formatted_rx'] or status['native']['up']!=status['formatted_tx']):errors.append(f"format {sample['sequence']}")
            if status and 'actual_title' in status['native']:
                actual=status['native']['actual_title']
                if actual!=f"\t{status['formatted_rx']}\n\t{status['formatted_tx']}":errors.append(f"actual native title {sample['sequence']}")
            deltas=[r for r in rows if r['stage']=='process-delta' and r['sample_ms']==sample['sample_ms']]
            if deltas:
                resolved_rx=sum(d['delta_rx'] for d in deltas);resolved_tx=sum(d['delta_tx'] for d in deltas)
                if (resolved_rx,resolved_tx)!=(sample['delta_rx'],sample['delta_tx']):errors.append(f"aggregation {sample['sequence']}")
                if abs(resolved_rx/elapsed-sample['rx_bps'])>1e-6 or abs(resolved_tx/elapsed-sample['tx_bps'])>1e-6:errors.append(f"rate calculation {sample['sequence']}")
            audit.append(dict(sequence=sample['sequence'],raw_rx_delta=rx,raw_tx_delta=tx,collector_rx_delta=sample['delta_rx'],collector_tx_delta=sample['delta_tx'],elapsed_seconds=elapsed,source_rx_bps=expected[0],bandpeek_rx_bps=sample['rx_bps'],source_tx_bps=expected[1],bandpeek_tx_bps=sample['tx_bps'],status=status,framing_delay_ms=sample['published_ms']-sample['sample_ms']))
        previous=sample['sample_ms']
    fixture_samples=[];last_raw={'rx':0,'tx':0};last_at=None
    for sample in samples:
        raw=next((r for r in sample['raw'] if r['pid']==download.pid),None)
        if raw is not None:
            if last_raw is not None:
                dt=(sample['sample_ms']-last_at)/1000
                fixture_samples.append(dict(sequence=sample['sequence'],raw=raw,delta_rx=max(0,raw['rx']-last_raw['rx']),delta_tx=max(0,raw['tx']-last_raw['tx']),elapsed_seconds=dt,rx_bps=max(0,raw['rx']-last_raw['rx'])/dt,tx_bps=max(0,raw['tx']-last_raw['tx'])/dt))
            last_raw=raw
        last_at=sample['sample_ms']
    process_deltas=[r for r in rows if r['stage']=='process-delta' and r['id']['pid']==download.pid]
    for fixture in fixture_samples:
        sample=next(s for s in samples if s['sequence']==fixture['sequence'])
        delta=next((d for d in process_deltas if d['sample_ms']==sample['sample_ms']),None)
        if process_deltas and (not delta or (delta['delta_rx'],delta['delta_tx'])!=(fixture['delta_rx'],fixture['delta_tx'])):errors.append(f"fixture delta {fixture['sequence']}")
    result=dict(sqlite_integrity=integrity,durable_rx=durable_rx,durable_tx=durable_tx,fixture_samples=fixture_samples,duration_seconds=ended-started,payload_and_speed=payload,fixture_pid=download.pid,fixture_exit=download.returncode,fixture_stderr=err,start_unix_ms=start_unix*1000,end_unix_ms=end_unix*1000,scope='loopback: both endpoints' if not args.url else args.url,errors=errors,audit=audit)
    (out/f'live-{args.label}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['audit','fixture_samples']},indent=2),flush=True)
    result['peak_rx_mbps']=max(s['rx_bps'] for s in samples)*8/1e6
    if samples[0].get('at_unix_ms'):
        high=[s for s in samples if s['at_unix_ms']>=start_unix*1000 and any(f['sequence']==s['sequence'] and f['rx_bps']>1_000_000 for f in fixture_samples)]
        result['onset_to_high_ms']=high[0]['at_unix_ms']-start_unix*1000 if high else None
        result['onset_to_status_high_ms']=statuses[high[0]['sequence']]['at_unix_ms']-start_unix*1000 if high else None
        drops=[s for s in samples if s['at_unix_ms']>end_unix*1000 and any(f['sequence']==s['sequence'] and f['rx_bps']<json.loads(payload)['bytes_per_second']*.2 for f in fixture_samples)]
        result['end_to_status_drop_ms']=statuses[drops[0]['sequence']]['at_unix_ms']-end_unix*1000 if drops else None
        low=[s for s in samples if s['at_unix_ms']>end_unix*1000 and any(f['sequence']==s['sequence'] and f['rx_bps']<100 for f in fixture_samples)]
        result['end_to_idle_ms']=low[0]['at_unix_ms']-end_unix*1000 if low else None
        result['end_to_status_idle_ms']=statuses[low[0]['sequence']]['at_unix_ms']-end_unix*1000 if low else None
    (out/f'live-{args.label}.json').write_text(json.dumps(result,indent=2)+'\n')
    print({k:result[k] for k in ['peak_rx_mbps','onset_to_high_ms','end_to_idle_ms'] if k in result},flush=True)
    print('peak RX B/s',max(s['rx_bps'] for s in samples),'source peak',max(a['source_rx_bps'] for a in audit),'samples',len(samples),flush=True)
    assert durable_rx==sum(s['delta_rx'] for s in samples) and durable_tx==sum(s['delta_tx'] for s in samples), 'history flush totals differ'
    assert not errors,errors
    # A long-lived fixture must produce high-rate samples and settle after traffic.
    assert max(f['rx_bps'] for f in fixture_samples)>1_000_000
    assert any(f['rx_bps']==0 for f in fixture_samples[-4:])

if __name__=='__main__':
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='serve':serve(sys.argv[2])
    else:main()
