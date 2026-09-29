#!/usr/bin/env python3
"""Observe a real Tauri process through manual sleep or automated child faults.
Launches a technical tray instance; emits private snapshots. For wifi mode,
restores Wi-Fi in finally. Only kills/stops the verified child of this test app.
"""
import argparse, datetime, json, math, os, signal, subprocess, sys, time
from m2_common import ROOT, OUT, Nettop, save, active_route

def read_samples(path):
    result=[]
    for line in path.read_text().splitlines():
        if line.startswith('{'):result.append(json.loads(line))
    return result

def run(mode):
    route=active_route()
    if mode=='wifi':
        hardware=subprocess.check_output(['networksetup','-listallhardwareports'],text=True)
        assert 'Hardware Port: Wi-Fi\nDevice: '+route['device'] in hardware, 'Active route is not the verified Wi-Fi hardware port'
    name='lifecycle-'+mode;path=OUT/f'{name}.jsonl';events=[]
    def event(label, **data):
        events.append({'event':label,'wall':time.time(),**data});print(label,flush=True)
    with path.open('w') as log:
        app=subprocess.Popen([str(ROOT/'src-tauri/target/release/bandpeek'),'--validation-mode','visible','--validation-log'],stdout=log,stderr=(OUT/f'{name}.stderr').open('w'))
        # Independent filtered observers validate reconnect classifications.
        wifi=Nettop(name+'-wifi',extra=['-t','wifi','-J','bytes_in,bytes_out']) if mode=='wifi' else None
        allnet=Nettop(name+'-all',extra=['-J','bytes_in,bytes_out']) if mode=='wifi' else None
        transfer_records=[]
        def transfer(label):
            # Keep process alive after HTTP traffic so birth identity is verifiable.
            code="import urllib.request,time,json,sys; u='http://'+sys.argv[1]+'/'; r=urllib.request.urlopen(u,timeout=8); print(json.dumps({'payload':len(r.read()),'status':r.status}),flush=True); time.sleep(12)"
            p=subprocess.Popen([sys.executable,'-c',code,route['gateway']],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            out,err=p.communicate(timeout=25)
            transfer_records.append({'label':label,'pid':p.pid,'returncode':p.returncode,'result':out,'error':err})
        try:
            time.sleep(16);first=read_samples(path)[-1];assert first['sample_sequence']>=2
            transfer('before');event('baseline-ready',generation=first['collector_generation'])
            if mode=='sleep':
                event('READY: sleep Mac for about 30 seconds, then wake it; recording for 180 seconds')
                # wall deadline includes sleep; user has ample time to react.
                end=time.time()+180
                while time.time()<end:time.sleep(1)
            elif mode=='wifi':
                try:
                    subprocess.run(['networksetup','-setairportpower',route['device'],'off'],check=True);event('wifi-off')
                    time.sleep(12);transfer('offline')
                finally:
                    subprocess.run(['networksetup','-setairportpower',route['device'],'on'],check=True);event('wifi-on')
                time.sleep(20)
            else:
                current=read_samples(path)[-1];child=current['collector_pid']
                parent=int(subprocess.check_output(['ps','-p',str(child),'-o','ppid='],text=True))
                assert parent==app.pid
                os.kill(child,signal.SIGKILL if mode=='kill' else signal.SIGSTOP);event('child-'+mode,pid=child)
                time.sleep(32 if mode=='stall' else 15)
            transfer('after');time.sleep(12)
            samples=read_samples(path);last=samples[-1]
            assert app.poll() is None and last['status'].startswith('Tracking')
            assert all(b['session_bytes'][d]>=a['session_bytes'][d] for a,b in zip(samples,samples[1:]) for d in ('download','upload'))
            assert all(math.isfinite(s[k]) and s[k]>=0 for s in samples for k in ('download_bytes_per_second','upload_bytes_per_second'))
            generations=sorted({s['collector_generation'] for s in samples})
            power_events=[]
            if mode in ('kill','stall','sleep'):assert len(generations)>=2
            if mode=='sleep':
                power_log=subprocess.check_output(['pmset','-g','log'],text=True)
                for line in power_log.splitlines():
                    try:at=datetime.datetime.strptime(line[:25],'%Y-%m-%d %H:%M:%S %z').timestamp()
                    except ValueError:continue
                    if at>=events[0]['wall'] and ('Entering Sleep state' in line or 'Wake from' in line or 'FullWake' in line):power_events.append(line)
                assert any('Entering Sleep state' in line for line in power_events), 'No real macOS sleep event recorded'
                assert any('Wake' in line for line in power_events), 'No real macOS wake event recorded'
            for r in transfer_records:
                rows=[row for s in samples for row in s['rows'] if row['id']['pid']==r['pid']]
                r['observed_bytes']=max((row['total_bytes'] for row in rows),default=0)
                if r['label'] in ('before','after'):assert r['returncode']==0 and r['observed_bytes']>0,r
                if wifi:
                    r['filtered']={label:{d:max((f['rows'].get(r['pid'],{}).get(d,0) for f in stream.frames),default=0) for d in ('download','upload')} for label,stream in [('wifi',wifi),('all',allnet)]}
            tracking=[]
            for s in samples:
                if s['status'].startswith('Tracking') and (not tracking or s['sample_sequence']!=tracking[-1]['sample_sequence']):tracking.append(s)
            restart_checks=[]
            for a,b in zip(tracking,tracking[1:]):
                if b['collector_generation']!=a['collector_generation']:
                    assert b['session_bytes']==a['session_bytes']
                    assert b['download_bytes_per_second']==0 and b['upload_bytes_per_second']==0
                    restart_checks.append({'generation':b['collector_generation'],'first_rate_zero':True,'totals_unchanged':True})
            result={'power_events':power_events,'restart_checks':restart_checks,'route':route,'mode':mode,'app_pid':app.pid,'events':events,'generations':generations,'monotonic':True,'finite_nonnegative_rates':True,
                'gaps':list(dict.fromkeys(s['status'] for s in samples if 'gap' in s['status'])),
                'max_down_rate':max(s['download_bytes_per_second'] for s in samples),'max_up_rate':max(s['upload_bytes_per_second'] for s in samples),
                'transfers':transfer_records,'last_sequence':last['sample_sequence']}
            save(name+'-result',result)
        finally:
            if wifi:wifi.close();allnet.close()
            # SIGTERM does not run Rust Drop. Explicitly stop only this app's child.
            for child in {s['collector_pid'] for s in read_samples(path) if s['collector_pid']}:
                try:
                    parent=subprocess.check_output(['ps','-p',str(child),'-o','ppid='],text=True).strip()
                    if parent and int(parent)==app.pid:os.kill(child,signal.SIGKILL)
                except (ProcessLookupError,subprocess.CalledProcessError):pass
            app.terminate();app.wait(timeout=10)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['sleep','wifi','kill','stall']);args=parser.parse_args();run(args.mode)
