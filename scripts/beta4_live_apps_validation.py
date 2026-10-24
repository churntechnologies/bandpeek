#!/usr/bin/env python3
"""beta.4 live per-app speeds: Now tab and popup "Using now" on the release app.

A local HTTP server streams zeros; curl downloads them at a fixed rate, so a
known process moves a known number of bytes per second. The harness checks that
curl is listed with about that speed in the main window's Now tab and in the
popup, that it drops out after curl exits, that the Now tab persists natively,
and that the popup keeps its height while apps come and go. Uses an isolated
database/settings and does not record history.

Usage (outside the agent sandbox, after `npm run desktop:build`):
  python3 scripts/beta4_live_apps_validation.py
Evidence: .validation/beta4/ (snapshots, report.json)
"""

import http.server
import json
import os
import pathlib
import queue
import subprocess
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / 'src-tauri/target/release/bandpeek'
OUT = ROOT / '.validation' / 'beta4'
RATE = 4_000_000  # curl --limit-rate, bytes per second
ROWS = "[...document.querySelectorAll('.tbody .row')].map(r=>[...r.children].map(c=>c.innerText.trim()))"
NOW = "[...document.querySelectorAll('.popup-now .popup-app')].map(r=>r.innerText.replace(/\\n/g,' | '))"


class Zeros(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.end_headers()
        chunk = bytes(65536)
        try:
            while True:
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class App:
    def __init__(self, env):
        self.lines = queue.Queue()
        self.proc = subprocess.Popen([str(APP), '--validation-mode', 'visible', '--validation-no-record', '--validation-dir', str(OUT)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, env=env, cwd=ROOT)
        threading.Thread(target=lambda: [self.lines.put(l.strip()) for l in self.proc.stdout], daemon=True).start()

    def send(self, command, wait=0.0):
        self.proc.stdin.write(command + '\n')
        self.proc.stdin.flush()
        time.sleep(wait)

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

    def report(self, label, js):
        """Evaluates `js` in a window and returns its JSON value."""
        while not self.lines.empty():
            self.lines.get_nowait()
        self.send(f"eval {label} Promise.resolve({js}).then(v=>window.__TAURI_INTERNALS__.invoke('validation_report',{{text:JSON.stringify(v)}}))", 1)
        value = self.wait_for('validation-report=', 5)
        return json.loads(value) if value else None


def mb_per_s(text):
    number, unit = text.split()[:2]
    scale = {'KB/s': 1e3, 'MB/s': 1e6, 'GB/s': 1e9}[unit]
    return float(number) * scale


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-beta4-'))
    env = dict(os.environ, BANDPEEK_DB_PATH=str(work / 'history.db'), BANDPEEK_SETTINGS_PATH=str(work / 'settings.json'))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Zeros)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results, checks = {}, []

    def check(name, ok, detail=None):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name, '' if detail is None else detail)

    app = App(env)
    curl = None
    try:
        check('app started', app.wait_for('validation-state=', 20) is not None)
        app.send('resize 840 600', 1)
        app.send('front', 3)
        curl = subprocess.Popen(['curl', '-s', '-o', '/dev/null', '--limit-rate', str(RATE),
                                 f'http://127.0.0.1:{server.server_address[1]}/'])
        time.sleep(7)  # baseline sample plus at least two rate samples
        app.report('main', "([...document.querySelectorAll('.segment')].find(b=>b.textContent==='Now').click(),true)")
        time.sleep(3)
        results['pressed'] = app.report('main', "[...document.querySelectorAll('.segment[aria-pressed=true]')].map(b=>b.textContent)")
        check('Now tab selected', results['pressed'] == ['Now'], results['pressed'])
        results['rows'] = app.report('main', ROWS)
        row = next((r for r in results['rows'] or [] if r[0] == 'curl'), None)
        check('curl listed in Now tab', row is not None, row)
        if row:
            down = mb_per_s(row[1])
            # The local server (this harness's Python) uploads as much as curl downloads.
            check('curl download within 25% of the limit', abs(down - RATE) <= RATE * 0.25, f'{down:.0f} B/s vs {RATE}')
        results['ui_state'] = app.report('main', "window.__TAURI_INTERNALS__.invoke('get_ui_state')")
        check('Now tab persisted natively', (results['ui_state'] or {}).get('range') == 'now', results['ui_state'])
        for look in ('dark', 'light'):
            app.send(f'appearance {look}', 2)
            app.send('front', 2)
            app.send(f'snapshot now-{look}', 2)
        # A harness-launched app can't take activation, so the popup may blur-close; retry.
        for _ in range(8):
            app.send('popup-close', 0.5)
            app.send('popup', 4)
            results['popup_busy'] = app.report('tray', NOW)
            if results['popup_busy']:
                break
        app.send('snapshot now-popup', 2)
        results['popup_height_busy'] = app.report('tray', "document.querySelector('.popup').getBoundingClientRect().height")
        check('curl listed in popup Using now', any(r.startswith('curl |') for r in results['popup_busy'] or []), results['popup_busy'])
        check('popup lists at most three apps', len(results['popup_busy'] or []) <= 3)
        curl.terminate()
        curl.wait()
        time.sleep(7)
        results['popup_idle'] = app.report('tray', NOW)
        results['popup_height_idle'] = app.report('tray', "document.querySelector('.popup').getBoundingClientRect().height")
        check('curl gone from popup after exit', not any(r.startswith('curl |') for r in results['popup_idle'] or []), results['popup_idle'])
        check('popup height unchanged', results['popup_height_busy'] == results['popup_height_idle'],
              (results['popup_height_busy'], results['popup_height_idle']))
        app.send('popup-close', 1)
        results['rows_idle'] = app.report('main', ROWS)
        check('curl gone from Now tab after exit', not any(r[0] == 'curl' for r in results['rows_idle'] or []))
    finally:
        if curl and curl.poll() is None:
            curl.kill()
        app.send('quit')
        try:
            app.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            app.proc.kill()
        server.shutdown()
    check('app exited', app.proc.poll() is not None)
    (OUT / 'report.json').write_text(json.dumps({'results': results, 'checks': checks}, indent=2))
    failed = [c['check'] for c in checks if not c['ok']]
    print('ALL PASS' if not failed else f'FAILED: {failed}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
