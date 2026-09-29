#!/usr/bin/env python3
"""Milestone 3 Performance Measurement.
Measures settled CPU and RSS for BandPeek release binary in tray-only background mode
and visible mode, divided into Native, nettop, and WebKit helpers.
Compares directly to Milestone 2 baseline.
"""

import argparse
import json
import os
import pathlib
import queue
import statistics
import subprocess
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation' / 'milestone3'
OUT.mkdir(parents=True, exist_ok=True)

def table():
    result = {}
    for line in subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,time=,command='], text=True).splitlines():
        f = line.strip().split(None, 4)
        if len(f) != 5:
            continue
        pid, ppid, rss, cpu, command = f
        parts = cpu.split(':')
        seconds = sum(float(x) * 60**i for i, x in enumerate(reversed(parts)))
        result[int(pid)] = {'ppid': int(ppid), 'rss_kib': int(rss), 'cpu_seconds': seconds, 'command': command}
    return result

def measure(pids, seconds=60):
    samples = []
    # 13 samples spaced 5s apart across 60 seconds
    for _ in range(seconds // 5 + 1):
        t = time.monotonic()
        rows = table()
        samples.append({'at': t, 'processes': {p: rows[p] for p in pids if p in rows}})
        if len(samples) < seconds // 5 + 1:
            time.sleep(5)
    wall = samples[-1]['at'] - samples[0]['at']
    metrics = []
    for p in sorted(pids):
        seen = [s['processes'][p] for s in samples if p in s['processes']]
        if not seen:
            continue
        stable = all(p in s['processes'] for s in samples)
        cpu_pct = 100 * (seen[-1]['cpu_seconds'] - seen[0]['cpu_seconds']) / wall if stable else None
        metrics.append({
            'pid': p,
            'command': seen[0]['command'],
            'stable': stable,
            'cpu_percent': cpu_pct,
            'mean_rss_mib': statistics.mean(s['processes'].get(p, {}).get('rss_kib', 0) for s in samples) / 1024,
            'peak_rss_mib': max(x['rss_kib'] for x in seen) / 1024,
        })
    combined_cpu = sum(m['cpu_percent'] for m in metrics if m['cpu_percent'] is not None)
    combined_rss = sum(m['mean_rss_mib'] for m in metrics)
    simultaneous_peaks = [sum(s['processes'].get(p, {}).get('rss_kib', 0) for p in pids) / 1024 for s in samples]
    return {
        'seconds': wall,
        'metrics': metrics,
        'cpu_percent': combined_cpu,
        'mean_rss_mib': combined_rss,
        'peak_rss_mib': max(simultaneous_peaks) if simultaneous_peaks else combined_rss,
    }

def measure_mode(mode='tray', settling=30, sample_duration=60):
    print(f"\n--- Measuring BandPeek Release in {mode.upper()} mode (settling={settling}s, measure={sample_duration}s) ---")
    before = table()
    messages = queue.Queue()
    pids = set()

    cmd = [
        str(ROOT / 'src-tauri/target/release/bandpeek'),
        '--validation-mode', mode,
        '--validation-seconds', str(settling + sample_duration + 30)
    ]
    app = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=(OUT / f'perf-release-{mode}.stderr').open('w'),
        text=True
    )

    def read_stdout():
        for line in app.stdout:
            if line.startswith('validation-state='):
                messages.put(line.strip().split('=', 1)[1])
    threading.Thread(target=read_stdout, daemon=True).start()

    try:
        state = messages.get(timeout=30)
        assert state == mode, f"Expected state {mode}, got {state}"
        print(f"State confirmed: {state}. Settling for {settling} seconds...")
        time.sleep(settling)

        current = table()
        assert app.pid in current, "App process unexpectedly exited"
        pids.add(app.pid)
        pids.update(p for p, r in current.items() if r['ppid'] == app.pid)

        if mode == 'visible':
            for p, r in current.items():
                if p not in before and 'com.apple.WebKit.' in r['command']:
                    try:
                        label = subprocess.check_output(['lsappinfo', 'info', '-only', 'name,pid', str(p)], text=True)
                        if 'bandpeek' in label.lower():
                            pids.add(p)
                    except Exception:
                        pass

        print(f"Target PIDs identified: {pids}. Measuring for {sample_duration}s...")
        res = measure(pids, seconds=sample_duration)
        res['mode'] = mode
        res['app_pid'] = app.pid
        
        # Categorize
        native_cpu = 0.0
        nettop_cpu = 0.0
        webkit_cpu = 0.0
        native_rss = 0.0
        nettop_rss = 0.0
        webkit_rss = 0.0

        for m in res['metrics']:
            cmd_str = m['command'].lower()
            c_pct = m['cpu_percent'] or 0.0
            r_mib = m['mean_rss_mib'] or 0.0
            if 'nettop' in cmd_str:
                nettop_cpu += c_pct
                nettop_rss += r_mib
            elif 'webkit' in cmd_str:
                webkit_cpu += c_pct
                webkit_rss += r_mib
            elif 'bandpeek' in cmd_str:
                native_cpu += c_pct
                native_rss += r_mib

        res['breakdown'] = {
            'native_cpu': round(native_cpu, 3),
            'nettop_cpu': round(nettop_cpu, 3),
            'webkit_cpu': round(webkit_cpu, 3),
            'combined_cpu': round(res['cpu_percent'], 3),
            'native_rss_mib': round(native_rss, 2),
            'nettop_rss_mib': round(nettop_rss, 2),
            'webkit_rss_mib': round(webkit_rss, 2),
            'combined_rss_mib': round(res['mean_rss_mib'], 2),
            'peak_rss_mib': round(res['peak_rss_mib'], 2),
        }

        save_path = OUT / f'perf-m3-{mode}.json'
        save_path.write_text(json.dumps(res, indent=2))
        print(f"Results for {mode}:")
        print(json.dumps(res['breakdown'], indent=2))
        return res
    finally:
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['tray', 'visible', 'both'], default='tray')
    args = parser.parse_args()

    if args.mode in ('tray', 'both'):
        measure_mode('tray', settling=30, sample_duration=60)
    if args.mode in ('visible', 'both'):
        measure_mode('visible', settling=20, sample_duration=60)
