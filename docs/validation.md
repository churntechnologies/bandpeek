# Milestone 1 engineering report

Validated 2026-09-28 on macOS 27.0 (26A428), Apple Silicon. Rust 1.98.1, Tauri 2.12.0, Node 24.19.0.

## Result

Working release desktop prototype using real TCP/UDP process counters, with explicitly limited polling coverage. The controlled transfer test agrees exactly with independent nettop. This is not yet a complete internet-usage or billing meter. Final design, SQLite, history views, auto-start, installers and Windows/Linux collectors were not implemented.

## Architecture and permissions

Rust collector trait → macOS persistent nettop worker → process-birth identity + cached bundle metadata → platform-independent session aggregation → Tauri snapshot command → crude React/TypeScript table. One nettop process, 5-second sampling, stdout PTY, open idle stdin pipe. A small SDK-compiled C adapter supplies public libproc/sysctl process birth times. No private API is called by BandPeek. See [the collector decision](collector-decision.md) for alternatives and compatibility tradeoffs.

No root/sudo, administrator privileges, Full Disk Access, packet capture, network extension or permission dialog was needed. Agent validation ran outside the execution sandbox as the ordinary logged-in user; that sandbox blocks nettop statistics access. Signing, notarization and App Store sandbox compatibility are untested.

## Controlled validation

`python3 scripts/validate.py` starts BandPeek and an independent persistent nettop stream, waits 10 seconds, then creates two new Node processes. The client sends exactly 2,097,152 synthetic bytes over loopback and receives 4,194,304. It additionally downloads a 1,048,576-byte public HTTPS object from Cloudflare. Processes stay alive after transfers to expose terminal counters. Final validation runs 75 seconds at the production 5-second interval and compares 14 complete BandPeek samples with the corresponding independent frames.

| Process | BandPeek download | Independent download | BandPeek upload | Independent upload | Difference |
|---|---:|---:|---:|---:|---:|
| Client (PID 43618) | 5,251,586 B | 5,251,586 B | 2,098,941 B | 2,098,941 B | 0 B |
| Server (PID 43617) | 2,097,152 B | 2,097,152 B | 4,194,304 B | 4,194,304 B | 0 B |

Client payload was 5,242,880 bytes received and 2,097,152 sent. Socket counters exceed those values by 8,706 received and 1,789 sent bytes, consistent with HTTPS/TLS and request/response protocol overhead. Both collectors agree on those extra bytes. Server counters exactly match the synthetic payloads. This does not establish on-the-wire Ethernet/IP accounting.

## Overall deltas and explained differences

| Direction | BandPeek | Independent calculation | Difference |
|---|---:|---:|---:|
| Download | 8,448,934 B | 8,470,465 B | -21,531 B (-0.254%) |
| Upload | 8,551,201 B | 8,565,634 B | -14,433 B (-0.168%) |

The independent calculation sums positive counter increments across matching complete frames; it includes the first counter of a PID that appears later. BandPeek deliberately baselines later-appearing processes already alive before collection. The streams are independently scheduled, not atomically synchronized.

- Two pre-existing services first appeared with counters: syspolicyd (PID 612) had 9,770 download / 7,389 upload bytes and timed (PID 503) had 144 / 144 bytes. BandPeek baselined them; those amounts exactly explain their differences.
- A short-lived gh process (PID 43705) contributed 5,606 / 1,893 bytes to independent nettop, but exited before BandPeek could verify its birth identity. Those bytes were omitted, not assigned an unsafe PID-only identity.
- Sample-boundary evidence: PID 91961 started 5,524 / 0 bytes ahead of the reference and ended 24 / 28 bytes ahead, exactly explaining its -5,500 / +28 delta difference. PID 91975 started 51 / 4,981 bytes ahead and ended equal, exactly explaining its -51 / -4,981 difference. Smaller differences are also associated with independently scheduled boundaries.
- Four unresolved row observations and zero malformed/rejected samples occurred. An unresolved observation is not necessarily a unique process; nettop may retain exited rows.

Earlier 3-second runs also matched the controlled transfers exactly. Their whole-session differences were -0.312%/-0.231% and -0.215%/-0.195%. The final 5-second results above replace those preliminary results.

## Recovery and correctness checks

- Forced SIGKILL of the test-owned nettop child: the collector exposed a gap, advanced to generation 2 and resumed tracking; session totals stayed monotonic. On normal probe exit no collector child remained.
- Nine Rust tests pass: baseline/rates, birth/exit/reappearance/PID reuse, per-direction reset, restart rebaseline, duplicate/out-of-order rejection, real CSV/process-name parsing, malformed/partial/negative/overflow input, outer app helper resolution and public process-birth lookup.
- TypeScript check + Vite production build: pass. Tauri release build without bundling: pass. Final release with embedded assets: pass. Clippy for all targets with warnings denied: pass. rustfmt: pass. Python validation-script syntax and Node traffic-script syntax: pass.
- Native UI inspection verified live rates, process rows, PID/bundle identity, session totals and the final 5-second interval; the app was left open. Final performance results are recorded below.

## Performance

Final release build measured over **60.72 seconds**, after **20 seconds settling**, with the full debug window visible and no generated test traffic during measurement. Ordinary background apps/network activity remained running; this was not an isolated idle-machine laboratory test. Five-second collection/display interval, production embedded frontend (no Vite server).

| Component | Average CPU (one core = 100%) | Mean RSS | Peak sampled RSS |
|---|---:|---:|---:|
| BandPeek native process | 0.198% | 92.96 MiB | 93.42 MiB |
| nettop collector | 0.296% | 3.02 MiB | 3.12 MiB |
| GPU | 0.428% | 26.76 MiB | 28.25 MiB |
| WebContent | 0.478% | 46.03 MiB | 47.95 MiB |
| Networking | 0.000% | 7.38 MiB | 7.50 MiB |

**BandPeek + nettop: 0.494% average CPU; 95.98 MiB mean RSS.**

**Including all three WebKit helpers: 1.400% average CPU; 176.14 MiB summed mean RSS.** Summed RSS can count shared pages more than once; it is not unique physical-memory usage. WebKit's memory cost remains substantial relative to the collector. The native process plus collector is below 1%, but the full visible desktop remains above the ideal sub-1% target. That limitation is not hidden by excluding helper processes.

Method: `scripts/measure.py` reads cumulative `ps time` and RSS for the app, its nettop child and newly created WebKit GPU/WebContent/Networking helpers. Average CPU is `(CPU_seconds_end - CPU_seconds_start) / monotonic_wall_seconds * 100`, not a snapshot `%cpu` or division by core count. RSS is sampled every 5 seconds (13 observations including endpoints). PIDs/commands and all samples are retained in `.validation/performance.json`. An optional `--pids app,collector,helpers` argument measures an already settled instance.

Investigations and prior results (retained, not discarded):

- Initial subprocess configuration used stdin=/dev/null. nettop averaged 136.30% CPU over 60.47 seconds. A separate concurrent I/O experiment measured ~145% with `/dev/null` and ~0.20% with a held-open idle pipe; `-c` alone did not help. The EOF spin is fixed in the production worker.
- At 3-second sampling after the stdin fix, one full desktop run measured 3.75% combined including WebKit, with late native-process spikes; a subsequent settled run measured 2.96%. The latter was 0.87% for native process + nettop and 214.68 MiB summed mean RSS.
- The debug UI now caches its Intl.NumberFormat rather than performing locale setup for every cell, and both collection and display refresh moved to 5 seconds. The final full desktop measurement is 1.40%. Ambient workload also varies between runs, so this is not a controlled attribution of every percentage point to either optimization.
- Further shell rendering/RAM work is still appropriate; no UI kit, animation, final styling, or extra analytics/update packages were introduced to address it.

## Files and scope

`README.md`, MIT `LICENSE`, `.gitignore`, npm/Cargo lockfiles, Vite/TypeScript config; `src/main.tsx` and minimal CSS; `src-tauri` Tauri config/capability/build entrypoints, model/aggregator, collector trait, macOS stream/parser/identity/C ABI adapter, collector-only probe and unchanged approved icon; `scripts/` for repeatable validation/recovery/performance/I/O diagnosis; `docs/collector-decision.md` and this report. The design ZIP was inspected under its actual filename, BandPeek desktop application.zip. Only the required approved 128px icon was copied; SHA-256 matches the handoff.

## Remaining limitations and next milestone

Processes that start and exit between samples, terminal counters after exit, first traffic from pre-existing processes that appear later, counter resets and collection gaps may be omitted. Shared helpers may not map to their originating app. The display adds ~5 seconds of framing latency plus up to 5 seconds of UI polling delay. All-interface socket traffic includes LAN and loopback; Wi-Fi/Ethernet filtering is not exposed. Counters are in RAM only and disappear on exit. Only this macOS build was tested.

**Recommended next milestone: macOS collector coverage and interface attribution.** Build a repeatable short-lived-process/connection-churn and sleep/wake test matrix, decide whether nettop’s lifecycle gaps are acceptable for the product, and prove Wi-Fi/Ethernet attribution across interface changes and VPNs without increasing overhead materially. Establish the accuracy contract before implementing SQLite history or the approved polished UI.

Raw evidence is retained locally under ignored `.validation/`: validation-result.json, overall-comparison.json, independent-nettop.csv, validation-probe.jsonl, recovery-result.json, nettop-io-experiment.json, performance-before-fix.json, performance-first-fixed.json, performance-three-second.json and performance.json. Raw process names and paths are not included in published source.
