#!/usr/bin/env python3
"""Click-path to native visibility and double-rAF content latency, resource use,
and 100 cached popup cycles. All history/settings are isolated.
"""
import argparse,json,os,pathlib,statistics,subprocess,tempfile,time
from m5_window_cycles import App,heap_counts,rss_kib
from m5_performance import measure,summary,ps_table
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--binary',default=str(ROOT/'src-tauri/target/release/bandpeek'));ap.add_argument('--label',default='fixed');ap.add_argument('--baseline',action='store_true');ap.add_argument('--cycles',type=int,default=100);ap.add_argument('--seconds',type=int,default=60)
    a=ap.parse_args();out=ROOT/'.validation/beta2';out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bandpeek-popup-') as tmp:
        work=pathlib.Path(tmp);env=dict(os.environ,BANDPEEK_DB_PATH=str(work/'db'),BANDPEEK_SETTINGS_PATH=str(work/'settings'))
        app=App(a.binary,env,out/f'popup-{a.label}.stderr')
        # Temporary measurement assertion; no persistent power setting changes.
        awake=subprocess.Popen(['caffeinate','-d','-u','-w',str(app.proc.pid)])
        result={'cycles':[],'latency_method':'stdin dispatch to production click path → window.show → two requestAnimationFrame callbacks with content assertion; includes IPC overhead','states':{}}
        try:
            assert app.wait_for('validation-state=tray') is not None
            time.sleep(10)
            if a.seconds:
                result['states']['tray']=measure(app.proc.pid,app.name,seconds=a.seconds);summary('tray',result['states']['tray'])
            result['objects_before']=heap_counts(app.proc.pid)
            for i in range(a.cycles):
                start=time.monotonic();app.send('popup')
                visible=app.wait_for('validation-popup-visible-ms=');assert visible is not None,f'open {i}'
                if a.baseline:
                    app.send("eval tray requestAnimationFrame(()=>requestAnimationFrame(()=>window.__TAURI_INTERNALS__.invoke('validation_report',{text:document.querySelector('.popup')?'popup-painted':'empty'})))")
                painted=app.wait_for('validation-report=')
                if painted!='popup-painted':
                    app.send('geometry tray');geometry=app.wait_for('validation-geometry=')
                    app.send('popup-identity');identity=app.wait_for('validation-popup-identity=')
                    page=app.report('tray',"JSON.stringify({visibility:document.visibilityState,root:!!document.querySelector('.popup')})")
                    raise AssertionError((i,visible,painted,geometry,identity,page))
                latency=(time.monotonic()-start)*1000
                identity=None
                if not a.baseline:
                    app.send('popup-identity');identity=json.loads(app.wait_for('validation-popup-identity='));assert identity['visible'] and identity['webviews']==1
                result['cycles'].append({'cycle':i+1,'native_ms':float(visible),'content_ms':latency,'identity':identity})
                if i==0:
                    if a.seconds:
                        result['states']['popup']=measure(app.proc.pid,app.name,seconds=a.seconds);summary('popup',result['states']['popup'])
                    result['content']=json.loads(app.report('tray',"JSON.stringify({width:innerWidth,height:innerHeight,speeds:document.querySelector('.popup-speeds')?.innerText,visibility:document.visibilityState})"))
                    assert result['content']['visibility']=='visible', 'popup resource sample was occluded'
                    # Focus loss through production native main window path.
                    app.send('open')
                    if not a.baseline:assert app.wait_for('validation-popup-hidden') is not None, 'focus loss did not hide popup'
                    assert app.report('main',"document.querySelector('.header')?'ready':''")
                    app.send('popup-close');app.send('close');assert app.wait_for('validation-state=main-closed') is not None
                    result['focus_test']=True
                else:app.send('popup-close')
                # Flush hidden acknowledgement; baseline has no acknowledgement.
                if not a.baseline and i!=0:assert app.wait_for('validation-popup-hidden') is not None
                time.sleep(.35)
            result['objects_after']=heap_counts(app.proc.pid);result['rss_after_mib']=rss_kib(app.proc.pid)/1024
            if a.seconds:
                result['states']['tray_after_cycles']=measure(app.proc.pid,app.name,seconds=a.seconds);summary('tray after cycles',result['states']['tray_after_cycles'])
            result['collector_pids']=[p for p,r in ps_table().items() if r['ppid']==app.proc.pid and 'nettop' in r['command']];assert len(result['collector_pids'])==1
        finally:
            app.send('quit');app.proc.wait(timeout=20);result['exit_status']=app.proc.returncode
            awake.wait(timeout=10)
    result['orphans_after_exit']=[p for p in result.get('collector_pids',[]) if p in ps_table()];assert not result['orphans_after_exit']
    times=[r['content_ms'] for r in result['cycles'][1:]]
    result['first_ms']=result['cycles'][0]['content_ms'];result['subsequent_median_ms']=statistics.median(times) if times else None;result['subsequent_p95_ms']=sorted(times)[int(len(times)*.95)] if times else None;result['subsequent_max_ms']=max(times) if times else None
    (out/f'popup-{a.label}.json').write_text(json.dumps(result,indent=2)+'\n');print({k:result[k] for k in ['first_ms','subsequent_median_ms','subsequent_p95_ms','subsequent_max_ms','objects_before','objects_after','orphans_after_exit']},flush=True)
    if not a.baseline:
        assert len({r['identity']['pointer'] for r in result['cycles']})==1, 'popup WebView was replaced'
        assert result['subsequent_p95_ms']<150,result['subsequent_p95_ms']
if __name__=='__main__':main()
