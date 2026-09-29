#!/usr/bin/env python3
"""Milestone 5: memory retained by repeated main-window / popup open-close cycles.

Runs a release binary in validation mode (tray-only start) on a *copy* of the
history database and a temporary settings file, then drives the production
open/close paths through the validation stdin commands:

  main cycle   `open` → page rendered (checked by eval) → `close`
  popup cycle  `popup` → popup page rendered → `popup-close`

At checkpoints (after 0, 10, 50, 100 main cycles, then after the popup cycles)
it lets the app settle and records:

  - RSS of the native process (`ps`), and its physical footprint (`vmmap -summary`)
  - live Objective-C instances by class (`heap`): tao windows/views/delegates,
    wry's WebView parent and WKWebView, wry URL-scheme handlers
  - BandPeek WebKit helper processes (Launch Services names)
  - the page's viewport after each reopen (layout must not change between opens)

Usage: python3 scripts/m5_window_cycles.py [--binary PATH] [--label NAME]
                                           [--checkpoints 10,50,100] [--popup-cycles 100]
Evidence: .validation/milestone5/cycles-<label>.json
"""

import argparse
import json
import os
import pathlib
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation' / 'milestone5'
REAL_DB = pathlib.Path.home() / 'Library/Application Support/BandPeek/bandpeek.db'
CLASSES = {
    'tao_window': r'TaoWindow$',
    'tao_view': r'^TaoView$',
    'tao_window_delegate': r'^TaoWindowDelegate$',
    'wry_parent_view': r'WryWebViewParent',
    'wry_webview': r'^WryWebView',
    'wk_webview_internal': r'^WKWebView$',
    'url_scheme_handlers': r'URLSchemeHandler_',
}


class App:
    def __init__(self, binary, env, stderr_path):
        self.lines = queue.Queue()
        self.name = pathlib.Path(binary).name
        self.proc = subprocess.Popen([binary, '--validation-mode', 'tray'], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=stderr_path.open('w'), text=True, env=env,
                                     cwd=ROOT)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line.rstrip('\n'))

    def send(self, command):
        if self.proc.poll() is not None:
            raise RuntimeError(f'app exited with status {self.proc.returncode} before {command!r}')
        self.proc.stdin.write(command + '\n')
        self.proc.stdin.flush()

    def wait_for(self, prefix, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line[len(prefix):]
        return None

    def report(self, label, js, timeout=8):
        """Evaluates `js` in a page until it returns a non-empty string."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.send(f"eval {label} window.__TAURI_INTERNALS__.invoke('validation_report',{{text:String({js})}})")
            got = self.wait_for('validation-report=', timeout=0.4)
            if got:
                return got
        return None


def rss_kib(pid):
    out = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
    return int(out) if out else 0


def footprint_mib(pid):
    out = subprocess.run(['vmmap', '-summary', str(pid)], capture_output=True, text=True).stdout
    m = re.search(r'Physical footprint:\s+([\d.]+)([KMG])', out)
    if not m:
        return None
    return round(float(m.group(1)) * {'K': 1 / 1024, 'M': 1, 'G': 1024}[m.group(2)], 2)


def heap_counts(pid):
    out = subprocess.run(['heap', str(pid)], capture_output=True, text=True).stdout
    counts = {k: 0 for k in CLASSES}
    for line in out.splitlines():
        f = line.split()
        if len(f) < 5 or not f[0].isdigit():
            continue
        name = f[3]
        for key, pattern in CLASSES.items():
            if re.search(pattern, name):
                counts[key] += int(f[0])
    return counts


def helpers(app_name):
    """WebKit helpers register with Launch Services as '<executable name> <role>'."""
    prefix = app_name.lower() + ' '
    out = subprocess.run(['lsappinfo', 'list'], capture_output=True, text=True).stdout
    found, name = [], None
    for line in out.splitlines():
        m = re.match(r'\s*\d+\) "(.*)" ASN', line)
        if m:
            name = m.group(1)
            continue
        m = re.search(r'pid = (\d+)', line)
        if m and name and name.lower().startswith(prefix):
            found.append(name[len(prefix):])
    return sorted(found)


def checkpoint(app, label, settle):
    time.sleep(settle)
    pid = app.proc.pid
    point = {'label': label, 'rss_mib': round(rss_kib(pid) / 1024, 2), 'footprint_mib': footprint_mib(pid),
             'objects': heap_counts(pid), 'webkit_helpers': helpers(app.name)}
    print(f"{label:>24}: RSS {point['rss_mib']:7.2f} MiB  footprint {point['footprint_mib']} MiB  "
          f"objects {point['objects']}  helpers {point['webkit_helpers']}", flush=True)
    return point


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary', default=str(ROOT / 'src-tauri/target/release/bandpeek'))
    ap.add_argument('--label', default='current')
    ap.add_argument('--checkpoints', default='10,50,100')
    ap.add_argument('--popup-cycles', type=int, default=100)
    ap.add_argument('--settle', type=int, default=15)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-m5-'))
    db = work / 'bandpeek.db'
    if REAL_DB.exists():
        sqlite_backup(REAL_DB, db)
    env = dict(os.environ, BANDPEEK_DB_PATH=str(db), BANDPEEK_SETTINGS_PATH=str(work / 'settings.json'))
    app = App(args.binary, env, OUT / f'cycles-{args.label}.stderr')
    result = {'binary': args.label, 'points': [], 'viewports': [], 'failures': []}
    try:
        app.wait_for('validation-state=tray')
        result['points'].append(checkpoint(app, 'fresh tray (0 opens)', args.settle))
        done = 0
        for target in [int(x) for x in args.checkpoints.split(',')]:
            while done < target:
                app.send('open')
                viewport = app.report('main', "document.querySelector('.header') ? innerWidth+'x'+innerHeight : ''")
                if not viewport:
                    result['failures'].append(f'main open {done + 1}: page did not render')
                result['viewports'].append(viewport)
                app.send('close')
                if app.wait_for('validation-state=main-closed') is None:
                    result['failures'].append(f'main close {done + 1}: no close event')
                done += 1
            result['points'].append(checkpoint(app, f'after {done} main opens', args.settle))
        for i in range(args.popup_cycles):
            app.send('popup')
            if not app.report('tray', "document.querySelector('.popup') ? 'ok' : ''"):
                result['failures'].append(f'popup open {i + 1}: page did not render')
            app.send('popup-close')
            time.sleep(0.4)  # the 300 ms reopen guard only applies to blur closes
        result['points'].append(checkpoint(app, f'+ {args.popup_cycles} popup opens', args.settle))
    except RuntimeError as error:
        result['failures'].append(str(error))
        print('ABORTED:', error)
    finally:
        if app.proc.poll() is None:
            app.send('quit')
        try:
            app.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            app.proc.kill()
        result['exit_status'] = app.proc.returncode
        shutil.rmtree(work, ignore_errors=True)

    distinct = sorted(set(v for v in result['viewports'] if v))
    result['distinct_viewports'] = distinct
    print(f"viewports after reopen: {distinct}; failures: {len(result['failures'])}")
    for f in result['failures'][:10]:
        print('  ', f)
    points = result['points']
    base = points[0]
    for p in points[1:]:
        print(f"  Δ vs fresh after '{p['label']}': RSS {p['rss_mib'] - base['rss_mib']:+.2f} MiB, "
              f"footprint {(p['footprint_mib'] or 0) - (base['footprint_mib'] or 0):+.2f} MiB")
    path = OUT / f'cycles-{args.label}.json'
    path.write_text(json.dumps(result, indent=2))
    print(f'Saved {path}')


def sqlite_backup(src, dst):
    """Consistent copy of a live WAL database (never the original file)."""
    import sqlite3
    with sqlite3.connect(f'file:{src}?mode=ro', uri=True) as s, sqlite3.connect(dst) as d:
        s.backup(d)
    return dst


if __name__ == '__main__':
    main()
