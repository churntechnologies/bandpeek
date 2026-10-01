#!/usr/bin/env python3
"""Real topology observation with isolated DB, persistent traffic and native traces.

Run --label before against the instrumented beta.2 binary first. Toggle the VPN
or Wi-Fi while it runs; no synthetic topology is presented as a real VPN test.
Evidence contains local interface addresses/process identities; keep it ignored.
"""
import argparse
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import time

from m5_window_cycles import App
from m5_performance import ps_table

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation/beta3'


def audit_raw(events):
    """Replay accepted raw counters using resolved birth identities and generations."""
    baselines = {}
    last_ms = {}
    audited = 0
    process_deltas = {}
    for e in events:
        if e['stage'] == 'process-delta':
            process_deltas.setdefault(e['sample_ms'], []).append(e)
    for sample in (e for e in events if e['stage'] == 'sample'):
        generation = sample['generation']
        raw = {r['pid']: r for r in sample['raw']}
        deltas = { (r['id']['pid'], r['id']['start_us']): r
                   for r in process_deltas.get(sample['sample_ms'], []) }
        rx = tx = 0
        for observation in sample['resolved']:
            identity = (observation['id']['pid'], observation['id']['start_us'])
            source = raw[identity[0]]
            counters = dict(download=source['rx'], upload=source['tx'])
            assert observation['counters'] == counters
            key = (generation, *identity)
            previous = baselines.get(key)
            delta = deltas[identity]
            assert delta['baseline'] == previous
            if previous is not None:
                expected = (max(0, source['rx']-previous['download']),
                            max(0, source['tx']-previous['upload']))
            elif generation not in last_ms:
                expected = (0, 0)
            else:
                # Existing identities establish zero baselines; new births can
                # contribute counters. Their birth-cutoff rule has Rust tests.
                expected = (delta['delta_rx'], delta['delta_tx'])
                assert expected in [(0, 0), (source['rx'], source['tx'])]
            assert expected == (delta['delta_rx'], delta['delta_tx'])
            rx += expected[0]; tx += expected[1]
            baselines[key] = counters
        assert (rx, tx) == (sample['delta_rx'], sample['delta_tx'])
        elapsed = (sample['sample_ms']-last_ms[generation])/1000 if generation in last_ms else None
        assert abs(sample['rx_bps']-(rx/elapsed if elapsed else 0)) < 1e-6
        assert abs(sample['tx_bps']-(tx/elapsed if elapsed else 0)) < 1e-6
        last_ms[generation] = sample['sample_ms']
        audited += 1
    return {'raw_samples_audited': audited, 'generations': len(last_ms)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--binary', default=str(ROOT / 'src-tauri/target/release/bandpeek'))
    ap.add_argument('--label', default='fixed')
    ap.add_argument('--seconds', type=int, default=300)
    ap.add_argument('--expect-recovery', action='store_true')
    ap.add_argument('--url', help='Use retained retrying HTTPS traffic instead of loopback')
    ap.add_argument('--audit-trace', type=pathlib.Path, help='Replay an existing trace without launching an app')
    args = ap.parse_args()
    if args.audit_trace:
        events = [json.loads(l) for l in args.audit_trace.read_text().splitlines()]
        print(json.dumps(audit_raw(events), indent=2)); return
    OUT.mkdir(parents=True, exist_ok=True)
    trace = OUT / f'{args.label}.jsonl'
    trace.unlink(missing_ok=True)
    stop_file = OUT / f'{args.label}.stop'
    stop_file.unlink(missing_ok=True)
    child_pids = set()
    with tempfile.TemporaryDirectory(prefix='bandpeek-topology-') as tmp:
        env = dict(os.environ, BANDPEEK_DB_PATH=tmp+'/db',
                   BANDPEEK_SETTINGS_PATH=tmp+'/settings', BANDPEEK_LIVE_TRACE_PATH=str(trace))
        app = App(args.binary, env, OUT / f'{args.label}.stderr', extra_args=['--validation-log'])
        server = client = None
        try:
            assert app.wait_for('validation-state=tray') is not None
            # Separate long-lived processes preserve terminal process identities.
            server = subprocess.Popen(['python3', '-u', '-c', '''
from http.server import BaseHTTPRequestHandler, HTTPServer
import pathlib, sys, time
class Handler(BaseHTTPRequestHandler):
 def log_message(self, *a): pass
 def do_GET(self):
  self.send_response(200); self.end_headers()
  try:
   while True:
    self.wfile.write(b'x'*32768); self.wfile.flush(); time.sleep(.02)
  except (BrokenPipeError, ConnectionResetError): pass
s=HTTPServer(('127.0.0.1',0),Handler)
pathlib.Path(sys.argv[1]).write_text(str(s.server_port)); s.serve_forever()
''', tmp+'/port'])
            while not pathlib.Path(tmp+'/port').exists(): time.sleep(.05)
            client = subprocess.Popen(['python3', '-u', '-c', '''
import urllib.request, sys, time
while True:
 try:
  with urllib.request.urlopen(sys.argv[1],timeout=5) as r:
   while r.read(32768): pass
 except Exception: time.sleep(.5)
''', args.url or 'http://127.0.0.1:'+pathlib.Path(tmp+'/port').read_text()])
            print(f'READY app_pid={app.proc.pid} fixture_pid={client.pid} trace={trace}', flush=True)
            start = time.monotonic()
            while time.monotonic()-start < args.seconds and not stop_file.exists():
                table = ps_table()
                children = [pid for pid, row in table.items()
                            if row['ppid'] == app.proc.pid and '/usr/bin/nettop' in row['command']]
                assert len(children) <= 1, children
                child_pids.update(children)
                assert app.proc.poll() is None
                time.sleep(.25)
        finally:
            try:
                if app.proc.poll() is None:
                    app.send('quit')
                app.proc.wait(timeout=20)
            finally:
                for process in [client, server]:
                    if process is not None:
                        process.terminate(); process.wait(timeout=10)
        assert not child_pids.intersection(ps_table()), 'nettop survived quit'
        with sqlite3.connect(tmp+'/db') as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            durable = db.execute('SELECT COALESCE(SUM(download_bytes),0), '
                                 'COALESCE(SUM(upload_bytes),0) FROM traffic_buckets').fetchone()
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    raw_audit = audit_raw(events)
    samples = [r for r in events if r['stage'] == 'sample']
    starts = [r for r in events if r['stage'] == 'collector-start']
    changes = [r for r in events if r['stage'] == 'topology']
    errors = []
    for before, after in zip(samples, samples[1:]):
        if 'session_bytes' in before:
            assert all(after['session_bytes'][d] >= before['session_bytes'][d]
                       for d in ['download', 'upload'])
    assert durable == (sum(s['delta_rx'] for s in samples), sum(s['delta_tx'] for s in samples))
    for start in starts:
        first = next((s for s in samples if s['generation'] == start['generation']), None)
        if first:
            assert first['delta_rx'] == first['delta_tx'] == first['rx_bps'] == first['tx_bps'] == 0
    for s in samples:
        ds = [e for e in events if e['stage'] == 'process-delta' and e['sample_ms'] == s['sample_ms']]
        assert (sum(d['delta_rx'] for d in ds), sum(d['delta_tx'] for d in ds)) == (s['delta_rx'], s['delta_tx'])
        notifications = [e for e in events if e['stage'] == 'live-notification'
                         and e['rates']['sample_sequence'] == s['sequence']]
        assert any(e['rates']['download_bytes_per_second'] == s['rx_bps']
                   and e['rates']['upload_bytes_per_second'] == s['tx_bps'] for e in notifications)
        statuses = [e for e in events if e['stage'] == 'status' and e['sequence'] == s['sequence']]
        assert any(e['delivered_rx_bps'] == s['rx_bps'] and e['delivered_tx_bps'] == s['tx_bps']
                   and e['native']['down'] == e['formatted_rx'] and e['native']['up'] == e['formatted_tx']
                   and e['native'].get('actual_title') == f"\t{e['formatted_rx']}\n\t{e['formatted_tx']}"
                   for e in statuses)
    recovery = []
    for reaped in [e for e in events if e['stage'] == 'collector-reaped' and e['reason'] == 'Ok(Topology)']:
        change = next(e for e in reversed(changes) if e['at_unix_ms'] <= reaped['at_unix_ms'])
        active = next((s for s in samples if s['generation'] > reaped['generation']
                       and any(r['pid'] == client.pid for r in s['raw']) and s['rx_bps'] > 500_000), None)
        status = next((e for e in events if active and e['stage'] == 'status'
                       and e['sequence'] == active['sequence'] and e['delivered_rx_bps'] == active['rx_bps']), None)
        latency = status['at_unix_ms']-change['at_unix_ms'] if status else None
        recovery.append({'change_unix_ms':change['at_unix_ms'],
                         'from_generation': reaped['generation'], 'to_live_traffic_ms': latency})
        if args.expect_recovery and (latency is None or latency > 15_000):
            errors.append(f'Traffic failed to return after topology change: {latency}')
    final_age = events[-1]['at_unix_ms']-samples[-1]['at_unix_ms']
    if args.expect_recovery and final_age > 5000:
        errors.append(f'Accepted samples stopped advancing ({final_age} ms)')
    result = dict(samples=len(samples), generations=len(starts), topologies=len(changes),
                  one_collector=True, quit_reaped=True, durable_totals=durable,
                  fixture_pid=client.pid, recovery=recovery, final_sample_age_ms=final_age,
                  raw_audit=raw_audit,
                  rejected_frames=sum(e['stage']=='rejected-frame' for e in events), errors=errors)
    (OUT/f'{args.label}.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2), flush=True)
    assert not errors, errors


if __name__ == '__main__': main()
