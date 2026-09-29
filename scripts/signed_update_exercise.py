#!/usr/bin/env python3
"""Actual signed macOS updater validation on disposable bundles and fresh data.

Requires the loopback-only HTTPS fixture server and normally trusted TLS.
Never reads updater signing secrets or any real BandPeek database/settings.
Raw evidence remains under the git-ignored .validation directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import threading
import time

from m5_performance import ps_table

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / '.validation/signed-updater'
SETTINGS = {'appearance': 'dark', 'units': 'binary', 'retention_days': 365,
            'menu_bar_display': 'icon_and_speeds'}


def wait(test, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = test()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('Timed out waiting for validation condition')


def children(pid):
    return [p for p, row in ps_table().items()
            if row['ppid'] == pid and '/usr/bin/nettop' in row['command']]


def version(bundle):
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        return plistlib.load(stream)['CFBundleShortVersionString']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database(path):
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        return {
            'integrity': db.execute('PRAGMA integrity_check').fetchone()[0],
            'totals': list(db.execute('SELECT coalesce(sum(download_bytes),0), coalesce(sum(upload_bytes),0) FROM traffic_buckets').fetchone()),
            'sentinel': list(db.execute("SELECT download_bytes,upload_bytes FROM traffic_buckets JOIN apps ON app_id=apps.id WHERE identity_key='validation:synthetic'").fetchone() or []),
            'gaps': list(db.execute('SELECT generation,reason FROM monitoring_gaps')),
        }


class App:
    def __init__(self, case, port):
        self.work = FIXTURES / 'runs' / f'{case}-{time.time_ns()}'
        self.work.mkdir(parents=True)
        self.bundle = self.work / 'BandPeek.app'
        subprocess.run(['/usr/bin/ditto', str(FIXTURES / 'baseline/BandPeek.app'), str(self.bundle)], check=True)
        self.binary = self.bundle / 'Contents/MacOS/bandpeek'
        self.data = self.work / 'data'
        self.data.mkdir()
        self.db = self.data / 'history.sqlite'
        self.settings = self.data / 'settings.json'
        self.settings.write_text(json.dumps(SETTINGS))
        self.stdout = self.work / 'stdout.log'
        self.stderr = self.work / 'stderr.log'
        env = dict(os.environ, BANDPEEK_DB_PATH=str(self.db), BANDPEEK_SETTINGS_PATH=str(self.settings))
        # Do not inherit any signing material or an old validation output path.
        for name in ('TAURI_SIGNING_PRIVATE_KEY', 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD',
                     'TAURI_SIGNING_PRIVATE_KEY_PATH', 'BANDPEEK_VALIDATION_OUT'):
            env.pop(name, None)
        self.proc = subprocess.Popen(
            [str(self.binary), '--validation-mode', 'auto', '--validation-log', '--interval', '1',
             '--validation-update-endpoint', f'https://localhost:{port}/{case}/latest.json'],
            stdin=subprocess.PIPE, stdout=self.stdout.open('w'), stderr=self.stderr.open('w'),
            text=True, env=env, cwd=ROOT)
        self.wait_line('validation-state=visible')
        try:
            wait(lambda: self.report("document.querySelector('.header')?'ready':''") == 'ready')
        except Exception:
            self.send('quit')
            self.proc.wait(timeout=20)
            raise
        wait(lambda: len(children(self.proc.pid)) == 1)
        with sqlite3.connect(self.db) as db:
            now = int(time.time())
            db.execute('INSERT INTO apps(identity_key,application_name,first_seen_utc,last_seen_utc) VALUES(?,?,?,?)',
                       ('validation:synthetic', 'Synthetic validation history', now-3600, now))
            app_id = db.execute("SELECT id FROM apps WHERE identity_key='validation:synthetic'").fetchone()[0]
            db.execute('INSERT INTO traffic_buckets VALUES(?,?,?,?)', ((now-3600)//60*60, app_id, 12345, 6789))
        assert self.invoke('get_settings') == SETTINGS
        self.before_settings = digest(self.settings)

    def pids(self):
        return [pid for pid, row in ps_table().items() if row['command'].startswith(str(self.binary))]

    def send(self, line):
        # Tauri restart inherits the same stdin; its original Popen has exited.
        self.proc.stdin.write(line + '\n')
        self.proc.stdin.flush()

    def mark(self):
        return len(self.stdout.read_text())

    def wait_line(self, prefix, mark=0, seconds=30):
        def read():
            lines = self.stdout.read_text()[mark:].splitlines()
            return next((line[len(prefix):] or True for line in lines if line.startswith(prefix)), None)
        return wait(read, seconds)

    def report(self, expression):
        mark = self.mark()
        self.send("eval main window.__TAURI_INTERNALS__.invoke('validation_report',{text:'result='+String(" + expression + ")})")
        return self.wait_line('validation-report=result=', mark)

    def invoke(self, command, args=None):
        mark = self.mark()
        self.send(f"eval main window.__TAURI_INTERNALS__.invoke({json.dumps(command)},{json.dumps(args or {})}).then(x=>window.__TAURI_INTERNALS__.invoke('validation_report',{{text:'invoke='+JSON.stringify(x)}})).catch(e=>window.__TAURI_INTERNALS__.invoke('validation_report',{{text:'invoke-error='+String(e)}}))")
        return json.loads(self.wait_line('validation-report=invoke=', mark))

    def phase(self):
        mark = self.mark()
        self.send('update-state')
        return self.wait_line('validation-update-phase=', mark)

    def snapshot(self):
        lines = self.stdout.read_text().splitlines()
        for line in reversed(lines):
            if line.startswith('{"status":'):
                return json.loads(line)
        return None

    def check(self):
        self.send('update-check')

    def close(self):
        mark = self.mark()
        self.send('close')
        self.wait_line('validation-state=main-closed', mark)

    def open(self):
        self.send('open')
        wait(lambda: self.report("document.querySelector('.header')?'ready':''") == 'ready')

    def cleanup(self):
        if self.pids():
            self.open()
            state = self.invoke('set_login_item', {'enabled': False})
            assert state['state'] == 'disabled' and not state['error'], state
            self.send('quit')
            wait(lambda: not self.pids())
        self.proc.wait(timeout=20)
        assert not any(str(self.work) in row['command'] for row in ps_table().values())


def run(case, port):
    if case == 'sqlite-flush':
        (FIXTURES / 'sqlite-flush-ready').unlink(missing_ok=True)
    app = App(case, port)
    result = {'case': case, 'data_isolated': True, 'evidence': str(app.work.relative_to(ROOT))}
    try:
        old_pid = app.proc.pid
        old_children = children(old_pid)
        assert len(old_children) == 1
        before_hash = digest(app.binary)
        before_inode = app.bundle.stat().st_ino
        off = app.invoke('set_login_item', {'enabled': False})
        assert off['state'] == 'disabled' and not off['error'], off
        if case in ('no-update', 'bad-signature', 'bad-archive'):
            app.check()
            log = app.data / 'updates.log'
            expected = 'no newer release' if case == 'no-update' else 'check/download failed'
            wait(lambda: log.exists() and expected in log.read_text())
            assert app.phase() == ('Idle' if case == 'no-update' else 'Failed')
            if case != 'no-update':
                assert 'signature' in log.read_text().lower(), log.read_text()
            assert app.proc.poll() is None and version(app.bundle) == '0.1.0-beta.0'
            assert digest(app.binary) == before_hash and children(old_pid) == old_children
            assert digest(app.settings) == app.before_settings
            result['result'] = 'no update' if case == 'no-update' else 'download rejected before installation'
            result['bundle_and_collector_unchanged'] = True
            result['phase'] = app.phase()
        elif case == 'install-failure':
            wait(lambda: app.snapshot() and app.snapshot()['sample_sequence'] >= 3)
            before = app.snapshot()
            app.check()
            wait(lambda: app.phase() == 'Ready')
            app.close()
            wait(lambda: app.phase() == 'Failed')
            wait(lambda: children(old_pid) and children(old_pid) != old_children)
            wait(lambda: app.snapshot()['collector_generation'] > before['collector_generation']
                 and app.snapshot()['sample_sequence'] > before['sample_sequence'])
            after = app.snapshot()
            assert len(children(old_pid)) == 1
            assert all(pid not in ps_table() for pid in old_children)
            assert after['session_bytes']['download'] >= before['session_bytes']['download']
            assert after['session_bytes']['upload'] >= before['session_bytes']['upload']
            assert digest(app.binary) == before_hash and version(app.bundle) == '0.1.0-beta.0'
            assert app.bundle.stat().st_ino != before_inode, 'Independent backup was not restored'
            assert not list(app.work.glob('.BandPeek-update-backup-*'))
            assert not (app.data / 'update-relaunch').exists()
            saved = database(app.db)
            assert saved['integrity'] == 'ok' and saved['sentinel'] == [12345, 6789]
            assert any('Update preparation paused collector' in reason for _, reason in saved['gaps'])
            result.update(result='official installer failed; backup restored; current collector resumed',
                          old_collector=old_children[0], new_collector=children(old_pid)[0],
                          generation=[before['collector_generation'], after['collector_generation']],
                          sequence=[before['sample_sequence'], after['sample_sequence']],
                          session_totals_retained=True, sqlite=saved)
        else:
            login = case == 'login-enabled'
            if login:
                state = app.invoke('set_login_item', {'enabled': True})
                assert state['state'] == 'enabled' and not state['error'], state
            if case == 'open-close':
                app.check()
                wait(lambda: app.phase() == 'Ready')
                time.sleep(3)
                assert app.proc.poll() is None and digest(app.binary) == before_hash
                mark = app.mark()
                app.send('menu Minimize')
                app.wait_line('validation-menu-perform=Minimize enabled=true', mark)
                wait(lambda: app.invoke('plugin:window|is_minimized', {'label': 'main'}) is True)
                time.sleep(3)
                assert app.phase() == 'Ready' and children(old_pid) == old_children
                assert version(app.bundle) == '0.1.0-beta.0'
                result['open_and_minimized_remain_deferred'] = True
            else:
                app.close()
            buffered = None
            if case == 'sqlite-flush':
                # Begin early in a minute, after the normal periodic flush.
                # With no WebView, no history query can flush these new deltas.
                wait(lambda: time.time() % 60 < 10, seconds=65)
                time.sleep(2)
                subprocess.run(['curl', '--fail', '--silent', '--show-error', '--limit-rate', '2m',
                                f'https://localhost:{port}/traffic.bin', '-o', '/dev/null'], check=True)
                time.sleep(2)
                captured = app.snapshot()
                stored = database(app.db)
                session = captured['session_bytes']
                buffered = [session['download'] - (stored['totals'][0] - 12345),
                            session['upload'] - (stored['totals'][1] - 6789)]
                assert max(buffered) > 1024, 'Expected actual observations buffered before update'
                result['buffered_observations_before_install'] = buffered
                result['session_bytes_before_install'] = session
                (FIXTURES / 'sqlite-flush-ready').write_text('offer genuine signed update now\n')
            # Capture an independent SQLite read at the first observed bundle
            # replacement, before the relaunch can produce a history read.
            at_replacement = {}
            def observe():
                until = time.monotonic() + 40
                while time.monotonic() < until:
                    try:
                        changed = app.bundle.stat().st_ino != before_inode
                    except FileNotFoundError:
                        changed = False
                    if changed:
                        at_replacement.update(database(app.db))
                        at_replacement['old_collector_gone'] = all(p not in ps_table() for p in old_children)
                        return
                    time.sleep(.005)
            observer = threading.Thread(target=observe, daemon=True)
            mark = app.mark()
            before_db = database(app.db)
            observer.start()
            if case == 'open-close':
                app.close()
            else:
                app.check()
            app.proc.wait(timeout=40)
            assert app.proc.returncode == 0
            new_pid = wait(lambda: next((p for p in app.pids() if p != old_pid), None))
            app.wait_line('validation-state=tray', mark)
            wait(lambda: len(children(new_pid)) == 1)
            observer.join(timeout=5)
            assert at_replacement['integrity'] == 'ok'
            assert at_replacement['sentinel'] == [12345, 6789]
            assert at_replacement['old_collector_gone']
            if buffered is not None:
                assert at_replacement['totals'][0] >= result['session_bytes_before_install']['download'] + 12345
                assert at_replacement['totals'][1] >= result['session_bytes_before_install']['upload'] + 6789
                result['buffered_observations_flushed_before_replacement'] = True
            assert all(p not in ps_table() for p in old_children)
            assert len(app.pids()) == 1 and len(children(new_pid)) == 1
            assert version(app.bundle) == '0.1.0-beta.1'
            assert digest(app.binary) == digest(FIXTURES / 'signed-target/BandPeek.app/Contents/MacOS/bandpeek')
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app.bundle)], check=True)
            assert not (app.data / 'update-relaunch').exists()
            assert digest(app.settings) == app.before_settings
            assert not list(app.work.glob('.BandPeek-update-backup-*'))
            # Tray-only startup is observed before deliberately opening a view.
            tray_output = app.stdout.read_text()[mark:]
            assert 'validation-state=main-open' not in tray_output
            app.open()
            state = app.invoke('get_login_item')
            assert state['state'] == ('enabled' if login else 'disabled'), state
            assert app.invoke('get_settings') == SETTINGS
            off = app.invoke('set_login_item', {'enabled': False})
            assert off['state'] == 'disabled' and not off['error'], off
            saved = database(app.db)
            assert saved['integrity'] == 'ok' and saved['sentinel'] == [12345, 6789]
            assert all(new >= old for new, old in zip(saved['totals'], before_db['totals']))
            result.update(result='signed replacement and tray-only relaunch passed',
                          old_pid=old_pid, new_pid=new_pid, old_collector=old_children[0],
                          new_collector=children(new_pid)[0], exactly_one_collector=True,
                          sqlite_at_replacement=at_replacement, sqlite_after=saved,
                          settings_preserved=True, login_preserved=state['state'], login_final=off['state'])
        print(f'{case}: {result["result"]}', flush=True)
        return result
    finally:
        app.cleanup()
        if case == 'sqlite-flush':
            (FIXTURES / 'sqlite-flush-ready').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18443)
    parser.add_argument('--case', choices=['no-update', 'bad-signature', 'bad-archive', 'install-failure', 'open-close', 'tray-disabled', 'login-enabled', 'sqlite-flush'])
    args = parser.parse_args()
    cases = [args.case] if args.case else ['no-update', 'bad-signature', 'bad-archive', 'install-failure', 'open-close', 'tray-disabled', 'login-enabled', 'sqlite-flush']
    results = []
    result_path = FIXTURES / ('runtime-' + (args.case or 'all') + '.json')
    try:
        for case in cases:
            results.append(run(case, args.port))
            result_path.write_text(json.dumps(results, indent=2) + '\n')
    finally:
        result_path.write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
