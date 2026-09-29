# macOS collector decision — Milestone 1

> Milestone 2 follow-up: [measured accuracy and lifecycle report](milestone-2.md) and [proposed accuracy contract](macos-accuracy-contract.md). The sleep-inclusive clock and opt-in measurement tray described there supersede the corresponding Milestone 1 implementation details.

Investigated on macOS 27.0 (26A428), Apple Silicon, 2026-09-28.

## Choice

One persistent `/usr/bin/nettop -P -L 0 -s 5 -n -x -J bytes_in,bytes_out` process. No `-d`: the input is cumulative OS counters, not already-computed deltas. No separate TCP/UDP processes and no connection rows to accidentally add to process summary rows. `-n` prevents DNS resolution. An open, unwritten stdin pipe is essential: `/dev/null` caused a measured ~145% CPU nettop EOF spin on this Mac; `-c` did not cure it. Keeping stdin open reduced that isolated test to ~0.20%. Output-only PTY makes CSV line-buffered (ordinary pipes were buffered in the initial probe). The worker polls at most twice per second for bounded shutdown, and otherwise sleeps in `poll`; metadata/accounting runs once per sample. The UI pulls snapshots every five seconds with no animation and reuses one Intl.NumberFormat instance instead of recreating locale formatting work for every number. The initial 3-second configuration was increased to 5 seconds after measuring visible WebKit overhead.

The tool's repeated header terminates the previous sample. This deliberately adds one interval of display latency; there is no reliable end marker and treating a quiet pipe as a completed sample could accept a truncated sample. EOF, failures and stalls discard the pending frame. Headers/column order are validated against the tested format. Unknown formats fail visibly and retry with bounded backoff. Malformed or duplicate rows invalidate the entire sample; a later valid frame catches up from the last valid counters. There is no shell interpolation or user-controlled command.

## Alternatives

- Apple's public Network framework transfer reports operate on an `nw_connection_t` owned by the caller, not every other application's sockets. They cannot satisfy system-wide attribution. See [data transfer reports](https://developer.apple.com/documentation/network/nw_data_transfer_report_t).
- Network Extension content filters introduce filtering machinery, capabilities/entitlements, installation and user approval outside this project's monitor-only scope. See [Apple content filter providers](https://developer.apple.com/documentation/networkextension/content-filter-providers).
- Direct `NetworkStatistics.framework`/NStat and `com.apple.network.statistics` interfaces are private. Apple's [XNU statistics definitions](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/net/ntstat.h) expose internal structures, not a supported third-party ABI contract. A direct implementation would couple BandPeek to OS-specific layouts, undocumented permissions and changes; shipping/notarization/App Store compatibility would need independent assessment. It might remove subprocess overhead, but no measured benefit justifies those maintenance/compatibility risks here. BandPeek does not link or invoke that private API itself. Apple's nettop uses Apple's implementation internally; its CLI is still an OS dependency, not a promise that CSV will never change.
- Public `libproc` and `sysctl(KERN_PROC_PID)` ARE used for process identity, not network counters. A tiny C ABI adapter compiles the SDK's structures instead of reimplementing C layouts in Rust. Start-time lookup via libproc alone failed for root-owned services; the public sysctl fallback succeeded without root. Missing identity remains explicit and that row is skipped, never assigned PID-only history.

## Accounting contract

`ProcessId = (PID, process birth in microseconds)`. Birth is checked on every observation and before/after initial executable resolution. Metadata is cached by this identity; bundle metadata is cached separately. Chromium/Electron helpers resolve to the outermost `.app`. Finder's bundle name is preferred, with a known `com.openai.codex` → Codex normalization: this Mac's bundle is installed as ChatGPT.app and all its display-name fields say ChatGPT. Bundle ID/icon reference come from Info.plist, with executable/process fallback. Shared WebKit/system helpers cannot always be attributed to the originating app; no guessing from parent PID.

`OsCounters` are cumulative TCP/UDP socket counts supplied by nettop. `SessionBytes` are BandPeek's computed observations. Existing processes get a zero baseline. A process born after collection starts gets its first observed counters included, provided a baseline sample has completed. This counts early bytes from new processes without importing old traffic. Disappearing processes remain in the session table; their baselines survive temporary absence. PID reuse creates a separate row. Per-direction decreases rebaseline that direction with a zero delta. No unsigned subtraction wraps.

Collector failure/stall resets all baselines and live rates, keeps session totals, increments the generation and exposes a gap. First sample after restart is a baseline. We do not invent traffic during a gap. Rates use actual monotonic sample spacing, so rejected frames catch up over a longer interval. Birth identity uses OS wall time; major wall-clock changes can conservatively omit a new-process initial count. This is not a billing meter.

Retired rows are capped at 2,048 when possible; active rows are retained. Eviction preserves bytes in an archived bucket and advances the safe birth cutoff, so an evicted returning process is baselined rather than counted twice. Bundle cache is capped at 256; stream lines and frame sizes are bounded. Nothing is written to disk by the collector itself.

## Interface scope

All interfaces currently includes Wi-Fi, Ethernet, loopback, LAN, VPN/tunnels and other sockets as exposed by nettop. It is **not internet-only**. Loopback client and server are separate socket owners; summing both endpoints counts a local transfer in both rows. TCP/UDP totals are not physical link totals, and HTTP/TLS protocol data can exceed application payload size.

The model defines All/Wifi/Ethernet. Only All is exposed. `nettop -t wifi` and `-t wired` can filter a live source, but changing filters destroys the comparability of per-process cumulative counters and cannot retrospectively split a session. Running three sources increases overhead. A next milestone should evaluate per-interface/per-flow counters, transitions, VPNs and unbound sockets, with one source if possible. Do not subtract All from Wi-Fi to infer Ethernet or present a selector that merely relabels the same totals.

## Known coverage limits

Polling cannot guarantee capture of a process that starts, transfers and exits entirely between samples. Even a visible process may exit before its final counters can be paired with a live birth identity. Such rows are skipped and counted as unresolved observations. Historical exited nettop rows can continue appearing; they are not evidence that a process is alive. Process identity access may vary with OS policy. Cached metadata may lag an `exec` within the same process lifetime. Counter resets/closed-flow accounting and collection gaps can omit bytes; the tool never compensates by inventing totals.

No root/sudo, administrator privileges, Full Disk Access, packet capture, VPN, network extension, login item, account or analytics permission was required. The agent's execution sandbox blocks nettop's statistics socket and process inspection; validation used ordinary user execution outside that sandbox. This is distinct from requiring root or an end-user permission prompt. Mac App Store sandboxing/distribution has not been validated; no installer or signing work belongs to this milestone.
