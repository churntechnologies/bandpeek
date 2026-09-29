# beta.2 live rates and popup validation

Local macOS 27 / Apple Silicon validation, 2026-09-29. This report supersedes the
five-second cadence and destroy-on-dismiss popup decisions in earlier milestone
reports. No release tag or publication is part of this work.

## Diagnosis

The measured beta.1 rate path is:

1. One persistent `nettop -P -L 0 -s 5 -n -x -J bytes_in,bytes_out` subprocess.
2. Repeated CSV headers frame cumulative process RX/TX counters. Because there is
   no explicit end marker, the next header commits the preceding complete frame.
3. Verified PID + process birth time identities establish counter baselines.
   Directional nonnegative differences are summed, then divided by actual
   sleep-inclusive elapsed seconds. The first frame establishes a baseline.
4. The native title thread checked the sample sequence every second. Visible
   WebViews separately fetched live rates every five seconds.
5. Native AppKit applies decimal B/s formatting to the attributed status title.

A 60-second sustained loopback HTTP download delivered 833,617,920 payload bytes
at 13,892,867 B/s. The peak system RX rate was 14,079,869 B/s, exactly matching
independent reconstruction from the **same raw stream consumed by BandPeek**.
All 16 sample deliveries matched the calculated rates and native formatting.
The sample publication delay was approximately 4,983–5,005 ms, before the native
thread's additional wait. This produces roughly 5–10 seconds of lag between a
traffic transition and a corresponding title. A low pre-traffic rate can remain
visible during an active transfer. This is a timing defect, not acceptable
sampling error.

The sustained reproduction did **not** reproduce a persistent 1.5 KB/s reading
throughout a long download. It rules out arithmetic/formatting/propagation loss
for that fixture; it does not establish that every browser/OS workload has no
collection issue. Raw versus resolved rows remain visible in diagnostics so
identity omissions can be distinguished from stale samples.

The popup baseline reproduced the reported delay: first click to content
1,688.7 ms, subsequent median 1,611.6 ms (10 opens). Recreating the WebView left
its initial history poll skipped while hidden; the 1,500 ms fallback showed it,
then focus triggered loading. Both destruction and the hidden initial load
contributed to the delay.

## Changes

- Sample `nettop` every **2 seconds**, keeping one collector and the existing
  PTY, idle stdin, controlling-terminal hangup, stall/restart and identity rules.
- Publish rate notifications for every snapshot, including zero-rate gaps that
  retain the previous sample sequence. The native title blocks on notifications;
  it has no periodic sampling timer. Visible pages receive rate events; live
  rates have no UI polling timer. Initial/focus/open reads provide current state.
- Keep history accumulation in the existing per-app minute buckets and
  60-second database flush policy. Faster collection does not create per-sample
  transactions. No-record validation also drains unused history deltas.
- Initialize one hidden popup window/WebView at startup. Explicit dismissal,
  Escape, close requests, and focus loss hide it. Cached content is shown on
  reopening, refreshed by events. Readiness resizes the retained view without
  reopening a dismissed popup. Rapid clicks during loading cannot create a
  second instance; the blur-click debounce remains.
- Load popup history once during initialization even while hidden. Hidden popup
  pages receive no periodic live events. The main window retains its previous
  release-on-close policy.

## Reproduce

Build the embedded frontend and release binary:

```sh
npm run build
source scripts/env.sh # only for the optional repository-local Rust toolchain
cargo build --manifest-path src-tauri/Cargo.toml --release --features custom-protocol --offline
python3 scripts/beta2_live_validation.py --label local --interval 2
python3 scripts/beta2_live_validation.py --label https --interval 2 --url https://proof.ovh.net/files/1Gb.dat
python3 scripts/beta2_popup_validation.py --label fixed --cycles 100 --seconds 60
python3 scripts/beta2_lifecycle_validation.py
```

These harnesses use temporary history/settings files and cleanly quit their own
instances. Run them sequentially with other BandPeek instances closed to avoid
confounding resource attribution. The local HTTP server sends for 60 seconds;
the downloader remains alive after completion so terminal deltas keep a verified
identity. Local RX+TX counts both the receiving client and sending server.

Opt-in `BANDPEEK_LIVE_TRACE_PATH` JSONL output records raw source counters,
resolved identities, previous counter baselines, per-process and aggregate
RX/TX deltas, actual intervals, B/s calculations, delivered values, formatted
strings, and the native button's actual attributed title. Normal launches do no
trace serialization or file I/O. Evidence lives in ignored `.validation/beta2/`
because it contains local process names and paths.

External speed tests report bits/sec: multiply BandPeek's B/s by **8**, then
use decimal million for Mbps. For example, 14,079,869 B/s is 112.639 Mbps;
1.5 KB/s is 0.012 Mbps. This unit distinction cannot explain that discrepancy.

Popup content latency includes stdin dispatch into the production tray-click
path, native show, and two animation-frame callbacks checking popup content.
It measures rendering readiness, rather than merely successful `show()`. It is
not an optical display measurement. CPU uses cumulative process CPU-time delta
against monotonic elapsed time (100% = one core), with 60-second windows. RSS is
summed across native, collector, and Launch Services-attributed WebKit helpers;
shared pages can be counted more than once. WebView pointer identity and registry
counts provide the instance check; macOS `heap` class counts are supplementary
and do not reliably expose WebKit subclasses on this system.

## Final measured results

| Measurement | Result |
| --- | --- |
| First popup open after initialization, final beta.2 binary | 61.8 ms to content |
| Subsequent opens, 99 samples, final beta.2 binary | median 42.7 ms; p95 68.2 ms; max 133.8 ms |
| Native show path, final 100 samples | 1.2–15.1 ms |
| Tray only, 60 s | native 0.440% CPU / 95.95 MiB RSS; combined 0.961% / 165.82 MiB |
| Popup visible, 60 s | native 0.619% CPU / 97.45 MiB RSS; combined 2.280% / 176.96 MiB |
| Tray after 100 cycles, 60 s | native 0.374% CPU / 103.52 MiB RSS; combined 1.121% / 204.10 MiB |
| Popup instances over 100 cycles | one identical WKWebView pointer; registry contains one WebView |
| Collector | one child throughout; none survived graceful exit |
| Local sustained download | 60.00 s, 750,321,664 payload bytes; mean payload 12.505 MB/s |
| Local peak system RX | 13.409 MB/s = 107.271 Mbps; source reconstruction equals BandPeek |
| Local traffic response | native rise in 3.753 s; sharp fall in 3.689 s after completion |
| Local fixture RX idle | native delivery in 5.691 s; includes source-counter update and complete-frame delays |
| Retained HTTPS downloader | 60.11 s, 88,408,064 payload bytes; no fixture delta errors |
| HTTPS peak system RX | 1.926 MB/s = 15.406 Mbps; exact source reconstruction |
| SQLite after HTTPS | integrity `ok`; durable RX 89,848,445 B / TX 520,380 B exactly equal summed accepted deltas |

The first-open measurement follows initialization and settling; an artificial
stdin click at setup completion can precede AppKit status-item layout and receive
an off-screen anchor. It is not a meaningful real-click latency measurement.
The normal tray handler uses the actual mouse event's rectangle. Later smoke
measurements with `document.visibilityState=hidden` were discarded; animation
frames cannot measure visible content on a sleeping or locked display. The popup
harness now holds a temporary `caffeinate` display assertion until app exit,
without changing power preferences or bypassing the lock screen.

The retained fixture's raw directional differences agree with its collector
increments. Summed resolved increments agree with aggregate deltas; dividing by
measured intervals agrees with rates. Every sample's delivered numeric rate and
actual attributed native title agree with its calculation and decimal formatting.
Independently summing all raw rows without identity policy can differ at births
and after unrelated processes exit. The earlier curl HTTPS experiment showed a
735,584 B terminal RX delta omitted after curl had exited; retaining the fixture
process verifies terminal deltas without weakening identity safety.

All three menu-bar modes, fixed widths, native fonts/template mark, representative
rate formatting, and saved-mode relaunch passed. Focus-loss dismissal passed;
late readiness cannot reopen a hidden popup. The 100-cycle run opened/closed the
main window once to test focus loss, explaining the second retained native window
and its scheme handlers. RSS growth includes WebKit caches and that main-window
visit; stable pointer/registry counts rule out a new popup WebView per cycle.

Regression coverage includes sustained two-second rates returning to zero,
notification of gaps with unchanged sequence, removal of dead subscribers,
retained readiness on dismissal and cancellation during initialization. Existing
history, settings, update pause/flush/resume, and Launch at Login tests remain.
Launch at Login registration and the production updater key/endpoints/signature
policy were not changed. No OS login preference or installed app was modified.


The final beta.2 binary also passed native collector SIGKILL and SIGSTOP recovery
(generations 1 → 2 → 3, first rates zero, monotonic totals, one child). Previous
children were reaped, normal exit flushed SQLite with integrity `ok`, and killing
the app caused controlling-terminal hangup to reap its child. Final checks passed:
62 Rust tests, 17 release policy tests, Clippy with warnings denied, rustfmt,
TypeScript/Vite build, and offline release build. An ad-hoc-signed local app
bundle was built without installation, tagging, or publication.

A final 100-cycle run on the beta.2 binary, with the temporary display assertion,
passed every rendered-content check and focus-loss check. Every open reused the
same native WKWebView pointer with exactly one registered popup WebView. All
subsequent opens were below 150 ms, including the 133.8 ms maximum. The previous
full performance run measured 51.1 ms median / 70.3 ms p95 / 79.1 ms maximum;
its CPU/RSS windows remain the resource measurements above. Final process audit
found no surviving BandPeek, nettop, or caffeinate processes.
