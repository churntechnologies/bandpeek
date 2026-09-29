#!/usr/bin/env python3
"""Milestone 2.5 Investigation B: Churn workload comparison across collection cadences.

Tests collection intervals: 5s, 2s, 1s.
Exact same reproducible workload as Milestone 2:
- 80 client processes per interval per trial (240 clients per interval across 3 trials)
- 10 clients in each combination of (4 KiB, 1 MiB) x (0s, 1s, 3s, 7s post-transfer hold)
- Seed: random.Random(928 + trial_number)
- Spacing: 80 - 320 ms
- Known loopback payload oracle; immediate exit reaped promptly
- Diagnostic 1-second reference observer
- Evidence saved to .validation/milestone2_5/
"""

import argparse
import json
import os
import pathlib
import pty
import random
import socket
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / ".validation" / "milestone2_5"
OUT.mkdir(parents=True, exist_ok=True)
PROBE = ROOT / "src-tauri" / "target" / "release" / "bandpeek-probe"

CLIENT = r'''
import socket,sys,time,json,os
start=time.monotonic();n=int(sys.argv[2]);s=socket.create_connection(('127.0.0.1',int(sys.argv[1])))
s.sendall(b'x'*n);s.shutdown(socket.SHUT_WR)
assert s.recv(1)==b'!';s.close()
time.sleep(float(sys.argv[3]))
print(json.dumps({'pid':os.getpid(),'bytes':n,'lifetime':time.monotonic()-start}))
'''

class NettopReference:
    def __init__(self, name):
        self.lines = []
        self.frames = []
        self.frame = None
        self.file = (OUT / f"{name}.csv").open("w")
        self.master, slave = pty.openpty()
        args = ["/usr/bin/nettop", "-L", "0", "-s", "1", "-n", "-x", "-P", "-J", "bytes_in,bytes_out"]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=slave, stderr=subprocess.PIPE)
        os.close(slave)
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        buf = b""
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line_b, buf = buf.split(b"\n", 1)
                line = line_b.decode(errors="replace").rstrip("\r")
                at = time.monotonic()
                self.lines.append((at, line))
                self.file.write(line + "\n")
                self.file.flush()
                if line == ",bytes_in,bytes_out,":
                    if self.frame is not None:
                        self.frames.append(self.frame)
                    self.frame = {"at": at, "rows": {}}
                elif self.frame is not None:
                    try:
                        label, down, up = line.rstrip(",").rsplit(",", 2)
                        name, pid = label.rsplit(".", 1)
                        self.frame["rows"][int(pid)] = {"download": int(down), "upload": int(up)}
                    except ValueError:
                        pass

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.thread.join(timeout=2)
        os.close(self.master)
        self.file.close()

def snapshots(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

def serve(listener):
    while True:
        try:
            c, _ = listener.accept()
        except OSError:
            return
        def receive(c):
            with c:
                while c.recv(65536):
                    pass
                c.sendall(b"!")
        threading.Thread(target=receive, args=(c,), daemon=True).start()

def trial(interval, trial_number):
    name = f"m25-churn-{interval}s-{trial_number}"
    print(f"\n--- Starting Trial: interval={interval}s, trial={trial_number} ---", flush=True)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    threading.Thread(target=serve, args=(listener,), daemon=True).start()
    
    ref = NettopReference(name + "-reference")
    path = OUT / f"{name}.jsonl"
    log = path.open("w")
    probe_secs = "65" if interval >= 2 else "55"
    probe = subprocess.Popen([str(PROBE), probe_secs, str(interval)], stdout=log)
    clients = []
    records = []
    threads = []
    try:
        # Wait for baseline
        time.sleep(interval * 2 + 1)
        snaps = snapshots(path)
        assert snaps and snaps[-1]["sample_sequence"] >= 1, "Probe failed to establish baseline"
        
        jobs = [(size, hold) for size in (4096, 1048576) for hold in (0, 1, 3, 7) for _ in range(10)]
        rng = random.Random(928 + trial_number)
        rng.shuffle(jobs)
        
        def reap(p, size, hold, spawn_at):
            output, _ = p.communicate(timeout=20)
            assert p.returncode == 0, output
            item = json.loads(output)
            item.update({"hold": hold, "exit_at": time.monotonic()})
            item["spawn_to_reap_seconds"] = item["exit_at"] - spawn_at
            records.append(item)
            
        for size, hold in jobs:
            spawn_at = time.monotonic()
            p = subprocess.Popen(
                [sys.executable, "-c", CLIENT, str(listener.getsockname()[1]), str(size), str(hold)],
                stdout=subprocess.PIPE,
                text=True
            )
            clients.append(p)
            t = threading.Thread(target=reap, args=(p, size, hold, spawn_at))
            t.start()
            threads.append(t)
            time.sleep(rng.uniform(0.08, 0.32))
            
        for t in threads:
            t.join()
        assert len(records) == len(jobs)
        
        # Wait for final frames
        time.sleep(interval * 3 + 2)
        samples = snapshots(path)
        last = samples[-1]
        assert last["sample_sequence"] > 3 and last["collector_generation"] == 1
        
        rows = {r["id"]["pid"]: r for r in last["rows"]}
        for r in records:
            row = rows.get(r["pid"])
            r["observed_bytes"] = row["bytes"]["upload"] if row else 0
            r["identified"] = row is not None
            refs = [f for f in ref.frames if r["pid"] in f["rows"]]
            r["reference_max_upload"] = max((f["rows"][r["pid"]]["upload"] for f in refs), default=0)
            r["terminal_frames"] = sum(f["at"] > r["exit_at"] for f in refs)
            assert 0 <= r["observed_bytes"] <= r["bytes"], r
            
        cells = []
        for size in (4096, 1048576):
            for hold in (0, 1, 3, 7):
                group = [r for r in records if r["bytes"] == size and r["hold"] == hold]
                cells.append({
                    "payload": size,
                    "hold_seconds": hold,
                    "n": len(group),
                    "mean_lifetime": sum(r["lifetime"] for r in group) / len(group),
                    "process_missed_pct": 100 * sum(not r["identified"] for r in group) / len(group),
                    "byte_loss_pct": 100 * (1 - sum(r["observed_bytes"] for r in group) / sum(r["bytes"] for r in group)),
                    "observed_bytes": sum(r["observed_bytes"] for r in group),
                    "total_bytes": sum(r["bytes"] for r in group),
                })
                
        total_bytes = sum(r["bytes"] for r in records)
        observed_bytes = sum(r["observed_bytes"] for r in records)
        result = {
            "interval": interval,
            "trial": trial_number,
            "processes": len(records),
            "pid_reuse": len(records) - len({r["pid"] for r in records}),
            "processes_missed": sum(not r["identified"] for r in records),
            "process_missed_pct": 100 * sum(not r["identified"] for r in records) / len(records),
            "total_bytes": total_bytes,
            "observed_bytes": observed_bytes,
            "byte_loss_pct": 100 * (1 - observed_bytes / total_bytes),
            "terminal_processes": sum(r["terminal_frames"] > 0 for r in records),
            "unresolved_observations": last["unresolved_rows"],
            "cells": cells,
            "records": records,
        }
        
        result_file = OUT / f"{name}.json"
        result_file.write_text(json.dumps(result, indent=2))
        print(f"  Result {name}: process miss = {result['process_missed_pct']:.2f}%, byte loss = {result['byte_loss_pct']:.2f}%", flush=True)
        return result
    finally:
        for p in clients:
            if p.poll() is None:
                p.kill()
                p.wait()
        probe.wait(timeout=70)
        log.close()
        ref.close()
        listener.close()

def run_cadence_comparison(intervals=(5, 2, 1), trials=3):
    print("=" * 70)
    print("Milestone 2.5 Investigation B: Churn Cadence Comparison")
    print(f"Intervals: {intervals}, Trials per interval: {trials}")
    print("=" * 70)
    
    all_results = {interval: [] for interval in intervals}
    
    # Interleave intervals across trials: e.g. (5, 2, 1), (1, 2, 5), (2, 5, 1)
    for n in range(trials):
        order = list(intervals)
        if n % 3 == 1:
            order.reverse()
        elif n % 3 == 2:
            order = [intervals[1], intervals[0], intervals[2]]
        print(f"\n=== Trial Round {n + 1}/{trials} (Order: {order}) ===", flush=True)
        for interval in order:
            res = trial(interval, n)
            all_results[interval].append(res)
            time.sleep(3)
            
    # Aggregate results across trials for each interval
    summary = {}
    for interval in intervals:
        trial_list = all_results[interval]
        total_procs = sum(t["processes"] for t in trial_list)
        missed_procs = sum(t["processes_missed"] for t in trial_list)
        total_b = sum(t["total_bytes"] for t in trial_list)
        obs_b = sum(t["observed_bytes"] for t in trial_list)
        byte_losses = [t["byte_loss_pct"] for t in trial_list]
        
        # Aggregate by hold duration
        holds_summary = {}
        for hold in (0, 1, 3, 7):
            cell_procs = 0
            cell_missed_procs = 0
            cell_bytes = 0
            cell_obs_bytes = 0
            for t in trial_list:
                for c in t["cells"]:
                    if c["hold_seconds"] == hold:
                        cell_procs += c["n"]
                        cell_missed_procs += int(round(c["n"] * c["process_missed_pct"] / 100))
                        cell_bytes += c["total_bytes"]
                        cell_obs_bytes += c["observed_bytes"]
            holds_summary[f"{hold}s"] = {
                "hold_seconds": hold,
                "n_processes": cell_procs,
                "process_miss_pct": 100 * cell_missed_procs / cell_procs if cell_procs else 0,
                "byte_loss_pct": 100 * (1 - cell_obs_bytes / cell_bytes) if cell_bytes else 0,
                "captured_bytes_pct": 100 * cell_obs_bytes / cell_bytes if cell_bytes else 0,
            }
            
        summary[f"{interval}s"] = {
            "interval_seconds": interval,
            "trials": len(trial_list),
            "total_processes": total_procs,
            "processes_missed": missed_procs,
            "process_miss_pct": 100 * missed_procs / total_procs,
            "total_bytes": total_b,
            "observed_bytes": obs_b,
            "byte_loss_pct": 100 * (1 - obs_b / total_b),
            "byte_loss_range": [min(byte_losses), max(byte_losses)],
            "holds": holds_summary,
        }
        
    summary_path = OUT / "cadence_churn_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print("\n" + "=" * 70)
    print("CHURN CADENCE SUMMARY:")
    print("=" * 70)
    print(json.dumps(summary, indent=2))
    return summary

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--intervals", type=str, default="5,2,1")
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    intervals = [int(x.strip()) for x in args.intervals.split(",")]
    run_cadence_comparison(intervals=intervals, trials=args.trials)
