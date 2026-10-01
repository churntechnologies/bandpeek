# beta.3 VPN teardown and topology recovery

Local validation on Apple Silicon, macOS 27.0 (26A428), 2026-10-01.
Candidate version: `0.1.0-beta.3`. No tag, publication or installed-app replacement.

## Reproduce before changing behavior

An instrumented beta.2 binary was built and preserved in ignored
`.validation/beta3/bandpeek-instrumented-beta2`. Only diagnostics were added for
the first two runs; topology recovery and parser behavior were still beta.2.
The installed app was left separate from isolated validation instances, which
used temporary databases/settings and audited children by their actual parent PID.

ProtonVPN reproduced the defect. Its `utun6` interface and primary IPv4 path
appeared on connect, then disappeared on disconnect. The persistent `nettop`
PID stayed alive in generation 1 and continued delivering headers. Accepted
samples stopped at sequence 33 in the first run; notifications and the actual
native title continued showing zero for 115 seconds until the harness quit.
The normal header-stall watchdog therefore had no reason to restart it.

A second run captured rejected frames and the exact invalid line:

```text
kernel_task.0,0,0,
```

After teardown this row appeared in every frame. The beta.2 parser required a
positive PID, marked the entire frame invalid, and discarded the traffic of all
other processes. Ten consecutive frames were discarded in the captured tail.
The retained fixture's RX counters in rejected frames still increased:
136,609,675 → 139,394,955 → 142,147,467 bytes. Its resolved PID/birth identity was
stable. Native zero rates matched delivered zero notifications.

This is a collector framing/lifecycle failure: **raw counters were advancing**;
neither stale zero source counters, counter resets, invalid fixture identities,
nor formatting caused the observed freeze. No recovery was inferred from zero
rates alone. Restarting with a fresh collector avoids the affected stream, but
the parser also needs to handle the legitimate kernel row.

## Recovery implementation

- Accept the specific `kernel_task.0` CSV row, retaining raw diagnostics and
  counting it as unresolved. PID 0 never gets a fabricated application identity;
  malformed numbers and other unexpected PID-0 rows remain invalid.
- Query public macOS `getifaddrs` and SystemConfiguration primary IPv4/IPv6 state
  once per second on the existing collector worker. Normalize/sort/deduplicate
  interface indices, significant flags, addresses, masks, primary interfaces,
  services and routers. Include `utun*` membership even without an address.
  There is no shell polling, UI timer or additional collector/monitor thread.
- Ignore unaddressed non-tunnel interfaces and Wi-Fi peer discovery (`awdl`,
  `llw`, `ap`, `nan`), packet counters and transient flags. Require one second
  of stable changed state; changes reverting to the accepted state cancel the
  pending restart. Maintain at least six seconds between topology restarts.
  The watcher survives collector generations, preventing repeated restarts for
  an already accepted change. Failed interface enumeration does not look like
  all interfaces disappeared.
- Return an explicit topology exit, drop/kill/wait the old child, record a gap,
  publish zero/starting notifications and immediately create exactly one new
  child. Rebuild counter and identity baselines; preserve session and durable
  history totals. Topology gaps do not force traffic-buffer flushes.
- Three consecutive malformed frames now trigger the existing fault retry
  path. Crash, closed stream, suspend and header-stall detection retain their
  backoff. Valid zero-rate frames never trigger a restart.

The PTY, idle stdin and controlling-terminal hangup protection remain. Live
collection stays at two seconds; framing still needs the next header to commit
a complete sample. History keeps minute aggregation and its existing buffered
flush cadence. Popup and updater behavior have no implementation changes.

API references: [Apple getifaddrs manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/freeifaddrs.3.html),
[Apple SCDynamicStore](https://developer.apple.com/documentation/systemconfiguration/scdynamicstore-gb2).

## Measured validation

| Check | Result |
| --- | --- |
| Final consecutive ProtonVPN off → on → off cycles | 10 real cycles, 20 transitions, exactly 20 restarts |
| Additional initial fixed run | 3 cycles, exactly 6 restarts |
| VPN modes observed | Stealth in reproduction/initial run; WireGuard in final run |
| Detected VPN disconnect → live native traffic, all 10 final disconnects | 5.047–5.651 s |
| Disconnect action → live native traffic, timestamped cycles 9/10 | **7.162 / 7.034 s** |
| Detected VPN connect → live native traffic | 5.052–5.716 s |
| Wi-Fi off for 5 s → on | 2 transitions, 2 restarts; Wi-Fi power restored and HTTPS HTTP 200 |
| Detected Wi-Fi disconnect/reconnect → native fixture traffic | 5.070 / 5.546 s |
| Final run including Wi-Fi | 393 accepted samples, 23 starts and 23 reaps, zero rejected frames |
| Collector ownership | Never more than one child per instance; each predecessor reaped before successor |
| Every new generation's first sample | Zero deltas/rates, no counter spike |
| Persisted final-run totals | RX 990,052,783 B / TX 959,593,493 B; exactly summed accepted deltas |
| Session totals and SQLite | Monotonic across generations; SQLite integrity `ok` |
| Ordinary idle fixture window | 65 s, 32 accepted samples, zero fixture deltas, zero restarts |
| SIGKILL / SIGSTOP collector recovery | Passed; old children reaped, first rates zero, monotonic totals |
| Graceful quit / abrupt app death | No nettop child from either test instance survived |
| Popup 100 opens | First 51.5 ms; subsequent median 37.8 ms, p95 67.7 ms, max 69.5 ms |
| Popup instance/focus checks | Same WKWebView pointer across all opens; focus loss passed; no orphan on exit |

Recovery times use sustained **loopback TCP traffic**, with both endpoints kept
alive to preserve identities. They measure the native title returning to high
traffic after real VPN/Wi-Fi topology changes, not WAN download throughput.
Wi-Fi restoration was additionally checked with ordinary HTTPS. Detected-change
measurements exclude up to one second of interface probe latency and VPN-client
teardown time. The timestamped Disconnect-action measurements include both.
These are local measurements, not a cross-macOS timing guarantee.

Independent raw replay keyed by generation + PID + birth verifies existing
counter baselines/directional differences, resolved source values, aggregate
deltas and rates for all 393 accepted samples. Rust regression tests cover the
new-birth cutoff and reset behavior. The harness checks live notification and
actual native attributed-title agreement for every sample, monotonic session
totals, exact durable delta sums, first-generation baselines and child cleanup.

A first popup resource benchmark became hidden during its 60-second visible
window. Its popup resource numbers are invalid and excluded. The separate
100-cycle latency/identity retry passed. That interrupted run measured native
tray CPU 0.686% and RSS 93.37 MiB while other validation activity was running;
it is not a clean comparative idle resource benchmark. No new memory/performance
claim beyond the passing popup latency and identity checks is made.

Checks passed: 66 Rust tests (56 core + 10 shell), 22 release-policy tests,
Clippy with warnings denied, rustfmt, TypeScript/Vite, offline optimized build,
and the local app bundle. Production updater keys/endpoints and install policy
are unchanged; update pause/flush/resume regression tests passed.
The ad-hoc bundle's signature verified with `codesign --verify --deep --strict`
and its embedded version is beta.3. A separate 20-second bundled-binary smoke
test audited 10 raw samples, one child, zero rejected frames, exact durable
totals and clean exit.

## Reproduction tools and private evidence

```sh
source scripts/env.sh # optional repository-local Rust toolchain
npm run build
cargo build --manifest-path src-tauri/Cargo.toml --release --features custom-protocol --offline
python3 scripts/beta3_topology_validation.py --label fixed --seconds 1200 --expect-recovery
# While running: connect/disconnect ProtonVPN, letting each state settle at least
# eight seconds. Perform 10 cycles, then a Wi-Fi disconnect/reconnect.
# End cleanly before the deadline by creating .validation/beta3/fixed.stop.
python3 scripts/beta3_topology_validation.py --audit-trace .validation/beta3/fixed.jsonl
python3 scripts/beta2_lifecycle_validation.py
python3 scripts/beta2_popup_validation.py --label beta3-latency --cycles 100 --seconds 0
CARGO_NET_OFFLINE=true npm run tauri -- build --bundles app
```

The topology harness prints its app/fixture PIDs. While it is still running, an
idle test can pause only its traffic fixtures and always resume them:

```sh
python3 scripts/beta3_idle_validation.py --app-pid APP_PID --fixture-pid FIXTURE_PID \
  --trace .validation/beta3/fixed.jsonl
```

Opt-in `BANDPEEK_LIVE_TRACE_PATH` records interface snapshots, collector start/reap
PID/generation/reason, invalid lines and rejected raw frames, accepted raw counters,
resolved identities, process baselines/deltas, aggregate deltas/session totals,
live notifications and actual native menu values. Normal launches have no trace
serialization/file I/O. Evidence is ignored because it contains local process
identities and network addresses. Final evidence: `.validation/beta3/summary.json`,
`fixed-ten.jsonl`, `fixed-ten.json`, `fixed-ten.idle.json`, `disconnect-actions.json`,
`wifi-actions.json`; popup/lifecycle evidence uses the existing
`.validation/beta2/` harness directory with beta.3 labels/binary.

The installed executable is lowercase `bandpeek`; `pkill -x BandPeek` does not
terminate it. The harness quits its own app through the normal validation quit
path, rather than relying on a guessed process name. All assertions about orphan
cleanup refer to the owned test instances; preexisting installed instances are
not treated as orphans.
After validation, the installed beta.2 app was quit through its normal menu.
A process audit found no BandPeek/nettop/validation/caffeinate process remaining.
It was then relaunched from `/Applications/BandPeek.app`; the final audit found
only that app and its single owned nettop child. Its installed files were not
replaced. ProtonVPN was left disconnected and Wi-Fi restored to On.
