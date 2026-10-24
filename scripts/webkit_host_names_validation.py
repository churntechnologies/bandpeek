#!/usr/bin/env python3
"""Host-named WebKit Networking rows on the release app.

Safari loads a local page whose script downloads zeros at a fixed rate through
Safari's own WebKit Networking process, then tries to close its tab (Safari may
throttle a background tab first, leaving a "BandPeek WebKit check" tab open). The harness checks
that the traffic is listed live as "Safari Networking" (shared-helper tooltip
naming Safari), not as "WebKit Networking", and that history stores it under
the `helper:Safari Networking` identity. Safari must already be running; the
page is opened in the background. Uses an isolated database and settings.

Usage (outside the agent sandbox, after `npm run desktop:build`):
  python3 scripts/webkit_host_names_validation.py
Evidence: .validation/webkit-hosts/ (snapshots, report.json)
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
OUT = ROOT / '.validation' / 'webkit-hosts'
RATE = 3_000_000  # bytes per second served to Safari
SECONDS = 24
ROWS = ("[...document.querySelectorAll('.tbody .row')].map(r=>({cells:[...r.children].map(c=>c.innerText.trim()),"
        "title:(r.querySelector('.app')||{}).title||''}))")
PAGE = f"""<!doctype html><title>BandPeek WebKit check</title><p>BandPeek validation: downloading zeros…</p>
<script>
fetch('/zeros', {{cache: 'no-store'}}).then(r => r.arrayBuffer()).then(b => fetch('/done?bytes=' + b.byteLength))
  .finally(() => setTimeout(() => window.close(), 500));
</script>""".encode()
done = threading.Event()  # page script reached its end (best effort: Safari may throttle a background tab)
served = threading.Event()  # server wrote the whole stream


class Server(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/done'):
            done.set()
        if self.path not in ('/', '/zeros') and not self.path.startswith('/done'):
            # Safari also asks for favicon/touch icons, the latter from its app process.
            self.send_error(404)
            return
        body = PAGE if self.path == '/' else b'' if self.path.startswith('/done') else None
        self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        if body is not None:
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        chunk = bytes(RATE // 10)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(chunk) * SECONDS * 10))
        self.end_headers()
        try:
            for _ in range(SECONDS * 10):
                self.wfile.write(chunk)
                time.sleep(0.1)
            served.set()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class App:
    def __init__(self, env):
        self.lines = queue.Queue()
        self.proc = subprocess.Popen([str(APP), '--validation-mode', 'visible', '--validation-dir', str(OUT)],
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


def bytes_per_s(text):
    try:
        number, unit = text.split()[:2]
        return float(number) * {'B/s': 1, 'KB/s': 1e3, 'MB/s': 1e6, 'GB/s': 1e9}[unit]
    except (ValueError, KeyError):
        return 0.0  # idle cells such as "—"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-webkit-'))
    env = dict(os.environ, BANDPEEK_DB_PATH=str(work / 'history.db'), BANDPEEK_SETTINGS_PATH=str(work / 'settings.json'))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results, checks = {}, []

    def check(name, ok, detail=None):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name, '' if detail is None else detail)

    names = subprocess.run(['lsappinfo', 'list'], capture_output=True, text=True).stdout
    check('Safari Networking registered with Launch Services', '"Safari Networking"' in names)
    app = App(env)
    try:
        check('app started', app.wait_for('validation-state=', 20) is not None)
        app.send('resize 840 600', 1)
        app.send('front', 3)
        app.report('main', "([...document.querySelectorAll('.segment')].find(b=>b.textContent==='Now').click(),true)")
        time.sleep(5)  # baseline sample before Safari starts
        subprocess.run(['open', '-g', '-a', 'Safari', f'http://127.0.0.1:{server.server_address[1]}/'], check=True)
        safari = None
        for _ in range(20):
            time.sleep(1)
            rows = app.report('main', ROWS) or []
            safari = next((r for r in rows if r['cells'][0] == 'Safari Networking'), None)
            if safari and bytes_per_s(safari['cells'][1]) > RATE * 0.5:
                time.sleep(4)  # past the ramp-up: read a steady sample
                rows = app.report('main', ROWS) or rows
                safari = next((r for r in rows if r['cells'][0] == 'Safari Networking'), safari)
                results['rows_busy'] = rows
                break
        check('Safari traffic listed live as "Safari Networking"', safari is not None, safari)
        if safari:
            down = bytes_per_s(safari['cells'][1])
            # Safari's other tabs share this process, so only a lower bound is exact.
            check('Safari Networking carries the served rate', down >= RATE * 0.7, f'{down:.0f} B/s vs {RATE}')
            app_row = next((r for r in rows if r['cells'][0] == 'Safari'), None)
            check('the stream is not on the Safari app row', app_row is None or bytes_per_s(app_row['cells'][1]) < RATE * 0.2,
                  app_row and app_row['cells'])
            check('tooltip names the host app', 'for Safari' in safari['title'] and 'Shared system process' in safari['title'],
                  safari['title'])
            generic = next((r for r in results.get('rows_busy', []) if r['cells'][0] == 'WebKit Networking'), None)
            check('Safari traffic is not on the generic WebKit Networking row',
                  generic is None or bytes_per_s(generic['cells'][1]) < RATE * 0.2, generic)
        for look in ('light', 'dark'):
            app.send(f'appearance {look}', 2)
            app.send('front', 2)
            app.send(f'snapshot safari-networking-{look}', 2)
        check('server sent the whole stream', served.wait(SECONDS + 15))
        results['page_reported_done'] = done.wait(5)
        results['live'] = app.report('main', "window.__TAURI_INTERNALS__.invoke('live_rates')")
        history = app.report('main', "window.__TAURI_INTERNALS__.invoke('get_history',{range:'today'})") or {}
        keep = ('identity_key', 'application_name', 'display_name', 'kind', 'host_app', 'executable_path', 'total_bytes')
        results['history'] = [{k: r.get(k) for k in keep} for r in history.get('rows', [])]
        row = next((r for r in results['history'] if r['identity_key'] == 'helper:Safari Networking'), None)
        check('history row helper:Safari Networking', row is not None, row)
        if row:
            check('history row labelled and kinded as a shared helper',
                  (row['display_name'], row['kind'], row['host_app']) == ('Safari Networking', 'shared_system_process', 'Safari'))
            check('history row holds the served bytes', row['total_bytes'] >= RATE * SECONDS * 0.9,
                  f"{row['total_bytes']} vs {RATE * SECONDS}")
            check('history row keeps the WebKit executable path',
                  (row['executable_path'] or '').endswith('/com.apple.WebKit.Networking'))
    finally:
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
