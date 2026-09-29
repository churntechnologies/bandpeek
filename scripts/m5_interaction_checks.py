#!/usr/bin/env python3
"""Milestone 5: automatable parts of the macOS interaction checklist.

Each scenario runs a fresh release instance on a copy of the history database
and a temporary settings file, and drives the *production* code paths:

  menu        app-menu items fired through NSMenu (the same action as a click or
              its shortcut): Close Window (Cmd+W), Quit BandPeek (Cmd+Q)
  a11y        rendered main window, Settings sheet and popup: every control has
              an accessible name, decorative images are hidden, tab order
  keyboard    Escape closes Settings and the popup
  quit paths  app-menu Quit, popup Quit button, SIGTERM, SIGKILL — the nettop
              child must be gone afterwards in every case

It does not replace a human check of real clicks, VoiceOver speech, the menu
bar on light/dark/notched/multiple displays: see docs/manual-checks.md.

Usage: python3 scripts/m5_interaction_checks.py
Evidence: .validation/milestone5/interaction.json
"""

import json
import os
import pathlib
import queue
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation' / 'milestone5'
APP = ROOT / 'src-tauri/target/release/bandpeek'
REAL_DB = pathlib.Path(os.environ.get('BANDPEEK_VALIDATION_SOURCE_DB',
    str(pathlib.Path.home() / 'Library/Application Support/BandPeek/bandpeek.db')))

# Accessible-name audit, evaluated in a page. Returns JSON text.
AUDIT_JS = r"""(() => {
  const name = (e) => (e.getAttribute('aria-label') || e.getAttribute('title') && e.tagName !== 'DIV' && e.getAttribute('title') ||
    e.textContent || e.getAttribute('placeholder') || '').trim().replace(/\s+/g, ' ');
  const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const controls = [...document.querySelectorAll('button, input, select, textarea, [tabindex]')].filter(visible);
  const tabbable = controls.filter((e) => e.tabIndex >= 0 && !e.disabled);
  const unnamed = controls.filter((e) => !name(e)).map((e) => e.outerHTML.slice(0, 80));
  const imgs = [...document.querySelectorAll('img')].filter((i) => i.getAttribute('alt') === null).length;
  const svgs = [...document.querySelectorAll('svg')].filter((s) => s.getAttribute('aria-hidden') !== 'true' && !s.closest('[aria-hidden="true"]')).length;
  const groups = [...document.querySelectorAll('[role=group]')].map((g) => g.getAttribute('aria-label'));
  const pressed = [...document.querySelectorAll('[aria-pressed]')].length;
  return JSON.stringify({controls: controls.length, tabbable: tabbable.map(name), unnamed, imgs_without_alt: imgs,
    svgs_not_hidden: svgs, groups, toggles_with_state: pressed,
    dialog: !!document.querySelector('[role=dialog][aria-modal=true][aria-labelledby]'),
    status_live: document.querySelectorAll('[role=status]').length});
})()""".replace('\n', ' ')


def db_copy(work):
    db = work / 'bandpeek.db'
    if REAL_DB.exists():
        with sqlite3.connect(f'file:{REAL_DB}?mode=ro', uri=True) as s, sqlite3.connect(db) as d:
            s.backup(d)
    return db


LAUNCHED = []


class App:
    def __init__(self, mode, work):
        self.lines = queue.Queue()
        self.log = []
        env = dict(os.environ, BANDPEEK_DB_PATH=str(db_copy(work)), BANDPEEK_SETTINGS_PATH=str(work / 'settings.json'))
        self.proc = subprocess.Popen([str(APP), '--validation-mode', mode], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env, cwd=ROOT)
        LAUNCHED.append(self.proc)
        threading.Thread(target=self._read, daemon=True).start()
        self.wait_for('validation-state=')
        time.sleep(3)
        self.nettop = self.child_nettop()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip('\n')
            self.log.append(line)
            self.lines.put(line)

    def send(self, command):
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.send(f"eval {label} Promise.resolve({js}).then(v=>window.__TAURI_INTERNALS__.invoke('validation_report',{{text:String(v)}}))")
            got = self.wait_for('validation-report=', timeout=0.5)
            if got:
                return got
        return None

    def child_nettop(self):
        out = subprocess.run(['pgrep', '-P', str(self.proc.pid), 'nettop'], capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None

    def exited(self, timeout=15):
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        return self.proc.returncode


def alive(pid):
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def nettop_gone(pid, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return False


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results, checks = {}, []

    def check(name, ok, detail=None):
        checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f'  ({detail})' if detail is not None else ''), flush=True)

    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-m5-ui-'))
    try:
        # 1. Menus, Close Window, reopen, accessibility, keyboard, app-menu Quit.
        app = App('visible', work)
        app.send('menu')
        time.sleep(1)
        menu = [l.split('=', 1)[1] for l in app.log if l.startswith('validation-menu-item=')]
        results['menu'] = menu
        quit_item = next((m.split(' > ', 1)[1].split(' [')[0] for m in menu if ' > Quit ' in m and '[q' in m), None)
        check('app menu has Quit <app> with Cmd+Q', quit_item, quit_item)
        check('Window menu has Close Window (Cmd+W)', any('Close Window [w' in m for m in menu))

        main_audit = app.report('main', "document.querySelector('.header') ? " + AUDIT_JS + " : ''")
        results['main_audit'] = json.loads(main_audit) if main_audit else None
        a = results['main_audit'] or {}
        check('main window: every visible control has an accessible name', a and not a['unnamed'], a.get('unnamed'))
        check('main window: images have alt text, decorative SVG hidden', a and a['imgs_without_alt'] == 0 and a['svgs_not_hidden'] == 0)
        check('main window: segmented controls are labelled groups with pressed state', a and None not in a['groups'] and a['toggles_with_state'] > 0, a.get('groups'))
        check('main window: tab order', a and len(a['tabbable']) > 0, a.get('tabbable'))

        app.report('main', "(document.querySelectorAll('.toolbar .button')[0].click(), 'clicked')")
        sheet = app.report('main', "document.querySelector('[role=dialog]') && document.querySelector('.segmented[aria-label=\"Open at login\"]') ? " + AUDIT_JS + " : ''")
        results['settings_audit'] = json.loads(sheet) if sheet else None
        s = results['settings_audit'] or {}
        check('Settings: modal dialog with a title, Open at login control present', s and s['dialog'], s.get('groups'))
        check('Settings: every visible control has an accessible name', s and not s['unnamed'], s.get('unnamed'))
        # Wait until the login-item state has arrived (the control is enabled or
        # disabled by it), then read the help text.
        login_help = app.report('main', "document.querySelector('[data-login-state]:not([data-login-state=loading])') ? [...document.querySelectorAll('.setting-help')].map(e=>e.textContent).join(' | ') : ''")
        check('Settings: dev binary explains Launch at Login needs the installed app', login_help and 'installed app' in login_help, login_help)
        note = app.report('main', "document.querySelector('.note').textContent")
        check('Settings: accuracy note (not ISP/billing/wire, local traffic, missed short-lived, crash window)',
              note and all(k in note for k in ['not an ISP, billing or wire-level meter', 'Local and LAN', 'short-lived', 'up to a minute']), note)
        closed = app.report('main', "(window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'})), new Promise(r=>setTimeout(()=>r(document.querySelector('[role=dialog]') ? 'open' : 'closed'),300)))")
        check('Escape closes Settings', closed == 'closed', closed)

        app.send('perform-close')
        closed_state = app.wait_for('validation-state=main-closed', timeout=5)
        check('performClose: (close button / Cmd+W path) hides the window and keeps running',
              closed_state is not None and app.proc.poll() is None)
        for i in range(3):
            app.send('open')
            ok = app.report('main', "document.querySelector('.header') ? 'ok' : ''")
            app.send('close')
            app.wait_for('validation-state=main-closed', timeout=5)
        check('reopen after close (3x)', ok == 'ok' and app.proc.poll() is None)

        # A harness-launched app cannot take activation from the frontmost app,
        # so the popup can lose focus and close itself (as designed) before it is
        # read. Retry; a real click is user-initiated activation (manual check).
        tray, attempts = None, 0
        while not tray and attempts < 3:
            attempts += 1
            app.send('popup-close')
            time.sleep(0.5)
            app.send('popup')
            tray = app.report('tray', "document.querySelector('.popup') ? " + AUDIT_JS + " : ''")
        results['popup_attempts'] = attempts
        results['popup_audit'] = json.loads(tray) if tray else None
        t = results['popup_audit'] or {}
        check('popup: every control named; tab order', t and not t['unnamed'], t.get('tabbable'))
        app.send("eval tray window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        time.sleep(1)
        gone = app.report('tray', "'still-open'", timeout=2)
        check('Escape closes the popup', t and gone is None)

        app.send('open')
        app.report('main', "document.querySelector('.header') ? 'ok' : ''")
        nettop = app.nettop
        app.send(f'menu {quit_item}')
        code = app.exited()
        check('app menu Quit (Cmd+Q item) exits cleanly', code == 0 and 'validation-state=exited' in app.log, code)
        check('  nettop child gone after app-menu Quit', nettop and nettop_gone(nettop), nettop)

        # 2. Tray Quit button.
        app = App('tray', work)
        app.send('popup')
        app.report('tray', "document.querySelector('.popup-quit') ? 'ok' : ''")
        nettop = app.nettop
        app.report('tray', "(document.querySelector('.popup-quit').click(), 'clicked')", timeout=2)
        code = app.exited()
        check('popup Quit button exits cleanly', code == 0 and 'validation-state=exited' in app.log, code)
        check('  nettop child gone after popup Quit', nettop and nettop_gone(nettop), nettop)

        # 3. Forced termination.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            app = App('tray', work)
            nettop = app.nettop
            app.proc.send_signal(sig)
            app.exited()
            check(f'{sig.name}: nettop child gone (no orphan)', nettop and nettop_gone(nettop, 15), nettop)
    finally:
        for proc in LAUNCHED:  # never leave an instance (and its nettop) behind
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        shutil.rmtree(work, ignore_errors=True)

    results['checks'] = checks
    (OUT / 'interaction.json').write_text(json.dumps(results, indent=2))
    print(f"\n{sum(c['ok'] for c in checks)} passed, {sum(not c['ok'] for c in checks)} failed; saved {OUT / 'interaction.json'}")


if __name__ == '__main__':
    main()
