#!/usr/bin/env python3
"""Permanent-ID login registration, recovery and production restart seam.
Isolated history/settings. Actual signed artifact installation remains a gate.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import tempfile
import time
from m5_window_cycles import App
from m5_performance import ps_table

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.validation/release'
BUNDLE=ROOT/'src-tauri/target/release/bundle/macos/BandPeek.app'
BINARY=BUNDLE/'Contents/MacOS/bandpeek'

def invoke(app,command,args=None):
    app.send(f"eval main window.__TAURI_INTERNALS__.invoke('{command}',{json.dumps(args or {})}).then(x=>window.__TAURI_INTERNALS__.invoke('validation_report',{{text:'check='+JSON.stringify(x)}}))")
    result=app.wait_for('validation-report=check=')
    assert result is not None,command
    return json.loads(result)

def start(env,work):
    a=App(str(BINARY),env,OUT/'lifecycle.stderr',mode='auto')
    assert a.wait_for('validation-state=visible') is not None
    assert a.report('main',"document.querySelector('.header')?'ready':''")=='ready'
    return a

def snapshot(app):
    result=app.report('main',"window.__releaseSnapshot||''")
    return result

def children(pid):
    return [p for p,r in ps_table().items() if r['ppid']==pid and '/usr/bin/nettop' in r['command']]

def wait_until(test,seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=test()
        if value:return value
        time.sleep(.2)
    raise AssertionError('Timed out waiting for lifecycle condition')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    results={}
    with tempfile.TemporaryDirectory(prefix='bandpeek-release-lifecycle-') as directory:
        work=Path(directory)
        env=dict(os.environ,BANDPEEK_DB_PATH=str(work/'db.sqlite'),BANDPEEK_SETTINGS_PATH=str(work/'settings.json'))
        app=start(env,work)
        try:
            initial=invoke(app,'get_login_item')
            results['login_initial']=initial
            on=invoke(app,'set_login_item',{'enabled':True})
            assert on['state']=='enabled' and not on['error'],on
            results['login_enable']=on
            app.send('quit');app.proc.wait(timeout=20)
            app=start(env,work)
            assert invoke(app,'get_login_item')['state']=='enabled'
            results['login_persists']=True
            off=invoke(app,'set_login_item',{'enabled':False})
            assert off['state']=='disabled' and not off['error'],off
            results['login_final']=off
            # Persisted mode must survive the same restart path used by updates.
            app.send('menu-bar icon_and_speeds');app.wait_for('validation-settings=')
            app.send('close');app.wait_for('validation-state=main-closed')
            original=wait_until(lambda: children(app.proc.pid))
            assert len(original)==1
            # Force restart-marker preparation failure; no installation is done.
            (work/'update-relaunch').mkdir()
            app.send('update-relaunch')
            replaced=wait_until(lambda:[p for p in children(app.proc.pid) if p not in original])
            assert len(replaced)==1 and all(p not in ps_table() for p in original)
            assert app.proc.poll() is None
            assert (work/'update-relaunch').is_dir()
            (work/'update-relaunch').rmdir()
            results['failed_preparation_resumed_one_collector']=True
            # Real normal Tauri restart callback, reap + flush before new launch.
            app.send('update-relaunch')
            assert app.wait_for('validation-update=')=='collector-stopped-database-flushed'
            app.proc.wait(timeout=20)
            assert app.proc.returncode==0
            assert all(p not in ps_table() for p in replaced)
            # Restart inherits stdin/stdout but has a distinct application PID.
            new_pid=wait_until(lambda: next((p for p,r in ps_table().items() if
                str(BINARY) in r['command'] and p!=app.proc.pid),None))
            assert len(wait_until(lambda:children(new_pid)))==1
            assert not (work/'update-relaunch').exists()
            assert app.wait_for('validation-state=tray') is not None
            results['update_marker_returns_manual_launch_to_tray']=True
            app.proc.stdin.write('status\n');app.proc.stdin.flush()
            status=json.loads(app.wait_for('validation-status='))
            assert not status['icon_hidden'] and not status['rates_hidden']
            # Open a window to read ServiceManagement after relaunch, then leave Off.
            app.proc.stdin.write("open\n");app.proc.stdin.flush();time.sleep(2)
            app.proc.stdin.write("eval main window.__TAURI_INTERNALS__.invoke('get_login_item').then(x=>window.__TAURI_INTERNALS__.invoke('validation_report',{text:'check='+JSON.stringify(x)}))\n")
            app.proc.stdin.flush()
            assert json.loads(app.wait_for('validation-report=check='))['state']=='disabled'
            app.proc.stdin.write('quit\n');app.proc.stdin.flush()
            wait_until(lambda:new_pid not in ps_table())
            results['restart_clean']=True
            results['settings_and_login_off_preserved']=True
            with sqlite3.connect(work/'db.sqlite') as db:
                assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                results['sqlite_integrity']='ok'
        finally:
            if app.proc.poll() is None:
                # Ensure login registration is left disabled even if a check fails.
                app.send('open');time.sleep(1)
                try:invoke(app,'set_login_item',{'enabled':False})
                finally:app.send('quit');app.proc.wait(timeout=20)
        # Reproduce the actual login-launch Apple event (no logout needed).
        log=work/'login-launch.log'
        command=['osascript','-l','JavaScript',str(ROOT/'scripts/m5_login_launch.js'),str(BUNDLE),'login',
                 'BANDPEEK_DB_PATH='+str(work/'db.sqlite'),'BANDPEEK_SETTINGS_PATH='+str(work/'settings.json'),
                 'BANDPEEK_VALIDATION_OUT='+str(log),'--','--validation-mode','auto','--validation-seconds','8']
        assert subprocess.check_output(command,text=True).strip()=='launched'
        wait_until(lambda:log.exists() and 'validation-state=tray' in log.read_text())
        wait_until(lambda:'validation-state=exited' in log.read_text())
        text=log.read_text()
        assert 'login-launch=true' in text and 'validation-state=main-open' not in text
        results['login_launch_stays_in_tray']=True
    (OUT/'lifecycle.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
