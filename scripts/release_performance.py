#!/usr/bin/env python3
"""Final status-mode measurements: 30 s settle + 60 s each, isolated data.
Same CPU/RSS method and helper attribution as Milestone 5. No concurrent loads.
"""
import argparse
import json
import os
import pathlib
import shutil
import tempfile
import time
from m5_window_cycles import App, sqlite_backup
from m5_performance import measure, summary

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / '.validation/release'
REAL_DB = pathlib.Path(os.environ.get('BANDPEEK_VALIDATION_SOURCE_DB',
    str(pathlib.Path.home()/'Library/Application Support/BandPeek/bandpeek.db')))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--visible-only', action='store_true', help='Resume the visible sample after an occlusion failure; retain completed tray samples')
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-release-perf-'))
    db = work/'bandpeek.db'
    if REAL_DB.exists(): sqlite_backup(REAL_DB, db)
    env = dict(os.environ, BANDPEEK_DB_PATH=str(db), BANDPEEK_SETTINGS_PATH=str(work/'settings.json'))
    app = App(str(ROOT/'src-tauri/target/release/bandpeek'),env,OUT/'performance.stderr')
    results = {'updater': 'inactive until owner public key is embedded', 'states': {}}
    if args.visible_only:
        results = json.loads((OUT/'performance.json').read_text())
        results['visible_sample_separate_instance'] = True
    try:
        assert app.wait_for('validation-state=tray') is not None
        for mode in ([] if args.visible_only else ['speeds_only','icon_and_speeds','icon_only']):
            app.send('menu-bar '+mode)
            assert app.wait_for('validation-settings=') is not None
            print(f'{mode}: settling for 30 s, then sampling for 60 s',flush=True)
            time.sleep(30)
            app.send('status')
            results['states'][mode] = measure(app.proc.pid, app.name)
            results['states'][mode]['status'] = json.loads(app.wait_for('validation-status='))
            summary(mode,results['states'][mode])
            assert app.proc.poll() is None
        app.send('menu-bar speeds_only'); app.wait_for('validation-settings=')
        print('main_visible: settling for 30 s, then sampling for 60 s',flush=True)
        app.send('open'); time.sleep(2); app.send('front'); time.sleep(30)
        # As in Milestone 5, another window can cover the page during settling.
        # Bring it forward again before sampling; never accept a hidden page.
        for _ in range(5):
            app.send('front'); time.sleep(1)
            if app.report('main','document.visibilityState') == 'visible': break
        assert app.report('main','document.visibilityState') == 'visible'
        results['states']['main_visible'] = measure(app.proc.pid,app.name,tick=lambda:app.send('front'))
        assert app.report('main','document.visibilityState') == 'visible'
        summary('main_visible',results['states']['main_visible'])
    finally:
        if app.proc.poll() is None: app.send('quit')
        app.proc.wait(timeout=20)
        results['exit_status'] = app.proc.returncode
        shutil.rmtree(work,ignore_errors=True)
        (OUT/'performance.json').write_text(json.dumps(results,indent=2)+'\n')
    assert app.proc.returncode == 0

if __name__ == '__main__': main()
