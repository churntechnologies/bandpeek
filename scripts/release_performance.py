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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--visible-only', action='store_true', help='Resume the visible sample after an occlusion failure; retain completed tray samples')
    parser.add_argument('--binary', type=pathlib.Path, default=ROOT/'src-tauri/target/release/bundle/macos/BandPeek.app/Contents/MacOS/bandpeek')
    parser.add_argument('--source-db', type=pathlib.Path, help='Optional explicitly selected synthetic fixture; default is fresh isolated history')
    parser.add_argument('--update-endpoint', help='Owner-authorized trusted HTTPS fixture; no verification bypass')
    parser.add_argument('--output', type=pathlib.Path, default=OUT/'performance.json')
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix='bandpeek-release-perf-'))
    db = work/'bandpeek.db'
    if args.source_db: sqlite_backup(args.source_db, db)
    env = dict(os.environ, BANDPEEK_DB_PATH=str(db), BANDPEEK_SETTINGS_PATH=str(work/'settings.json'))
    extra_args = ['--validation-update-endpoint', args.update_endpoint] if args.update_endpoint else []
    app = App(str(args.binary),env,args.output.with_suffix('.stderr'),extra_args=extra_args)
    results = {'updater': 'configured embedded key; installed bundle; ordinary TLS verification',
               'history': 'explicit fixture' if args.source_db else 'fresh isolated database', 'states': {}}
    if args.visible_only:
        results = json.loads(args.output.read_text())
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
            collectors = results['states'][mode]['nettop_children']
            assert len(collectors) == 1 and collectors[0]['parent_is_app']
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
        collectors = results['states']['main_visible']['nettop_children']
        assert len(collectors) == 1 and collectors[0]['parent_is_app']
        assert app.report('main','document.visibilityState') == 'visible'
        summary('main_visible',results['states']['main_visible'])
    finally:
        if app.proc.poll() is None: app.send('quit')
        app.proc.wait(timeout=20)
        results['exit_status'] = app.proc.returncode
        update_log = work/'updates.log'
        results['updater_check_observed'] = update_log.exists() and 'no newer release' in update_log.read_text()
        if update_log.exists():
            args.output.with_suffix('.updater.log').write_text(update_log.read_text())
        shutil.rmtree(work,ignore_errors=True)
        args.output.write_text(json.dumps(results,indent=2)+'\n')
    assert app.proc.returncode == 0
    if args.update_endpoint:
        assert results['updater_check_observed'], 'Expected a real configured-updater HTTPS no-update check'

if __name__ == '__main__': main()
