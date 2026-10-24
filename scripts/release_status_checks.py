#!/usr/bin/env python3
"""Native Packet/status mode checks and on-disk preference persistence."""
import json
import os
from pathlib import Path
import tempfile
from m5_window_cycles import App

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.validation/release'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bandpeek-status-') as directory:
        work=Path(directory)
        env=dict(os.environ,BANDPEEK_DB_PATH=str(work/'db.sqlite'),BANDPEEK_SETTINGS_PATH=str(work/'settings.json'))
        binary=str(ROOT/'src-tauri/target/release/bandpeek')
        results={}
        app=App(binary,env,OUT/'status.stderr')
        try:
            assert app.wait_for('validation-state=tray') is not None
            app.send('status'); first=json.loads(app.wait_for('validation-status='))
            assert first['icon_hidden'] and not first['rates_hidden']
            for mode in ['speeds_only','icon_and_speeds','icon_only']:
                app.send('menu-bar '+mode)
                settings=json.loads(app.wait_for('validation-settings='))
                assert settings['menu_bar_display']==mode
                assert json.loads((work/'settings.json').read_text())['menu_bar_display']==mode
                statuses=[]
                for down,up in [(0,0),(14300,17800),(888800000,999900000000)]:
                    app.send(f'status-rates {down} {up}')
                    status=json.loads(app.wait_for('validation-status='))
                    assert status['font_size']==10 and 'Medium' in status['font_name']
                    assert status['line_spacing']==10
                    assert status['template']
                    assert status['rates_hidden']==(mode=='icon_only')
                    assert status['icon_hidden']==(mode=='speeds_only')
                    if mode=='icon_and_speeds':
                        assert status['icon_size']==14
                        assert status['text_x']-(status['icon_x']+14)==4
                    if mode=='icon_only': assert status['icon_size']==16 and status['length']==28
                    if down==14300: assert status['down']=='↓ 14.3 KB/s' and status['up']=='↑ 17.8 KB/s'
                    statuses.append(status)
                # Rates rise through the samples: the item widens at once and never narrows here.
                lengths=[s['length'] for s in statuses]
                assert lengths==sorted(lengths), statuses
                if mode=='speeds_only':
                    assert all(s['length']==4+s['rates_width'] and s['text_x']==2 for s in statuses), statuses
                if mode!='icon_only':
                    assert all(s['text_rect'][2]==s['rates_width'] for s in statuses), statuses
                if mode=='icon_and_speeds':
                    assert all(s['length']==22+s['rates_width'] for s in statuses), statuses
                    assert all(s['icon_x']==2 and s['text_x']==20 for s in statuses), statuses
                results[mode]=statuses
        finally:
            if app.proc.poll() is None: app.send('quit')
            app.proc.wait(timeout=20)
        assert app.proc.returncode==0
        # New process loads the saved last mode.
        app=App(binary,env,OUT/'status-relaunch.stderr')
        try:
            assert app.wait_for('validation-state=tray') is not None
            app.send('status'); s=json.loads(app.wait_for('validation-status='))
            assert s['rates_hidden'] and not s['icon_hidden'] and s['icon_size']==16
            results['relaunch_persisted']=True
        finally:
            app.send('quit');app.proc.wait(timeout=20)
        assert app.proc.returncode==0
        (OUT/'status.json').write_text(json.dumps(results,indent=2)+'\n')
        print('PASS: default, all 3 native modes, font, template, geometry, widths, formatting and persisted relaunch')
        print({m:s[0]['length'] for m,s in results.items() if isinstance(s,list)})

if __name__=='__main__':main()
