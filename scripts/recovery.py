#!/usr/bin/env python3
"""Kill only this test's nettop child and verify generation change/recovery/cleanup."""
import json, pathlib, signal, subprocess, os, time
root=pathlib.Path(__file__).resolve().parents[1]
output=root/'.validation/recovery.jsonl'
with output.open('w') as log:
    probe=subprocess.Popen([str(root/'src-tauri/target/release/bandpeek-probe'),'38'],stdout=log)
    try:
        time.sleep(16)
        initial=[json.loads(x) for x in output.read_text().splitlines()][-1]
        assert initial['sample_sequence'] >= 2
        child=initial['collector_pid']
        parent=int(subprocess.check_output(['ps','-p',str(child),'-o','ppid='],text=True).strip())
        assert parent==probe.pid
        os.kill(child,signal.SIGKILL)
        probe.wait(timeout=45)
        samples=[json.loads(x) for x in output.read_text().splitlines()]
        last=samples[-1]
        assert last['collector_generation']>=2 and last['status'].startswith('Tracking')
        assert any('Collector gap' in s['status'] for s in samples)
        assert all(b['session_bytes'][d]>=a['session_bytes'][d] for a,b in zip(samples,samples[1:]) for d in ('download','upload'))
        for pid in {s['collector_pid'] for s in samples if s['collector_pid']}:
            result=subprocess.run(['ps','-p',str(pid),'-o','pid='],capture_output=True,text=True)
            assert not result.stdout.strip(),f'orphan nettop {pid}'
        summary={'recovered_generation':last['collector_generation'],'samples':last['sample_sequence'],'monotonic_totals':True,'no_orphan_children':True}
        (root/'.validation/recovery-result.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary))
    finally:
        if probe.poll() is None:probe.terminate();probe.wait()
