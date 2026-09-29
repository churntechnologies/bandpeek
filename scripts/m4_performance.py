#!/usr/bin/env python3
"""Milestone 4 performance: final UI, measured the same way as Milestones 2–3.

CPU = cumulative `ps` CPU-time delta / monotonic wall time (one core = 100%).
RSS is sampled every 5 s (13 samples over 60 s) and summed across processes
(shared pages can be counted more than once; this is not unique footprint).

Processes: the BandPeek native process, its nettop child, and WebKit helpers
attributed to BandPeek by Launch Services name ("bandpeek Web Content",
"bandpeek Graphics and Media", "bandpeek Networking") — not by timing guesses.

Phases
  visible     main window open and frontmost, live rates/history polling active
  closed      same instance after the window is closed via the production close
              path (WebView destroyed), 90 s teardown settle
  fresh-tray  new instance that never created a WebView (M2/M3 comparable)

Usage: python3 scripts/m4_performance.py [--settle-visible 30] [--settle-closed 90]
"""

import argparse
import json
import pathlib
import re
import statistics
import subprocess
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation' / 'milestone4'
APP = ROOT / 'src-tauri/target/release/bandpeek'


def ps_table():
    rows = {}
    for line in subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,time=,command='], text=True).splitlines():
        f = line.strip().split(None, 4)
        if len(f) != 5:
            continue
        pid, ppid, rss, cpu, command = f
        seconds = sum(float(x) * 60 ** i for i, x in enumerate(reversed(cpu.split(':'))))
        rows[int(pid)] = {'ppid': int(ppid), 'rss_kib': int(rss), 'cpu_seconds': seconds, 'command': command}
    return rows


def bandpeek_helpers():
    """PID → Launch Services name for helpers registered under BandPeek."""
    out = subprocess.run(['lsappinfo', 'list'], capture_output=True, text=True).stdout
    helpers, name = {}, None
    for line in out.splitlines():
        m = re.match(r'\s*\d+\) "(.*)" ASN', line)
        if m:
            name = m.group(1)
            continue
        m = re.search(r'pid = (\d+)', line)
        if m and name and name.lower().startswith('bandpeek '):
            helpers[int(m.group(1))] = name
    return helpers


def category(command, helper_name):
    if helper_name:
        return helper_name.split(' ', 1)[1]  # Web Content / Graphics and Media / Networking
    if 'nettop' in command:
        return 'nettop'
    return 'native'


def measure(app_pid, seconds=60):
    samples = []
    for i in range(seconds // 5 + 1):
        t = time.monotonic()
        table = ps_table()
        helpers = bandpeek_helpers()
        pids = {app_pid} | {p for p, r in table.items() if r['ppid'] == app_pid} | set(helpers)
        samples.append({'at': t, 'procs': {p: dict(table[p], helper=helpers.get(p)) for p in pids if p in table}})
        if i < seconds // 5:
            time.sleep(5)
    wall = samples[-1]['at'] - samples[0]['at']
    all_pids = set().union(*(s['procs'].keys() for s in samples))
    comps = {}
    for p in all_pids:
        seen = [s['procs'][p] for s in samples if p in s['procs']]
        stable = len(seen) == len(samples)
        name = category(seen[0]['command'], seen[0]['helper'])
        c = comps.setdefault(name, {'cpu_percent': 0.0, 'mean_rss_mib': 0.0, 'pids': [], 'complete': True})
        c['pids'].append(p)
        c['cpu_percent'] += 100 * (seen[-1]['cpu_seconds'] - seen[0]['cpu_seconds']) / wall
        c['complete'] &= stable
        c['mean_rss_mib'] += statistics.mean(s['procs'].get(p, {}).get('rss_kib', 0) for s in samples) / 1024
    peak = max(sum(pr['rss_kib'] for pr in s['procs'].values()) for s in samples) / 1024
    for c in comps.values():
        c['cpu_percent'] = round(c['cpu_percent'], 3)
        c['mean_rss_mib'] = round(c['mean_rss_mib'], 2)
    return {
        'seconds': round(wall, 2),
        'components': comps,
        'combined_cpu_percent': round(sum(c['cpu_percent'] for c in comps.values()), 3),
        'combined_mean_rss_mib': round(sum(c['mean_rss_mib'] for c in comps.values()), 2),
        'peak_simultaneous_rss_mib': round(peak, 2),
        'samples': samples,
    }


class Instance:
    def __init__(self, mode):
        self.lines = []
        self.proc = subprocess.Popen([str(APP), '--validation-mode', mode], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=ROOT)
        threading.Thread(target=lambda: [self.lines.append(l.strip()) for l in self.proc.stdout], daemon=True).start()

    def send(self, command):
        self.proc.stdin.write(command + '\n')
        self.proc.stdin.flush()

    def visibility(self):
        """Asks the main page whether WebKit considers it visible (polling active)."""
        before = len(self.lines)
        self.send("eval main window.__TAURI_INTERNALS__.invoke('validation_report',{text:document.visibilityState})")
        for _ in range(30):
            time.sleep(0.1)
            for l in self.lines[before:]:
                if l.startswith('validation-report='):
                    return l.split('=', 1)[1]
        return 'unknown'

    def quit(self):
        self.send('quit')
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def summary(label, r):
    print(f"\n{label}: {r['combined_cpu_percent']:.3f}% CPU, {r['combined_mean_rss_mib']:.2f} MiB mean RSS "
          f"(peak {r['peak_simultaneous_rss_mib']:.2f}) over {r['seconds']}s")
    for name, c in sorted(r['components'].items()):
        flag = '' if c['complete'] else ' (not present for whole window)'
        print(f"  {name:20s} {c['cpu_percent']:7.3f}%  {c['mean_rss_mib']:8.2f} MiB  pids={c['pids']}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--settle-visible', type=int, default=30)
    ap.add_argument('--settle-closed', type=int, default=90)
    ap.add_argument('--settle-fresh', type=int, default=30)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}

    app = Instance('visible')
    try:
        time.sleep(3)
        app.send('open')  # bring to front so WebKit treats the page as visible
        time.sleep(args.settle_visible)
        results['visible_state_start'] = app.visibility()
        results['visible'] = measure(app.proc.pid)
        results['visible_state_end'] = app.visibility()
        summary(f"Visible (page {results['visible_state_start']} → {results['visible_state_end']})", results['visible'])

        app.send('close')  # production close path: WebView destroyed, app stays in menu bar
        time.sleep(args.settle_closed)
        results['closed'] = measure(app.proc.pid)
        summary('Window closed (same instance, tray/background)', results['closed'])
    finally:
        app.quit()

    time.sleep(5)
    fresh = Instance('tray')
    try:
        time.sleep(args.settle_fresh)
        results['fresh_tray'] = measure(fresh.proc.pid)
        summary('Fresh tray-only instance (no WebView ever created)', results['fresh_tray'])
    finally:
        fresh.quit()

    (OUT / 'performance.json').write_text(json.dumps(results, indent=2))
    print(f"\nSaved {OUT / 'performance.json'}")


if __name__ == '__main__':
    main()
