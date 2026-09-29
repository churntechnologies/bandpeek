# Milestone 4: final approved UI

Measured on Sep 29, 2026 (local time, UTC+8) on macOS 27.0 (26A428), Apple Silicon, as the ordinary logged-in user, with the same Mac and toolchain as Milestones 1–3.

The collector, persistence and history architecture from Milestones 1–3 is unchanged. Integration testing exposed five bugs, all fixed below. Two involved the collector/model boundary. None required redesign.

## What was built

- **Main window** (dark and light). This reproduces `mockups/main-*.png` from the final handoff (`BandPeek-design-handoff/`), using the handoff's token values, spacing, type scale and component rules. It has the brand header with the final 4a open-lens mark, live ↓/↑ speeds, the Today / Yesterday / Last 7 Days / Last 30 Days range control, the app filter, Settings, the Downloaded / Uploaded / Total summary with its split bar, the Application / Download / Upload / Total / Share table, and the status bar. All values come from SQLite through `get_history`, and the header rates come from the collector.
- **Menu-bar item.** This is `brand/png/bandpeek-mark-mono-36.png`, used as a template image (`isTemplate`), plus two-line ↓/↑ rates (10 pt, right-aligned, line height 1.05). The item has a fixed width, so neighbouring menu-bar items don't shift as the digits change.
- **Tray popup** (320 pt, dark and light). It shows live ↓ Download / ↑ Upload, the Today total with ↓/↑, the top 5 apps today with split bars, and Open BandPeek / Quit. There's no Pause.
- **Settings** is minimal: Appearance (System / Light / Dark), Units (GB / GiB), History retention (30 / 90 / 180 days / 1 year) and Clear history, which asks for confirmation inline. It also carries a short, accurate data note about scope, accuracy and minute-buffered writes.
- **Themes.** System, Light and Dark apply app-wide (`AppHandle::set_theme`), so native chrome and the WebViews follow the same choice.

Sampling stays at 5 seconds. The 1 s and 2 s experimental modes aren't exposed.

## Design differences (smallest practical adaptations)

| Difference | Reason |
|---|---|
| The `All networks / Wi-Fi / Ethernet` header selector and its divider are removed. The speeds keep their position at the right of the header. | Milestone 2 decision: the collector can't partition per-process history across interface migrations. |
| The popup's "All networks" label next to "Top apps today" is removed. | It named the network scope that V1 doesn't offer, so it's removed along with the selector. |
| Live speeds refresh every 5 s, not every 1 s. | The collector's presentation cadence (the V1 5 s sampling decision). |
| Letter-tile app icons are replaced by real Finder icons. CLI tools and daemons get a neutral tile with a terminal glyph. | The handoff says the tiles are placeholders. |
| Dates follow the system locale (for example "Today, 29 Sep" on this en-GB Mac). The mockup shows en-US "Sep 28". | `Intl` locale formatting. Ranges use `formatRange` ("31 Aug – 29 Sep"). |
| Status-bar dot text reads "Starting" / "Collector restarting" / "Not tracking" when the collector isn't tracking. | This is honest state. The approved text "Tracking · Stored on this device only" is unchanged while tracking. |
| Settings is an in-window sheet using the same tokens. | The handoff defines no Settings design ("not part of this handoff"). |
| The popup is a borderless window rounded through its content view's layer. The native window shadow replaces the mockup's CSS drop shadow. The 1 px frame ring is drawn inside the popup. | This uses public NSWindow/CALayer API only. Tauri's `transparent` would need the macOS private-API feature. |
| Menu-bar rate text is drawn by AppKit (monospaced-digit system font). | A native `NSStatusItem` title, not HTML. |

The main window's content area is exactly 840 × 600 below the native title bar, with a 760 × 480 minimum. On this macOS/Tauri combination the title bar overlaps a full-size content view, so the window adds the *measured* title-bar inset (32 pt here) rather than assuming a height. Native window controls are real; none are drawn.

## Tray and window lifecycle

- **Closing the main window destroys it** (the WebView, not just a hidden window). BandPeek then switches to the `Accessory` activation policy: no Dock icon, and the menu bar only. Collection and persistence run in the native process and never depend on a window.
- **Last-window exit is prevented.** `RunEvent::ExitRequested { code: None }` is prevented. Only an explicit exit (tray Quit via `app.exit(0)`, or the app menu's Quit) ends the process. Quit runs `Monitor::stop()`, which joins the collector, kills `nettop` and flushes SQLite.
- **Open BandPeek** (from the popup, or a Dock reopen) recreates the window and restores the `Regular` policy.
- **View state survives recreation.** Range, sort and filter are kept natively (`get_ui_state`/`set_ui_state`). Settings are persisted to `settings.json` next to the database, written atomically (write, then rename).
- **The popup is created on demand and destroyed when it loses focus.** It's built hidden, sized to its content by the page, then shown, so it never flashes empty. A click on the menu-bar item while the popup is open closes it; a 300 ms guard stops the blur-then-click sequence from reopening it. Escape also closes it.

## App-icon resolution

1. From the stored executable or icon path, take the outermost `.app` (helpers resolve to their user-facing app). Otherwise, for `bundle:` identities only, ask Launch Services for the bundle ID (`URLForApplicationWithBundleIdentifier`).
2. `NSWorkspace.iconForFile` is rendered into a 36 px PNG (18 pt at 2×) and handed to the page as a data URL.
3. The result is cached per identity in the native process: bounded at 512 entries, including negative results. The page also caches per WebView lifetime.
4. `exec:`/`proc:` identities (CLI tools, daemons, XPC services) deliberately get the neutral fallback tile rather than a generic executable icon.

On this Mac, every bundle row showed its real icon, and every non-bundle row used the fallback (verified programmatically; see below).

## Integration bugs found and fixed

1. **Orphaned `nettop` burning ~140% CPU after a crash.** Two orphaned collector processes from Milestone 3 test runs were still running at ~147% CPU each (parent: launchd). If BandPeek dies abruptly, the idle stdin pipe hits EOF and `nettop` spins (the Milestone 1 EOF spin).
   - Reproduced by SIGKILL: the orphan ran at 138% CPU.
   - Fix: the collector's output PTY becomes `nettop`'s controlling terminal (`setsid` + `TIOCSCTTY`). When BandPeek exits for any reason, the kernel hangs up the terminal and SIGHUPs `nettop`.
   - Verified: after SIGKILL of BandPeek, `nettop` exits within seconds. While running, it measured 0.0% CPU.
   - The two stale orphans were terminated.
2. **7-/30-day ranges were unreachable over IPC.** `serde(rename_all = "snake_case")` serialises `Last7Days` as `last7_days`, while the UI (including Milestone 3's) sends `last_7_days`. Explicit renames were added, with a round-trip test.
3. **Truncated app names.** `nettop` truncates process names to 15 characters (`com.apple.WebKi`). Non-bundle identities now display the executable's file name (`com.apple.WebKit.Networking`). Existing rows pick up the fuller name the next time the app is seen after a relaunch.
4. **Retention only applied at startup.** A long-running instance never pruned. Flushes now re-apply retention at most once an hour. `set_retention_days` prunes immediately when the setting changes.
5. **Continuous IPC loop with the window visible (found by the performance measurement: 125% CPU).** `useSyncExternalStore` was given an inline subscribe function, so every render unsubscribed and resubscribed, which restarted the live poller with an immediate fetch. Fixed with a stable subscribe function. The pollers were also hardened so a missed `visibilitychange` can't stop them: they keep a timer and skip fetches while hidden.

Also hardened: the shell's mutexes recover from poisoning, and harness output never uses `println!`/`eprintln!`. Previously a closed stdout (for example a harness dying) panicked a thread while it held the snapshot lock. The next UI poll then aborted the app from inside a WebKit callback. That was reproduced once, and it's now verified that the app survives a closed stdout.

## Functional validation

`python3 scripts/m4_validation.py` runs the release app in validation mode on a **copy** of the real database, so clear-history is never applied to real data. It drives the real UI and reads back what the pages rendered. The result was **50 passed, 0 failed** (`.validation/milestone4/validation.json`).

| Area | Result |
|---|---|
| Today / Yesterday / 7 / 30 days | Rendered rows, values, share % and summary equal the backend view, which equals an independent SQLite aggregation. Yesterday is compared exactly; ranges that include now are compared as monotonic. Shares sum to 100%, summary = Σ rows, and the period label and app count are correct. |
| Sorting | Download / Upload / Total are descending, the header shows "↓ ", and the sort persists natively. |
| Filtering | Case-insensitive substring match; the no-match state reads No apps match “…”. |
| GB/GiB | Rows and the status bar switch and switch back. |
| Appearance | Light → `#fdfdfc`, Dark → `#1c1d1f`, System follows the OS. |
| Icons | Real icons for all bundle rows; fallback for all non-bundle rows. |
| Tray | The Today total and ↓/↑, and the top 5 apps, equal the backend Today view. The only actions are Open BandPeek and Quit. |
| Close → collect → reopen | The collector kept sampling with the window closed. A 1 MiB public download made while closed appeared after reopening, and view state was restored. |
| Clear history | All earlier buckets and gaps were removed from both the store and the UI; tracking continues. |
| Quit | Exit code 0, `nettop` gone, settings persisted. |
| Layout | At 760 × 480, 840 × 600 and 1100 × 720 there are no clipped columns, and the table body scrolls. |

Also verified:

- Destroying the popup frees its WKWebView and WebContent process (`heap` shows 0 live `WKWebView` instances).
- Across repeated opens, popup dismissal on focus loss works.
- The app survives a closed stdout.
- In source: Cmd+Q maps to `applicationWillTerminate` → `RunEvent::Exit` → `Monitor::stop()`.

Screenshots come from WebKit's public snapshot API and AppKit view caching (validation-only stdin commands). This environment has no Screen Recording permission, so the captures show page content without native chrome.

## Tests and builds

- `cargo test` (core and desktop): **32 passed**. That's 23 from Milestone 3 plus 9 new ones: settings, formatting, bundle resolution, range names, tracking state, display names, periodic and immediate retention, and share %.
- `cargo clippy --all-targets -- -D warnings`, both `--no-default-features` and desktop: clean.
- `cargo fmt --check`: clean.
- `npm run build` (tsc + Vite): clean.
- The release native build with the embedded frontend, and `npm run desktop:build`: clean.

## Performance

`python3 scripts/m4_performance.py` uses the same method as Milestones 2–3:

- CPU is the cumulative `ps` CPU-time delta over monotonic wall time (one core = 100%), over a ~61 s window with 13 samples.
- RSS is summed across processes. Shared pages can be counted twice, so it isn't unique footprint.
- WebKit helpers are attributed to BandPeek by their Launch Services names ("bandpeek Web Content", …), not by timing.
- Ordinary apps stayed open during the runs.

| State | Native | nettop | GPU | WebContent | Networking | **Combined CPU** | **Mean RSS** |
|---|---:|---:|---:|---:|---:|---:|---:|
| Main window visible (page reported `visible` at start and end) | 0.408% | 0.228% | 0.294% | 0.326% | 0.000% | **1.256%** | **217.8 MiB** |
| Same instance, window closed (90 s teardown) | 0.327% | 0.196% | none | none | 0.000% | **0.523%** | **125.1 MiB** |
| Fresh tray-only instance (no WebView ever) | 0.277% | 0.229% | none | none | none | **0.506%** | **77.2 MiB** |

RSS by component: visible was native 109.7, nettop 3.2, GPU 31.5, WebContent 62.0 and Networking 11.3 MiB. With the window closed it was native 110.4, nettop 3.3 and Networking 11.4 MiB. Fresh tray-only was native 74.2 and nettop 3.0 MiB.

Comparison with earlier milestones:

- **Tray/background is unchanged or slightly better.** Milestone 3's tray-only figure was 0.609% CPU / 87.0 MiB, and Milestone 2's fresh tray was 0.577% / 78.3 MiB. The final UI adds a menu-bar title update once per collector frame, which costs nothing measurable.
- **The visible window costs less CPU than Milestone 2's debug table** (1.596% / 168.1 MiB there). RSS is higher: the real UI loads app icons and AppKit imaging into the native process, and WebContent/GPU are larger.
- Milestone 3's "visible 0.414% / 132 MiB" figure isn't comparable. Its helper attribution checked names that didn't match, so helpers were likely excluded. Its runs also overlapped the two orphaned `nettop` processes described above.
- **Closing the window removes the WebContent and GPU helpers.** CPU returns to the tray baseline. RSS does **not** return to the fresh-instance level: +48 MiB, made up of native WebKit/AppKit state, retained window objects (below) and the Networking helper (11 MiB), which WebKit keeps alive. It's still ~93 MiB less than the visible state.

## Remaining issues before public beta

1. **Closed windows are never deallocated (tao/wry).** Each destroyed window leaves its `TaoWindow`, `TaoView` and wry parent view alive; the WKWebView itself *is* freed. Measured cost: ~50 KB per popup open and ~0.7 MB per main-window open. Removing tao's `NSDistributedNotificationCenter` observer didn't help, so that change was reverted. This needs an upstream fix, or reusing a single hidden NSWindow while still destroying its WebView.
2. **RSS after closing the window stays about 48 MiB above a fresh tray-only launch.** CPU is back at baseline. See the item above; also consider relaunching the WebKit Networking helper lazily.
3. **Not validated by a human.** Real clicks on the status item, keyboard focus order, VoiceOver, and the popup on a second display or with a notch were only driven programmatically here. Cmd+Q was verified by code path, not exercised.
4. **Only the dark menu bar has been captured.** The template mark and label colour adapt automatically, but a light-menu-bar capture wasn't possible on this Mac.
5. **Shared WebKit helpers.** `com.apple.WebKit.Networking` often tops the table, because shared system helpers can't be attributed to the originating app (accuracy contract).
6. **Durability.** Observations are buffered for up to a minute before the SQLite flush, so an abrupt crash can lose the most recent unflushed traffic. Settings says so.
7. Not in scope and not started: launch at login, signing/notarization, installer, auto-update, Windows/Linux.

## Reproduce

```sh
source scripts/env.sh            # only with the project-local toolchain
npm run build
cargo build --release --manifest-path src-tauri/Cargo.toml --features custom-protocol
python3 scripts/m4_validation.py # uses a copy of the history database
python3 scripts/m4_performance.py
```

Validation stdin commands are only active with `--validation-mode`: `open`, `close`, `quit`, `popup`, `popup-close`, `snapshot <name>`, `appearance|units|retention <value>`, `resize <w> <h>`, `geometry <label>` and `eval <label> <js>`. Raw evidence stays in the ignored `.validation/milestone4/` directory, because it contains local app names.
