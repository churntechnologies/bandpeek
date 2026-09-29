#!/usr/bin/env python3
"""Isolated desktop collector crash/stall recovery, graceful and abrupt cleanup."""
import json,os,pathlib,queue,signal,sqlite3,tempfile,time
from m5_window_cycles import App
from m5_performance import ps_table
ROOT=pathlib.Path(__file__).resolve().parents[1];OUT=ROOT/'.validation/beta2'

def sample(app,predicate,seconds=25):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:line=app.lines.get(timeout=.2)
        except queue.Empty:continue
        if line.startswith('{'):
            value=json.loads(line)
            if predicate(value):return value
    raise AssertionError('Timed out waiting for collector snapshot')

def children(pid):
    return [p for p,r in ps_table().items() if r['ppid']==pid and '/usr/bin/nettop' in r['command']]

def gone(pid):
    for _ in range(40):
        if pid not in ps_table():return True
        time.sleep(.1)
    return False

def main():
    OUT.mkdir(parents=True,exist_ok=True);result={}
    with tempfile.TemporaryDirectory(prefix='bandpeek-lifecycle-') as tmp:
        env=dict(os.environ,BANDPEEK_DB_PATH=tmp+'/db',BANDPEEK_SETTINGS_PATH=tmp+'/settings')
        app=App(str(ROOT/'src-tauri/target/release/bandpeek'),env,OUT/'lifecycle.stderr',extra_args=['--validation-log'])
        try:
            assert app.wait_for('validation-state=tray') is not None
            for name,fault in [('kill',signal.SIGKILL),('stall',signal.SIGSTOP)]:
                before=sample(app,lambda s:s['status'].startswith('Tracking'))
                child=before['collector_pid'];assert children(app.proc.pid)==[child]
                os.kill(child,fault)
                after=sample(app,lambda s:s['collector_generation']>before['collector_generation'] and s['status'].startswith('Tracking'))
                assert children(app.proc.pid)==[after['collector_pid']]
                assert gone(child)
                assert after['download_bytes_per_second']==after['upload_bytes_per_second']==0
                assert all(after['session_bytes'][d]>=before['session_bytes'][d] for d in ['download','upload'])
                result[name]={'before_generation':before['collector_generation'],'after_generation':after['collector_generation'],'first_rate_zero':True,'one_collector':True,'old_child_reaped':True}
            child=children(app.proc.pid)[0]
        finally:
            app.send('quit');app.proc.wait(timeout=20)
        assert gone(child);result['graceful_exit_reaped']=True
        with sqlite3.connect(tmp+'/db') as db:
            result['sqlite_integrity']=db.execute('PRAGMA integrity_check').fetchone()[0];assert result['sqlite_integrity']=='ok'
        app=App(str(ROOT/'src-tauri/target/release/bandpeek'),env,OUT/'lifecycle-abrupt.stderr',extra_args=['--validation-log'])
        try:
            assert app.wait_for('validation-state=tray') is not None
            current=sample(app,lambda s:s['collector_pid'] is not None);child=current['collector_pid']
            assert children(app.proc.pid)==[child]
            app.proc.kill();app.proc.wait(timeout=10);assert gone(child)
            result['abrupt_exit_hangup_reaped']=True
        finally:
            if app.proc.poll() is None:app.send('quit');app.proc.wait(timeout=20)
    (OUT/'lifecycle.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
if __name__=='__main__':main()
