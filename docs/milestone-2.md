# Milestone 2 — macOS measurement evidence

Measured Sep 28–29, 2026 (local time, UTC+8). Environment: same Apple Silicon Mac as Milestone 1, macOS 27.0 (26A428), ordinary logged-in user. Production architecture remains Rust / Tauri 2 / React, one persistent `nettop`, five-second collection. No SQLite, history UI, final design, installer, launch-at-login or other-platform collector.

## Interface attribution

Validation uses simultaneous independent `nettop` observers only in the test harness, with All, wifi, wired, loopback, undefined and external scopes, plus a connection-detail capture. Production still uses exactly one All summary source. Hardware mapping comes from `networksetup -listallhardwareports`; route and endpoint evidence comes from `route`, `scutil --nwi`, and nettop's connection rows, not interface-name guesses.

The active route is Wi-Fi. Ethernet adapters exist but have no active route. Two third-party VPN clients are configured but disconnected; the presence of `utun` devices is not evidence of a working VPN route. The inspected tunnel device had only a link-local IPv6 address; no reachable VPN test endpoint was supplied. No VPN was enabled or configured for this test, so tunnel encapsulation, double counting and VPN interface migration remain explicitly unvalidated.

| Controlled traffic | All bytes down / up | Wi-Fi | Loopback | wired / undefined |
|---|---:|---|---|---|
| Public HTTPS, successful 1 MiB object | 1,058,608 / 451 | Same as All | 0 | 0 |
| Loopback TCP, 2 MiB receive / 1 MiB send | 2,097,152 / 1,048,576 | 0 | Same as All | 0 |
| LAN gateway HTTP, 2,679-byte response body | 2,803 / 107 | Same as All | 0 | 0 |
| Same process: public HTTPS + loopback | 3,152,841 / 1,049,027 | 1,055,689 / 451 | 2,097,152 / 1,048,576 | 0 |
| Mac's own LAN address, 2 MiB / 1 MiB TCP | 2,097,152 / 1,048,576 | 0 | Same as All | 0 |

`external` equals Wi-Fi for the public and router requests and excludes loopback. **It is not an internet-only filter.** The self-LAN-address test traverses loopback and is not presented as a second physical LAN endpoint. The actual router request establishes LAN-on-Wi-Fi behavior. A preliminary urllib request received HTTP 403; the successful repeated test used the same user agent as the verified curl request. Both runs carried real public traffic; only the successful run supplies the table above.

Wi-Fi off/on also revalidated the same gateway counters in both All and Wi-Fi after reconnect (see lifecycle table). A Wi-Fi → Ethernet handoff and cross-network roaming were unavailable; same-interface reconnect is not evidence of correct historical attribution during a physical-interface handoff.

**Decision:** keep All only. The approved per-process summary has no interface or destination dimension. A current socket interface label is insufficient to partition its historical counter after migration. Switching `-t` breaks counter comparability, multiple persistent collectors violate the approved architecture, and connection-level collection needs a separately validated flow identity/accounting design. Destination addresses alone cannot determine billable internet use (private networks, VPNs, proxies, NAT, public LAN routes). Do not subtract All minus Wi-Fi to derive Ethernet. If later validated, selectors should say **“Traffic via Wi-Fi/Ethernet, including LAN”**, with explicit loopback/other/unknown scope, never “internet usage.”

## Churn methodology

`scripts/m2_churn.py` runs three seeded trials at each interval, alternating order: 480 total client processes, 240 per interval. Each trial has ten clients in each combination of 4 KiB / 1 MiB payload and post-transfer hold of 0 / 1 / 3 / 7 seconds. Starts are randomized 80–320 ms apart. All clients send a known payload over loopback and wait for a server acknowledgment. Exit is promptly reaped; zombie lifetimes are not used to manufacture identity coverage. Only client upload enters the denominator, never the server copy or the one-byte acknowledgment.

The independent one-second nettop observer is diagnostic, not the ground truth for bytes. The denominator is acknowledged payload; byte loss = 1 − BandPeek observed client upload / acknowledged upload. A missed process has no verified BandPeek row. Terminal rows are separately counted only in complete reference frames whose header follows client exit. Five-second display framing does not defer identity lookup: identity is resolved when the row is read, then the frame is committed at the next header.

| Sample interval | Processes missed | Bytes missed | Byte-loss range across three trials |
|---|---:|---:|---:|
| 5 seconds | 126/240 = **52.50%** | **51.67%** | 47.52–55.01% |
| 2 seconds | 90/240 = **37.50%** | **39.15%** | 34.99–42.47% |

| Post-transfer hold (60 clients per interval) | Mean client-body time | 5s process / byte loss | 2s process / byte loss |
|---|---:|---:|---:|
| Immediate exit | 2–3 ms | 100% / 100% | 100% / 100% |
| 1 second | 1.007 s | 78.33% / 76.68% | 50.00% / 56.61% |
| 3 seconds | 3.007 s | 31.67% / 30.01% | 0% / 0% |
| 7 seconds | 7.007 s | 0% / 0% | 0% / 0% |

Two-second sampling materially improves the mixed synthetic workload by **12.52 percentage points of bytes**, but does not solve immediate-exit loss, even for 1 MiB. All 480 processes had terminal rows in the one-second reference stream; BandPeek correctly refused to attach exited rows without live birth evidence. No actual PID reuse occurred. Forcing system PID wrap through tens of thousands of launches was not justified; deterministic birth/exit/reappearance/PID-reuse unit coverage remains in place. An empirical PID-wrap/stale-terminal collision is therefore **not validated**.

Client-body time starts after Python interpreter initialization and excludes interpreter startup/exit overhead; it is not a precise OS process lifetime. The rerunnable harness additionally records spawn-to-reap duration (an upper bound including launch and reaper scheduling), added after these captures. The payload is sent before each hold, so these numbers measure lifetime/observation opportunity, not continuous traffic spread across each lifetime. Do not generalize them to browser downloads or label them a universal ±accuracy bound. No identity safety was relaxed to recover terminal bytes. Production stays at five seconds: the two-second core cost rose from 0.31% to 0.59% CPU in the settled comparison below, while immediate-exit loss remained 100%. The 12.52-point benefit is real for this synthetic lifetime mix, but does not establish complete usage accounting or a sufficiently general benefit to change the approved default.

## Lifecycle methodology

`scripts/m2_lifecycle.py` runs the real Tauri app and logs snapshots once per second only in validation mode. Known gateway transfers before/after faults remain alive long enough for safe identity resolution. Kill/stall tests verify the child belongs to that app before signaling it. Wi-Fi testing restores power in `finally`. Physical sleep requires a human to wake the machine; a signal-stopped child is not claimed as Mac sleep.

A sleep-inclusive `mach_continuous_time` clock replaces `Instant` for sample time and stall detection. The guard is checked before and after `poll`, and before a header commits an already-read buffer. A header gap >20 seconds at production interval restarts the collector, discards the pending frame, retains totals, exposes a gap, and advances generation. Shorter delays use actual elapsed time rather than concentrating accumulated bytes into a nominal five-second rate. Clock changes do not alter this monotonic clock; process birth cutoff still uses wall time conservatively.

### Measured lifecycle results

| Scenario | Result |
|---|---|
| Real macOS sleep/wake | User put Mac to sleep at 23:57:35 +0800 on Sep 28; `pmset` records 31 seconds asleep, DarkWake at 23:58:06 and FullWake at 23:58:08. Same Tauri process survived. Generation 1 → 2, visible gap, first new baseline had exactly zero rates and unchanged totals. |
| Wi-Fi use after wake | Known router transfer succeeded after wake: 2,679-byte HTTP body, 2,917 observed socket bytes across both directions. This establishes connectivity after wake, not a capture of every association event. |
| Wi-Fi off/on with Tauri alive | Wi-Fi power was off for 12.25 seconds. Offline router request failed with `Network is unreachable` and zero counted bytes. After re-enable and reconnect, the same Tauri process captured 2,803 down / 114 up bytes; independent All and Wi-Fi sources matched exactly before and after. No collector restart was needed (generation 1 throughout), no gap or negative rate occurred. |
| Collector SIGKILL | Verified test child killed; same Tauri app continued, generation 1 → 2, baseline rates zero, totals unchanged across restart, then known traffic resumed. |
| Collector SIGSTOP / stall | Stopped child expired after the >20s guard; worker killed/reaped it and started generation 2. Same zero baseline and resumed traffic checks passed. |

All recorded session totals were monotonic and rates finite/nonnegative. No cross-generation counter jump was credited after sleep or either fault. Whole-system maxima include unrelated real background traffic; no arbitrary peak-rate threshold is used to call traffic bogus. The evidence is the unchanged totals and zero-rate restart baseline followed by verified transfers. A single ~31s sleep does not validate long hibernation, every power mode, every VPN, or many sleep cycles.

## Performance methodology

`scripts/m2_performance.py` measures cumulative `ps time` CPU deltas divided by elapsed monotonic wall time, one core = 100%. Visible/hidden/debug/core phases settle for 20 seconds; the corrected standalone tray phase settles for 90 seconds, sampling then runs every five seconds for at least 60 seconds (13 readings). RSS is summed resident memory, not unique physical footprint; shared pages may be counted more than once. No Vite or synthetic network-transfer workload runs during performance windows. Ordinary apps remain open, so these are single-machine observations, not benchmark guarantees.

Release visible/hidden measurement transitions the **same warmed app**. A separate corrected tray-only run starts with the WebView destroyed and uses 90 seconds of settling; it is explicitly a fresh technical tray instance. The original warm tray attempt is retained as invalid evidence: an unrelated Safari Web Content process was swept into the helper set, and BandPeek’s GPU helper terminated inside that window, preventing a complete CPU total. Launch Services identified the unrelated helper as Safari. No result from that invalid window is used in the performance table. A second ownership-checked tray run with 60s settling still saw the GPU helper terminate during sampling; that transient window is also retained and excluded from the steady CPU total. The final tray result waits 90s for teardown before measuring. This tests both hiding an existing WebView and operating without one, but the standalone tray memory result is not an exact before/after subtraction from the warm visible instance. A minimal “BP test” menu offers Show debug window and Quit, enabled only through `--validation-mode`; normal launch behavior is preserved. A separate debug native build with embedded production frontend measures the visible debug table. The valid visible/hidden/debug records contain the three helper PIDs created with the respective app; hidden retains the visible cohort. The corrected harness freezes that cohort, checks new helper Launch Services names for BandPeek, and does not sweep in new Safari helpers at later phases. Candidate commands, labels and samples are saved privately. The original visible/hidden/debug samples predate the label check; their three-helper launch cohorts and lifecycle support attribution, but a concurrently launched helper is a general limitation of the old method. Disappearing processes make a window's combined CPU unavailable rather than being silently treated as zero. Core-only two- versus five-second samples measure interval overhead separately. Do not infer that debug is faster than release from these independently timed windows; background traffic, row counts and WebKit work vary.

### Settled desktop measurements

CPU uses one core = 100%; absent helpers are shown as zero. Tray is a fresh instance with no WebView after 90s settling, not the invalid warm-teardown attempt.

| State | Native | nettop | GPU | WebContent | Networking | Combined | Measured |
|---|---:|---:|---:|---:|---:|---:|---:|
| Debug visible | 0.164% | 0.230% | 0.395% | 0.329% | 0.000% | **1.118%** | 60.83s |
| Release visible | 0.165% | 0.313% | 0.609% | 0.510% | 0.000% | **1.596%** | 60.77s |
| Release hidden | 0.050% | 0.099% | 0.413% | 0.430% | 0.000% | **0.992%** | 60.47s |
| Release tray only (fresh instance) | 0.214% | 0.346% | 0.000% | 0.000% | 0.016% | **0.577%** | 60.70s |

Mean resident memory in MiB; last column is the peak *simultaneous summed* sample, not the sum of independent component peaks.

| State | Native | nettop | GPU | WebContent | Networking | Combined mean | Combined peak |
|---|---:|---:|---:|---:|---:|---:|---:|
| Debug visible | 98.60 | 2.74 | 24.63 | 40.01 | 7.49 | **173.47** | 176.70 |
| Release visible | 91.51 | 2.76 | 24.47 | 42.06 | 7.27 | **168.07** | 177.41 |
| Release hidden | 82.26 | 2.55 | 21.20 | 35.01 | 3.09 | **144.11** | 147.56 |
| Release tray only (fresh instance) | 72.61 | 2.73 | 0.00 | 0.00 | 2.94 | **78.28** | 82.67 |

Hiding did **not** suspend WebKit: the same GPU/WebContent/Networking PIDs remained alive. Their combined CPU fell from 1.119% to 0.843% (about 25%); WebContent itself fell only from 0.510% to 0.430%. Background work varied, including collector CPU, so this is an observed reduction rather than an isolated causal estimate. In stable tray-only operation, WebContent and GPU were absent; a small BandPeek Networking helper remained. Combined tray cost was 0.577% CPU and 78.28 MiB RSS. Destroying a WebView is materially different from merely hiding its window, but this is a technical measurement mode, not the finished tray design.

Performance captures preceded the final extra header-commit stall check (the same clock read is retained); final source was rebuilt and unit/lint checks rerun afterward. No claim of a performance improvement from that final guard is made.

### Interval CPU cost (core-only, no WebKit)

| Interval | Native CPU | nettop CPU | Combined CPU | Combined mean RSS | Duration |
|---|---:|---:|---:|---:|---:|
| 5s | 0.033% | 0.279% | **0.312%** | 5.37 MiB | 60.84s |
| 2s | 0.033% | 0.559% | **0.592%** | 5.48 MiB | 60.80s |

This is a sequential, single-window-per-interval comparison with ordinary background traffic, not a statistically isolated cost ratio. The probe emits validation snapshots; production desktop behavior is measured separately. Two-second sampling roughly doubled nettop CPU here (+0.28 percentage points combined) without fixing the fundamental immediate-exit gap. Keep five seconds for this milestone; make any future interval change a separate product decision supported by representative workloads.

## Persistence recommendation

**Do not proceed to persistence as an accurate internet-usage ledger or with the promised Wi-Fi/Ethernet history selectors.** First accept the narrower [macOS accuracy contract](macos-accuracy-contract.md). Persisting *observed All-interface activity* can then be reasonable, provided gaps/generations, baseline policy and lost-coverage semantics are retained and exposed. Saving imperfect observations does not repair missing bytes. No persistence was implemented in this milestone.

## Changes and checks

- Added a sleep-inclusive clock through the existing SDK-compiled C adapter, and checked the stall threshold on both sides of `poll` and before header commit. Retained cumulative counters, birth identity, framing, reset arithmetic, metadata resolution and the production five-second interval.
- Added a bounded 1–60s interval seam for the collector-only probe. The normal desktop still calls the five-second default.
- Added opt-in technical tray/hidden/destroyed-WebView validation states, timed graceful exit and optional snapshot logging. Normal launches retain Milestone 1 behavior. No polished UI work.
- Added reproducible interface, churn, Tauri lifecycle and performance scripts. Raw evidence remains ignored in `.validation/milestone2/`; reports contain summarized measurements.
- **11 Rust tests passed**, including existing baseline, birth/exit/reappearance/PID reuse, directional reset, restart, atomic rejection, CSV and identity tests; added long-pause rate/rebaseline and suspend/stall-clock guard tests.
- Rustfmt check, all-target Clippy with warnings denied, TypeScript/Vite build, release and debug native builds with embedded assets passed. Python harness syntax checked. Physical sleep, real network loss and WebKit scheduling are integration measurements, not mocked unit-test claims.

## Reproduce

```sh
source scripts/env.sh # only for the project-local Rust installation
npm run build
cargo build --release --manifest-path src-tauri/Cargo.toml --features custom-protocol
cargo build --manifest-path src-tauri/Cargo.toml --features custom-protocol
python3 scripts/m2_interfaces.py
python3 scripts/m2_churn.py
python3 scripts/m2_lifecycle.py kill
python3 scripts/m2_lifecycle.py stall
python3 scripts/m2_lifecycle.py wifi # temporarily disables Wi-Fi; restores it
python3 scripts/m2_lifecycle.py sleep # follow READY instruction, sleep/wake manually
python3 scripts/m2_performance.py --profile release
python3 scripts/m2_performance.py --profile debug --visible-only
python3 scripts/m2_performance.py --profile release --tray-only
python3 scripts/m2_performance.py --interval 5
python3 scripts/m2_performance.py --interval 2
```

Use ordinary-user execution outside an agent sandbox that blocks the statistics socket. No root is needed for the collector. Scripts intentionally generate traffic or disrupt their test collector; the Wi-Fi test affects the Mac's shared connection. Raw captures include process names and endpoint addresses and remain ignored, local evidence. No runtime destination inspection or disk logging was added to normal BandPeek collection.

## Remaining manual coverage / decision gates

- Plug in a real Ethernet link, verify its hardware type and route, and transfer known bytes to both a LAN peer and a public endpoint. Repeat while moving a long-lived process between Wi-Fi and Ethernet. Do not treat a wired-filter zero on this Wi-Fi-only test as validation of Ethernet accounting.
- With a user-selected active VPN and reachable tunnel endpoint, compare inner socket, tunnel and underlying-interface rows; test split/full tunnel and reconnect. The current disconnected VPN inventory cannot establish those semantics.
- Repeat sleep/wake for longer sleep, hibernation and several cycles; include in-flight transfers and failed reconnect. The completed short sleep establishes one tested path only.
- Real PID wrap/reuse with retained terminal rows remains untested; deterministic identity reuse tests passed. Never substitute cached PID-only identity to improve the churn numbers.

These are limitations of the measured scope, not implemented or silently marked successful scenarios.

## Sources

The installed `/usr/bin/nettop -h` defines interface filtering as sockets bound to an interface type and `external` as non-loopback types. This is directly checked against traffic, rather than treated as a guarantee of historical attribution. Apple's [XNU statistics definitions](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/net/ntstat.h) distinguish socket/provider statistics and interface-related fields; these private structures are not imported as a BandPeek ABI. Apple's [continuous clock API](https://developer.apple.com/documentation/kernel/1646199-mach_continuous_time) supplies suspend-inclusive time. The socket-versus-wire wording is also grounded in the exact controlled plain-TCP payload counts and HTTPS excess measured here and in Milestone 1.

See the [proposed macOS accuracy contract](macos-accuracy-contract.md).
