#!/usr/bin/env python3
"""Pause only the topology harness's traffic fixtures; require unchanged generation.
The collector stays alive. Fixtures are always resumed before their owner quits.
"""
import argparse
import json
import os
import pathlib
import signal
import time
from m5_performance import ps_table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app-pid', type=int, required=True)
    ap.add_argument('--fixture-pid', type=int, required=True)
    ap.add_argument('--trace', type=pathlib.Path, required=True)
    args = ap.parse_args()
    table = ps_table()
    owner = table[args.app_pid]['ppid']
    assert table[args.fixture_pid]['ppid'] == owner
    fixtures = [args.fixture_pid] + [pid for pid, row in table.items()
        if row['ppid'] == owner and 'HTTPServer' in row['command']]
    assert len(fixtures) == 2
    before = [json.loads(l) for l in args.trace.read_text().splitlines()]
    generation = [e for e in before if e['stage'] == 'collector-start'][-1]['generation']
    started = int(time.time()*1000)
    try:
        for pid in fixtures: os.kill(pid, signal.SIGSTOP)
        print(f'Idle window started; paused fixtures {fixtures}', flush=True)
        time.sleep(65)
        events = [json.loads(l) for l in args.trace.read_text().splitlines()]
        window = [e for e in events if e['at_unix_ms'] >= started]
        samples = [e for e in window if e['stage'] == 'sample']
        assert len(samples) >= 30
        assert all(e['generation'] == generation for e in samples)
        assert not any(e['stage'] == 'collector-start' for e in window)
        stable = [e for e in window if e['stage'] == 'process-delta'
                  and e['at_unix_ms'] > started+10_000 and e['id']['pid'] == args.fixture_pid]
        assert stable and all(e['delta_rx'] == e['delta_tx'] == 0 for e in stable)
        result = dict(seconds=65, accepted_samples=len(samples), generation=generation,
                      restarts=0, fixture_zero_deltas=True)
        args.trace.with_suffix('.idle.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2), flush=True)
    finally:
        for pid in fixtures:
            try: os.kill(pid, signal.SIGCONT)
            except ProcessLookupError: pass


if __name__ == '__main__': main()
