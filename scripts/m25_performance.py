#!/usr/bin/env python3
"""Milestone 2.5 Investigation B: Performance comparison across collection cadences.

Measures:
- BandPeek native CPU
- nettop CPU
- Combined collector/core CPU
- Mean and peak RSS
For collection intervals: 5s, 2s, 1s.
Core-only settled measurements over 60 seconds (13 readings every 5s, after 20s settling).
Also optional desktop visible measurement at 5s UI refresh for 5s, 2s, 1s collection.
"""

import argparse
import json
import pathlib
import statistics
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / ".validation" / "milestone2_5"
OUT.mkdir(parents=True, exist_ok=True)
PROBE = ROOT / "src-tauri" / "target" / "release" / "bandpeek-probe"
DESKTOP = ROOT / "src-tauri" / "target" / "release" / "bandpeek"

def process_table():
    result = {}
    out = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,time=,command='], text=True)
    for line in out.splitlines():
        fields = line.strip().split(None, 4)
        if len(fields) != 5:
            continue
        pid, ppid, rss, cpu, command = fields
        parts = cpu.split(':')
        seconds = sum(float(x) * 60**i for i, x in enumerate(reversed(parts)))
        result[int(pid)] = {
            'ppid': int(ppid),
            'rss_kib': int(rss),
            'cpu_seconds': seconds,
            'command': command
        }
    return result

def measure_pids(pids, duration=60, sample_interval=5):
    samples = []
    num_samples = duration // sample_interval + 1
    for _ in range(num_samples):
        t = time.monotonic()
        tbl = process_table()
        samples.append({'at': t, 'processes': {p: tbl[p] for p in pids if p in tbl}})
        if len(samples) < num_samples:
            time.sleep(sample_interval)
            
    wall = samples[-1]['at'] - samples[0]['at']
    metrics = []
    for p in sorted(pids):
        seen = [s['processes'][p] for s in samples if p in s['processes']]
        if not seen:
            continue
        stable = all(p in s['processes'] for s in samples)
        cpu_sec_delta = seen[-1]['cpu_seconds'] - seen[0]['cpu_seconds']
        cpu_pct = 100 * cpu_sec_delta / wall if (stable and wall > 0) else None
        mean_rss = statistics.mean(s['processes'].get(p, {}).get('rss_kib', 0) for s in samples) / 1024
        peak_rss = max(x['rss_kib'] for x in seen) / 1024
        metrics.append({
            'pid': p,
            'command': seen[0]['command'],
            'stable': stable,
            'cpu_seconds_delta': cpu_sec_delta,
            'cpu_percent': cpu_pct,
            'mean_rss_mib': mean_rss,
            'peak_rss_mib': peak_rss,
        })
        
    all_stable = all(m['stable'] for m in metrics)
    total_cpu = sum(m['cpu_percent'] for m in metrics if m['cpu_percent'] is not None) if all_stable else None
    total_mean_rss = sum(m['mean_rss_mib'] for m in metrics)
    return {
        'duration_seconds': wall,
        'metrics': metrics,
        'combined_cpu_percent': total_cpu,
        'combined_mean_rss_mib': total_mean_rss,
        'samples': samples,
    }

def measure_core(interval, settling=20, duration=60):
    print(f"\n--- Measuring Core Performance: interval={interval}s ---", flush=True)
    path = OUT / f"perf-core-{interval}s.jsonl"
    with path.open("w") as log:
        # 20s settling + 60s measuring + 10s buffer = 90s probe duration
        proc = subprocess.Popen([str(PROBE), str(settling + duration + 10), str(interval)], stdout=log)
        try:
            print(f"  Settling for {settling}s...")
            time.sleep(settling)
            tbl = process_table()
            pids = {proc.pid} | {p for p, r in tbl.items() if r['ppid'] == proc.pid}
            print(f"  Measuring PIDs {pids} over {duration}s...")
            result = measure_pids(pids, duration=duration)
            result['interval_seconds'] = interval
            
            # Identify native probe vs nettop child
            for m in result['metrics']:
                cmd = m['command']
                if 'nettop' in cmd:
                    result['nettop_metrics'] = m
                elif 'bandpeek-probe' in cmd:
                    result['native_metrics'] = m
                    
            save_path = OUT / f"perf-core-{interval}s.json"
            save_path.write_text(json.dumps(result, indent=2))
            
            nat_cpu = result.get('native_metrics', {}).get('cpu_percent', 0.0)
            net_cpu = result.get('nettop_metrics', {}).get('cpu_percent', 0.0)
            comb_cpu = result['combined_cpu_percent']
            rss = result['combined_mean_rss_mib']
            print(f"  Result ({interval}s core): Native={nat_cpu:.3f}%, nettop={net_cpu:.3f}%, Combined={comb_cpu:.3f}%, Mean RSS={rss:.2f} MiB")
            return result
        finally:
            proc.wait(timeout=10)

def run_performance_comparison(intervals=(5, 2, 1)):
    print("=" * 70)
    print("Milestone 2.5 Investigation B: Core Performance Across Intervals")
    print(f"Intervals: {intervals}")
    print("=" * 70)
    
    summary = {}
    for interval in intervals:
        res = measure_core(interval)
        nat_cpu = res.get('native_metrics', {}).get('cpu_percent', 0.0)
        net_cpu = res.get('nettop_metrics', {}).get('cpu_percent', 0.0)
        summary[f"{interval}s"] = {
            "interval_seconds": interval,
            "native_cpu_percent": nat_cpu,
            "nettop_cpu_percent": net_cpu,
            "combined_cpu_percent": res["combined_cpu_percent"],
            "combined_mean_rss_mib": res["combined_mean_rss_mib"],
            "duration_measured_seconds": res["duration_seconds"],
        }
        time.sleep(3)
        
    summary_path = OUT / "performance_cadence_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print("\n" + "=" * 70)
    print("PERFORMANCE CADENCE SUMMARY:")
    print("=" * 70)
    print(json.dumps(summary, indent=2))
    return summary

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intervals", type=str, default="5,2,1")
    args = parser.parse_args()
    intervals = [int(x.strip()) for x in args.intervals.split(",")]
    run_performance_comparison(intervals=intervals)
