#!/usr/bin/env python3
"""Milestone 5 performance: same method as Milestones 2–4, one instance, four states.

CPU = cumulative `ps` CPU-time delta / monotonic wall time (one core = 100%).
RSS is sampled every 5 s over ~60 s and summed across processes (shared pages
can be counted more than once; not unique footprint). The native process's
physical footprint (`vmmap -summary`) is recorded at each phase end.

Processes: the native process, its nettop child, and WebKit helpers attributed
by Launch Services name ("<executable name> Web Content" / "Graphics and Media"
/ "Networking"), not by timing.

Phases (same instance, on a copy of the history database):
  fresh-tray   started menu-bar-only; no WebView has ever existed
  visible      main window opened and ordered front (page must report `visible`)
  closed       main window closed (WebView destroyed), settled
  cycled       after N further open/close cycles, settled

Usage: python3 scripts/m5_performance.py [--binary PATH] [--label NAME] [--cycles 20]
Evidence: .validation/milestone5/performance-<label>.json
"""

import argparse
import json
import os
import pathlib
import queue
import re
import shutil
import sqlite3
import statistics
import subprocess
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation' / 'milestone5'
REAL_DB = pathlib.Path.home() / 'Library/Application Support/BandPeek/bandpeek.db'


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


def helpers(app_name):
    prefix = app_name.lower() + ' '
    out = subprocess.run(['lsappinfo', 'list'], capture_output=True, text=True).stdout
    found, name = {}, None
    for line in out.splitlines():
        m = re.match(r'\s*\d+\) "(.*)" ASN', line)
        if m:
            name = m.group(1)
            continue
        m = re.search(r'pid = (\d+)', line)
        if m and name and name.lower().startswith(prefix):
            found[int(m.group(1))] = name[len(prefix):]
    return found


def footprint_mib(pid):
    out = subprocess.run(['vmmap', '-summary', str(pid)], capture_output=True, text=True).stdout
    m = re.search(r'Physical footprint:\s+([\d.]+)([KMG])', out)
    return round(float(m.group(1)) * {'K': 1 / 1024, 'M': 1, 'G': 1024}[m.group(2)], 2) if m else None


def measure(app_pid, app_name, seconds=60, tick=None):
    samples = []
    for i in range(seconds // 5 + 1):
        if tick:
            tick()
        t = time.monotonic()
        table = ps_table()
        roles = helpers(app_name)
        pids = {app_pid} | {p for p, r in table.items() if r['ppid'] == app_pid} | set(roles)
        samples.append({'at': t, 'procs': {p: dict(table[p], role=roles.get(p)) for p in pids if p in table}})
        if i < seconds // 5:
            time.sleep(5)
    wall = samples[-1]['at'] - samples[0]['at']
    comps = {}
    for p in set().union(*(s['procs'].keys() for s in samples)):
        seen = [s['procs'][p] for s in samples if p in s['procs']]
        name = seen[0]['role'] or ('nettop' if 'nettop' in seen[0]['command'] else 'native')
        c = comps.setdefault(name, {'cpu_percent': 0.0, 'mean_rss_mib': 0.0, 'pids': [], 'complete': True})
        c['pids'].append(p)
        c['cpu_percent'] += 100 * (seen[-1]['cpu_seconds'] - seen[0]['cpu_seconds']) / wall
        c['complete'] &= len(seen) == len(samples)
        c['mean_rss_mib'] += statistics.mean(s['procs'].get(p, {}).get('rss_kib', 0) for s in samples) / 1024
    for c in comps.values():
        c['cpu_percent'] = round(c['cpu_percent'], 3)
        c['mean_rss_mib'] = round(c['mean_rss_mib'], 2)
    nettop = [p for p, r in samples[-1]['procs'].items() if 'nettop' in r['command']]
    return {
        'seconds': round(wall, 2),
        'components': comps,
        'combined_cpu_percent': round(sum(c['cpu_percent'] for c in comps.values()), 3),
        'combined_mean_rss_mib': round(sum(c['mean_rss_mib'] for c in comps.values()), 2),
        'native_footprint_mib': footprint_mib(app_pid),
        'nettop_children': [{'pid': p, 'parent_is_app': samples[-1]['procs'][p]['ppid'] == app_pid} for p in nettop],
        'webkit_helpers': sorted(helpers(app_name).values()),
    }


class App:
    def __init__(self, binary, env):
        self.lines = queue.Queue()
        self.name = pathlib.Path(binary).name
        self.proc = subprocess.Popen([binary, '--validation-mode', 'tray'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, cwd=ROOT)
        threading.Thread(target=lambda: [self.lines.put(l.strip()) for l in self.proc.stdout], daemon=True).start()

    def send(self, command):
        self.proc.stdin.write(command + '\n')
        self.proc.stdin.flush()

    def report(self, js, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.send(f"eval main window.__TAURI_INTERNALS__.invoke('validation_report',{{text:String({js})}})")
            end = time.monotonic() + 0.5
            while time.monotonic() < end:
                try:
                    line = self.lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line.startswith('validation-report=') and line.split('=', 1)[1]:
                    return line.split('=', 1)[1]
        return None


def summary(label, r):
    print(f"\n{label}: {r['combined_cpu_percent']:.3f}% CPU, {r['combined_mean_rss_mib']:.2f} MiB mean RSS, "
          f"native footprint {r['native_footprint_mib']} MiB, nettop {r['nettop_children']}, helpers {r['webkit_helpers']}", flush=True)
    for name, c in sorted(r['components'].items()):
        flag = '' if c['complete'] else ' (not present for the whole window)'
        print(f"  {name:20s} {c['cpu_percent']:7.3f}%  {c['mean_rss_mib']:8.2f} MiB{flag}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary', default=str(ROOT / 'src-tauri/target/release/bandpeek'))
    ap.add_argument('--label', default='current')
    ap.add_argument('--cycles', type=int, default=20)
    ap.add_argument('--settle-fresh', type=int, default=30)
    ap.add_argument('--settle-visible', type=int, default=30)
    ap.add_argument('--settle-closed', type=int, default=90)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-m5-perf-'))
    db = work / 'bandpeek.db'
    if REAL_DB.exists():
        with sqlite3.connect(f'file:{REAL_DB}?mode=ro', uri=True) as s, sqlite3.connect(db) as d:
            s.backup(d)
    env = dict(os.environ, BANDPEEK_DB_PATH=str(db), BANDPEEK_SETTINGS_PATH=str(work / 'settings.json'))
    app = App(args.binary, env)
    results = {'binary': args.label}
    try:
        time.sleep(args.settle_fresh)
        results['fresh_tray'] = measure(app.proc.pid, app.name)
        summary('Fresh tray-only (no WebView yet)', results['fresh_tray'])

        app.send('open')
        time.sleep(1)
        app.send('front')  # on screen without activation; WebKit throttles occluded pages
        time.sleep(args.settle_visible)
        for _ in range(5):  # other windows may come forward; the page must be visible
            app.send('front')
            time.sleep(1)
            results['visible_page_state'] = app.report('document.visibilityState')
            if results['visible_page_state'] == 'visible':
                break
        # Re-order front before each sample (no-op while already frontmost).
        results['visible'] = measure(app.proc.pid, app.name, tick=lambda: app.send('front'))
        results['visible_page_state_end'] = app.report('document.visibilityState')
        summary(f"Main window visible (page {results['visible_page_state']} → {results['visible_page_state_end']})",
                results['visible'])

        app.send('close')
        time.sleep(args.settle_closed)
        results['closed'] = measure(app.proc.pid, app.name)
        summary('After closing the main window', results['closed'])

        for _ in range(args.cycles):
            app.send('open')
            app.report("document.querySelector('.header') ? 'ok' : ''")
            app.send('close')
            time.sleep(0.5)
        time.sleep(args.settle_closed)
        results['cycled'] = measure(app.proc.pid, app.name)
        summary(f'After {args.cycles} more open/close cycles', results['cycled'])
    finally:
        app.send('quit')
        try:
            app.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            app.proc.kill()
        results['exit_status'] = app.proc.returncode
        shutil.rmtree(work, ignore_errors=True)
    path = OUT / f'performance-{args.label}.json'
    path.write_text(json.dumps(results, indent=2))
    print(f'\nSaved {path}')


if __name__ == '__main__':
    main()
