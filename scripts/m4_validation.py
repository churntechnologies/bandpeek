#!/usr/bin/env python3
"""Milestone 4 functional validation of the final UI against real data.

Runs the release app in validation mode on a *copy* of the user's history
database (so clear-history never touches real data), drives the real UI through
stdin commands, reads back what the pages rendered, and compares it with the
backend's history view and with independent SQLite queries.

Usage: python3 scripts/m4_validation.py [--db PATH]
Evidence: .validation/milestone4/validation.json (+ snapshots).
"""

import argparse
import json
import math
import os
import pathlib
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation' / 'milestone4'
APP = ROOT / 'src-tauri/target/release/bandpeek'
REAL_DB = pathlib.Path.home() / 'Library/Application Support/BandPeek/bandpeek.db'

RANGES = ['today', 'yesterday', 'last_7_days', 'last_30_days']
RANGE_LABELS = {'today': 'Today', 'yesterday': 'Yesterday', 'last_7_days': 'Last 7 Days', 'last_30_days': 'Last 30 Days'}


def fmt(b, binary=False):
    """Python port of the design's formatter (specs/components.md)."""
    base = 1024 if binary else 1000
    units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'] if binary else ['B', 'KB', 'MB', 'GB', 'TB']
    if not b or b <= 0:
        return f'0 {units[1]}'
    i = min(4, max(1, math.floor(math.log(b) / math.log(base))))
    v = b / base ** i
    n = f'{v:.0f}' if v >= 100 else f'{v:.1f}' if v >= 10 else f'{v:.2f}'
    return f'{n} {units[i]}'


def pct(share):
    return '<1%' if share < 1 else f'{math.floor(share + 0.5)}%'  # JS Math.round


class App:
    def __init__(self, env, mode='visible'):
        self.lines = queue.Queue()
        self.log = []
        self.proc = subprocess.Popen(
            [str(APP), '--validation-mode', mode, '--validation-log', '--validation-dir', str(OUT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=(OUT / 'validation.stderr').open('w'),
            text=True, env=env, cwd=ROOT)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            line = line.rstrip('\n')
            if line.startswith('{'):
                self.last_snapshot = line
                continue
            self.log.append(line)
            self.lines.put(line)

    def send(self, command):
        self.proc.stdin.write(command + '\n')
        self.proc.stdin.flush()

    def wait_for(self, prefix, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line[len(prefix):]
        raise TimeoutError(prefix)

    def report(self, script, label='main', timeout=15):
        """Runs `script` (an async JS expression) in a page; returns its JSON value."""
        while not self.lines.empty():
            self.lines.get_nowait()
        js = ("(async()=>{let r;try{r=await (" + script + ")}catch(e){r={error:String(e)}}"
              "window.__TAURI_INTERNALS__.invoke('validation_report',{text:JSON.stringify(r)})})()")
        self.send(f'eval {label} ' + ' '.join(js.split('\n')))
        return json.loads(self.wait_for('validation-report=', timeout))

    def snapshot_state(self):
        return json.loads(self.last_snapshot)


READ_TABLE = """(async()=>{
  const q=s=>document.querySelector(s);
  const rows=[...document.querySelectorAll('.tbody .row')].map(r=>{const c=r.children;return{
    name:c[0].innerText.trim(), icon:!!c[0].querySelector('img'), d:c[1].innerText, u:c[2].innerText,
    t:c[3].innerText, pct:c[4].innerText.trim(), bar:[...c[4].querySelectorAll('.bar div')].map(x=>x.style.width)}});
  const stats=[...document.querySelectorAll('.stat-value')].map(x=>x.innerText.replace(/\\s+/g,' '));
  return {rows, stats, period:q('.period').innerText, empty:q('.empty')?.innerText??null,
    status:q('.statusbar').innerText.replace(/\\s+/g,' '),
    selected:q('.segment[aria-pressed=true]').innerText,
    sorted:q('.sort[data-active=true]').innerText,
    bg:getComputedStyle(document.body).backgroundColor,
    dark:matchMedia('(prefers-color-scheme: dark)').matches, theme:document.documentElement.dataset.theme??'system',
    view: await window.__TAURI_INTERNALS__.invoke('get_history',{range:%s})};
})()"""


def click(app, selector_js):
    app.report(f"(async()=>{{{selector_js}.click();await new Promise(r=>setTimeout(r,700));return 1}})()")


def set_query(app, text):
    app.report("(async()=>{const i=document.querySelector('.filter');"
               "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i,%s);"
               "i.dispatchEvent(new Event('input',{bubbles:true}));await new Promise(r=>setTimeout(r,400));return 1})()"
               % json.dumps(text))


def read(app, rng, binary=False):
    """Refreshes the page through its own `history-changed` path, then reads the
    DOM and a backend view within the same collector frame. Retries when a 5 s
    frame lands between the page's fetch and ours (values then legitimately differ)."""
    for attempt in range(4):
        app.report("(async()=>{await window.__TAURI_INTERNALS__.invoke('plugin:event|emit',"
                   "{event:'history-changed',payload:null});await new Promise(r=>setTimeout(r,500));return 1})()")
        page = app.report(READ_TABLE % json.dumps(rng))
        live = [r for r in page['view']['rows'] if r['total_bytes'] > 0]
        rendered = {r['name']: r['t'] for r in page['rows']}
        if all(rendered.get(r['application_name'], fmt(r['total_bytes'], binary)) == fmt(r['total_bytes'], binary) for r in live):
            break
    page['attempts'] = attempt + 1
    return page


def sql_totals(db, start, end):
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
        rows = c.execute("""SELECT a.identity_key, SUM(t.download_bytes), SUM(t.upload_bytes)
            FROM traffic_buckets t JOIN apps a ON a.id=t.app_id
            WHERE t.bucket_start_utc>=? AND t.bucket_start_utc<? GROUP BY a.identity_key""", (start, end)).fetchall()
    return {k: (d, u) for k, d, u in rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(REAL_DB))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    work = OUT / 'validation-db'
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    db = work / 'bandpeek.db'
    # Consistent copy of the live database (WAL-safe).
    with sqlite3.connect(f'file:{args.db}?mode=ro', uri=True) as src, sqlite3.connect(db) as dst:
        src.backup(dst)
    env = dict(os.environ, BANDPEEK_DB_PATH=str(db), BANDPEEK_SETTINGS_PATH=str(work / 'settings.json'))
    results = {'checks': [], 'db_copy': str(db)}

    def check(name, ok, detail=None):
        results['checks'].append({'name': name, 'ok': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name + (f' — {detail}' if detail and not ok else ''))

    app = App(env)
    app.wait_for('validation-state=visible', 30)
    time.sleep(12)  # baseline + first frames

    # 1. Ranges: rendered values == backend view == independent SQL.
    for rng in RANGES:
        click(app, f"[...document.querySelectorAll('.segment')].find(b=>b.innerText==={json.dumps(RANGE_LABELS[rng])})")
        page = read(app, rng)
        view = page['view']
        sql = sql_totals(db, view['range_start_utc'], view['range_end_utc'])
        backend = {r['identity_key']: (r['download_bytes'], r['upload_bytes']) for r in view['rows']}
        # Past ranges are immutable: exact. Ranges including now may gain a flush in between: monotonic.
        if rng == 'yesterday':
            sql_ok = sql == backend
        else:
            sql_ok = all(k in sql and sql[k][0] >= d and sql[k][1] >= u for k, (d, u) in backend.items())
        check(f'{rng}: backend history equals independent SQLite aggregation', sql_ok,
              {'backend_apps': len(backend), 'sql_apps': len(sql)})
        live_rows = [r for r in view['rows'] if r['total_bytes'] > 0]
        expected = [[r['application_name'], fmt(r['download_bytes']), fmt(r['upload_bytes']), fmt(r['total_bytes']), pct(r['share_percent'])]
                    for r in live_rows]
        got = [[r['name'], r['d'], r['u'], r['t'], r['pct']] for r in page['rows']]
        check(f'{rng}: table rows/values/share match backend ({len(got)} apps)', got == expected,
              {'first_mismatch': next(((g, e) for g, e in zip(got, expected) if g != e), None), 'lens': (len(got), len(expected))})
        s = view['summary']
        check(f'{rng}: summary downloaded/uploaded/total', page['stats'] == [fmt(s['download_bytes']), fmt(s['upload_bytes']), fmt(s['total_bytes'])],
              page['stats'])
        check(f'{rng}: summary total equals sum of rows',
              s['total_bytes'] == sum(r['total_bytes'] for r in view['rows']) and s['download_bytes'] + s['upload_bytes'] == s['total_bytes'])
        shares = sum(r['share_percent'] for r in live_rows)
        check(f'{rng}: share percentages sum to 100', not live_rows or abs(shares - 100) < 1e-6, shares)
        check(f'{rng}: period label + app count', page['period'].endswith(f"· {len(live_rows)} app{'s' if len(live_rows) != 1 else ''}") or (not live_rows and page['period'].endswith('0 apps')), page['period'])
        check(f'{rng}: selected segment', page['selected'] == RANGE_LABELS[rng], page['selected'])
        results[f'range_{rng}'] = {'period': page['period'], 'stats': page['stats'], 'apps': len(got),
                                   'range': [view['range_start_utc'], view['range_end_utc']]}

    # 2. Sorting (on Last 30 Days, the richest range).
    click(app, "[...document.querySelectorAll('.segment')].find(b=>b.innerText==='Last 30 Days')")
    for key, label, field in [('download', 'Download', 'download_bytes'), ('upload', 'Upload', 'upload_bytes'), ('total', 'Total', 'total_bytes')]:
        click(app, f"[...document.querySelectorAll('.sort')].find(b=>b.innerText.endsWith({json.dumps(label)}))")
        page = read(app, 'last_30_days')
        by_name = {r['application_name']: r for r in page['view']['rows']}
        values = [by_name[r['name']][field] for r in page['rows']]
        check(f'sort by {label}: descending, header marked', values == sorted(values, reverse=True) and page['sorted'] == f'↓ {label}',
              {'sorted_header': page['sorted']})
    ui_state = app.report("window.__TAURI_INTERNALS__.invoke('get_ui_state')")
    check('sort/range persisted natively for window recreation', ui_state['sort'] == 'total' and ui_state['range'] == 'last_30_days', ui_state)

    # 3. Filtering: case-insensitive substring on the app name.
    page = read(app, 'last_30_days')
    names = [r['name'] for r in page['rows']]
    probe = next((n for n in names if len(n) >= 3), names[0] if names else 'x')
    needle = probe[1:3].upper()
    set_query(app, needle)
    page = read(app, 'last_30_days')
    expected = [n for n in names if needle.lower() in n.lower()]
    check(f'filter "{needle}" (case-insensitive substring)', [r['name'] for r in page['rows']] == expected, [r['name'] for r in page['rows']])
    set_query(app, 'zzqqxx-no-such-app')
    page = read(app, 'last_30_days')
    check('filter with no matches shows empty state', page['rows'] == [] and page['empty'] == 'No apps match “zzqqxx-no-such-app”', page['empty'])
    set_query(app, '')

    # 4. App icons: real bundle icons for bundles, neutral fallback otherwise.
    page = read(app, 'last_30_days')
    key_by_name = {r['application_name']: r['identity_key'] for r in page['view']['rows']}
    bundle_icons = [r['icon'] for r in page['rows'] if key_by_name[r['name']].startswith('bundle:')]
    other_icons = [r['icon'] for r in page['rows'] if not key_by_name[r['name']].startswith('bundle:')]
    check('bundle apps show a real icon', bundle_icons and sum(bundle_icons) >= len(bundle_icons) - 1,
          f'{sum(bundle_icons)}/{len(bundle_icons)} bundle rows with icons')
    check('CLI/background processes use the neutral fallback', not any(other_icons), f'{sum(other_icons)}/{len(other_icons)} non-bundle rows with icons')
    results['icons'] = {'bundle_with_icon': sum(bundle_icons), 'bundle_rows': len(bundle_icons), 'fallback_rows': len(other_icons)}

    # 5. Units: GB ↔ GiB applies everywhere.
    app.send('units binary')
    app.wait_for('validation-settings=')
    time.sleep(0.6)
    page = read(app, 'last_30_days', binary=True)
    exp = [fmt(r['total_bytes'], True) for r in page['view']['rows'] if r['total_bytes'] > 0]
    check('GiB switching: rows and status bar', [r['t'] for r in page['rows']] == exp and 'Binary units (GiB)' in page['status'], page['status'])
    app.send('snapshot units-gib')
    time.sleep(1)
    app.send('units decimal')
    app.wait_for('validation-settings=')
    time.sleep(0.6)
    page = read(app, 'last_30_days')
    check('GB switching back', 'Decimal units (GB)' in page['status'] and all(r['t'].endswith(('KB', 'MB', 'GB', 'TB')) for r in page['rows']))

    # 6. Appearance: explicit light/dark tokens, and system follows the OS.
    for mode, bg in [('light', 'rgb(253, 253, 252)'), ('dark', 'rgb(28, 29, 31)')]:
        app.send(f'appearance {mode}')
        app.wait_for('validation-settings=')
        time.sleep(0.8)
        page = read(app, 'today')
        check(f'appearance {mode}: window background token', page['bg'] == bg and page['theme'] == mode, page['bg'])
        app.send(f'snapshot main-{mode}')
        time.sleep(1.2)
    app.send('appearance system')
    app.wait_for('validation-settings=')
    time.sleep(0.8)
    page = read(app, 'today')
    system_bg = 'rgb(28, 29, 31)' if page['dark'] else 'rgb(253, 253, 252)'
    check(f"appearance system follows OS ({'dark' if page['dark'] else 'light'})", page['bg'] == system_bg and page['theme'] == 'system', page['bg'])

    # 7. Tray popup: live speeds, Today total, top 5 == backend Today.
    app.send('popup')
    time.sleep(1.0)
    # Wait for the popup's own first history load to render.
    app.report("(async()=>{for(let i=0;i<50&&!document.querySelector('.popup-app,.popup-empty');i++)"
               "await new Promise(r=>setTimeout(r,100));return 1})()", label='tray')
    tray = app.report("""(async()=>({
      today:document.querySelector('.popup-today-total').innerText,
      parts:[...document.querySelectorAll('.popup-today > .muted')].map(x=>x.innerText),
      apps:[...document.querySelectorAll('.popup-app')].map(r=>[r.querySelector('.app-name').innerText, r.querySelector('.num').innerText]),
      speeds:[...document.querySelectorAll('.popup-speed-value')].map(x=>x.innerText.replace(/\\s+/g,' ')),
      buttons:[...document.querySelectorAll('button')].map(b=>b.innerText),
      view: await window.__TAURI_INTERNALS__.invoke('get_history',{range:'today'})}))()""", label='tray')
    tv = tray['view']
    top = [[r['application_name'], fmt(r['total_bytes'])] for r in tv['rows'] if r['total_bytes'] > 0][:5]
    check('tray Today total and ↓/↑', tray['today'] == fmt(tv['summary']['total_bytes'])
          and tray['parts'] == ['Today', f"↓ {fmt(tv['summary']['download_bytes'])}", f"↑ {fmt(tv['summary']['upload_bytes'])}"], tray)
    check('tray top 5 apps today', tray['apps'] == top, {'got': tray['apps'], 'expected': top})
    check('tray actions are exactly Open BandPeek / Quit (no Pause)', tray['buttons'] == ['Open BandPeek', 'Quit'], tray['buttons'])
    app.send('snapshot tray')
    time.sleep(1.5)
    app.send('popup-close')
    results['tray'] = tray

    # 8. Close main window → collection continues; reopen → data intact.
    before = read(app, 'today')['view']['summary']['total_bytes']
    seq_before = app.snapshot_state()['sample_sequence']
    app.send('close')
    app.wait_for('validation-state=main-closed')
    # Known traffic while the UI is closed (1 MiB public object, kept alive by the socket timing).
    try:
        urllib.request.urlopen('https://speed.cloudflare.com/__down?bytes=1048576', timeout=20).read()
        downloaded = True
    except Exception as e:  # noqa: BLE001
        downloaded = str(e)
    time.sleep(20)
    seq_after = app.snapshot_state()['sample_sequence']
    check('collector keeps sampling while the window is closed', seq_after >= seq_before + 3, {'before': seq_before, 'after': seq_after})
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
        n = c.execute('SELECT COUNT(*) FROM traffic_buckets WHERE bucket_start_utc >= ?', (int(time.time()) - 120,)).fetchone()[0]
    app.send('open')
    app.wait_for('validation-state=main-open')
    time.sleep(3)
    page = read(app, 'today')
    after = page['view']['summary']['total_bytes']
    check('reopened window shows history including traffic while closed', after > before,
          {'before': before, 'after': after, 'download': downloaded, 'recent_buckets': n})
    check('reopened window restored view state (range)', page['selected'] == 'Today' or page['selected'] == RANGE_LABELS.get(ui_state['range']))

    # 9. Clear history (database copy only).
    before_clear = read(app, 'last_30_days')['view']['summary']['total_bytes']
    cleared_at = int(time.time())
    app.report("window.__TAURI_INTERNALS__.invoke('clear_history')")
    time.sleep(1)
    page = read(app, 'last_30_days')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
        oldest, gaps = c.execute('SELECT MIN(bucket_start_utc), (SELECT COUNT(*) FROM monitoring_gaps) FROM traffic_buckets').fetchone()
    # Collection continues after a clear, so only traffic from the current minute may exist.
    after_clear = page['view']['summary']['total_bytes']
    check('clear history removes all earlier history (store and UI)',
          (oldest is None or oldest >= cleared_at - cleared_at % 60) and gaps == 0 and after_clear < before_clear
          and page['stats'][2] == fmt(after_clear),
          {'before': before_clear, 'after': after_clear, 'oldest_bucket': oldest, 'cleared_at': cleared_at})
    app.send('snapshot after-clear')

    # 10. Retention setting persists; Quit shuts the collector down cleanly.
    app.send('retention 180')
    app.wait_for('validation-settings=')
    collector = app.snapshot_state().get('collector_pid')
    app.send('quit')
    app.wait_for('validation-state=exited', 20)
    code = app.proc.wait(timeout=20)
    alive = subprocess.run(['ps', '-p', str(collector)], capture_output=True).returncode == 0 if collector else False
    check('quit exits cleanly (code 0) and stops nettop', code == 0 and not alive, {'code': code, 'collector_pid': collector, 'collector_alive': alive})
    saved = json.loads((work / 'settings.json').read_text())
    check('settings persisted to disk', saved['retention_days'] == 180 and saved['appearance'] == 'system' and saved['units'] == 'decimal', saved)

    results['passed'] = sum(c['ok'] for c in results['checks'])
    results['failed'] = sum(not c['ok'] for c in results['checks'])
    (OUT / 'validation.json').write_text(json.dumps(results, indent=2))
    print(f"\n{results['passed']} passed, {results['failed']} failed → {OUT / 'validation.json'}")


if __name__ == '__main__':
    main()
