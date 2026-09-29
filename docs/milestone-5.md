> Historical Milestone 5 snapshot. Current release identity, updater, signing gates and validation are in [release engineering](release-engineering.md) and [final beta validation](beta-release-validation.md).

# Milestone 5: macOS public-beta hardening

Measured on Sep 29, 2026 (local time, UTC+8) on macOS 27.0 (26A428), Apple Silicon, as the ordinary logged-in user, on the same Mac and toolchain as Milestones 1–4 (Rust 1.98.1, Tauri 2.12.0, tao 0.37.1, wry 0.57.0, Node 24.19).

The collector, persistence architecture, UI design and brand are unchanged. This milestone fixes the closed-window leak, improves display names without touching stored identity, and adds Launch at Login. It also prepares version 0.1.0-beta.1, the `.app`/`.dmg`, the README, contributor docs and CI. Nothing has been pushed or published.

## 0. Checkpoint

Before any change, a privacy audit covered `.gitignore` and every file to be committed. It found no databases, validation captures, endpoint data, personal paths or e-mail addresses. Two wording-only redactions were made so they never enter history: the home time-zone name became "UTC+8", and two named VPN clients became "two third-party VPN clients". `.gitignore` now also covers databases (`*.db*`, `*.sqlite*`), `settings.json`, packaged artifacts (`*.dmg`, `*.app/`), signing material (`*.p12`, `*.p8`, `*.cer`, provisioning profiles), `.env*` and editor/agent folders.

Initial local commit: **`dfeb401` feat: build BandPeek macOS beta** (Milestones 1–4, 69 files).

## 1. Closed-window resource leak

### Root cause

tao 0.37.1 (the latest release, 26 Sep 2026) creates each macOS window with `alloc` + `initWithContentRect:…`. That returns an owned (+1) pointer, which it then wraps with `Retained::retain` (another +1) instead of taking ownership (`src/platform_impl/macos/window.rs`, around lines 246–255). One reference is never released, so **every NSWindow BandPeek destroyed stayed allocated**, together with its content view (`TaoView`) and wry's `WryWebViewParent`.

Evidence:
- A diagnostic build (not committed) tracked zeroing weak references. On one open/close: the window's retain count went 22 → 4 → **1**, then stayed at 1 indefinitely. The window delegate was freed about a second after close, and the WKWebView was freed.
- `heap` confirms that the objects accumulate, one set per open (table below).
- `leaks` doesn't flag them because they are reachable by conservative scanning, not orphaned.
- Milestone 4's attempt to remove the distributed-notification observer couldn't help, since the observer isn't the owner.

### Fix (public API only)

Each surface (main window, popup) now keeps **one native window for the process lifetime**:
- Closing hides the window and **destroys its WebView** (`Webview::close`).
- Opening attaches a fresh WebView (`Window::add_child`, Tauri's multi-WebView API behind its `unstable` cargo feature).

No retain-count manipulation, private API or patched dependency is used. WebKit still has no WebView to run while BandPeek is closed, so the Milestone 2 reason for destroying the WebView still holds.

Side effects checked:
- The page viewport is still exactly 840 × 600 pt below the 32 pt title bar, identical on every reopen (verified 100×).
- The window now reopens where the user left it.
- The close button, Cmd+W and the harness all go through `CloseRequested` → hide + destroy WebView → `Accessory` policy.
- The popup reuses its borderless window and is re-positioned under the menu-bar item on every open.

Rejected alternatives: sending the orphaned NSWindow a balancing `release` (depends on tao's exact bug and on `retainCount`, and would over-release if tao is fixed), and vendoring a patched tao (not an application-level fix, and adds fork maintenance). The tao bug should be reported upstream. When it's fixed, BandPeek could return to destroying windows, but it doesn't need to.

### Measurements

`scripts/m5_window_cycles.py` runs one instance, starting menu-bar-only, on a copy of the history database. Each cycle is `open` → page rendered → `close`. At each checkpoint it settles for 15 s, then records RSS, physical footprint (`vmmap`) and live Objective-C objects (`heap`).

| After | M4 build: tao windows / RSS / footprint | M5 build: tao windows / RSS / footprint |
|---|---|---|
| fresh, no WebView yet | 0 / 76.9 / 15.4 MiB | 0 / 77.2 / 15.5 MiB |
| 10 main-window opens | 10 / 114.1 / 38.4 MiB | **1** / 111.4 / 33.0 MiB |
| 50 main-window opens | 50 / 135.0 / 66.7 MiB | **1** / 94.6 / 35.2 MiB |
| 100 main-window opens | 101 / 173.2 / 104.9 MiB | **1** / 104.7 / 35.6 MiB |
| + 100 popup opens | 203 / 182.0 / 111.4 MiB | **2** / 107.5 / 31.9 MiB |

`WryWebViewParent` instances follow the tao window counts before the fix (10 → 203) and are **0** after it.

- **Before:** ≈ 0.74 MiB footprint per main-window open and ≈ 65 KiB per popup open, growing without bound (consistent with Milestone 4's 0.7 MB estimate).
- **After:** flat. From 10 to 100 opens the footprint moved 30.3 → 31.8 MiB in one run and 33.0 → 35.6 MiB in another, about 17–29 KiB per open including allocator and cache noise. Popups add nothing measurable. RSS varies more than footprint because it counts shared framework pages that macOS pages in and out.
- A third run under an all-core CPU load (20 main + 150 popup opens): 2 windows, 32.4 MiB footprint, exit 0, no failures.

What remains after the first open is a one-time cost, not a leak:
- About 17 MiB of footprint (≈ 34 MiB RSS) of WebKit/JavaScriptCore state that the process keeps once any WebView has existed.
- WebKit's *Networking* helper (≈ 11 MiB), which WebKit keeps alive; there is no public API to end it.

The *Graphics and Media* helper exits 30–60 s after close in both builds (timed side by side). wry still leaks four small URL-scheme-handler objects per WebView (`heap`: +4 per open, well under 1 KiB each time); that is upstream (wry 0.57 `url_scheme_handler`) and immaterial.

**Unexplained event:** in the first full M5 run, the app exited during the popup phase after 100 main-window opens. It produced no crash report. That harness version didn't record the exit status or stderr, and a stray baseline instance and a concurrent build were running at the time. The harness now records both. Three later runs, including the stress run, reproduced nothing: 120 main + 400 popup opens in total, all exit 0. It's reported here rather than claimed fixed.

## 2. Application names

A new platform-independent module, `presentation.rs`, derives a label and a kind for each history row at read time. **Nothing persisted changes**: `identity_key`, the stored `application_name` and all buckets are untouched, so historical identity cannot split or merge. `get_history` rows gain `display_name` and `kind`; the UI shows `display_name`, and filtering matches either name.

| Stored identity | Before | Now | Kind (tooltip) |
|---|---|---|---|
| `bundle:com.anthropic.claude-code` (bundle `claude.app`, `CFBundleName` = "Claude Code") | claude | **Claude Code** | Command-line tool |
| `bundle:com.anthropic.claudefordesktop` | Claude | Claude (unchanged, separate identity) | Application |
| `exec:…/com.apple.WebKit.Networking` | com.apple.WebKit.Networking | **WebKit Networking** | Shared system process: "Handles network traffic for other apps; BandPeek cannot tell which app it was for." |
| `exec:…/com.apple.Safari.SearchHelper`, `…/com.apple.geod` | reverse-DNS names | Safari SearchHelper, geod | macOS system process |
| `exec:/usr/sbin/mDNSResponder`, `/usr/libexec/nsurlsessiond` | unchanged | unchanged | Shared system process |
| `exec:/usr/bin/curl`, Homebrew tools | unchanged | unchanged | Command-line tool |

Rules:
- Shared-helper labels apply only to executables in system locations (`/System`, `/usr/libexec`, `/usr/sbin`, `/sbin`, `/Library/Apple`, including the Cryptex paths). A user binary that merely has the same name isn't treated as one.
- Helpers are never folded into Safari, Claude, ChatGPT or any other app. Non-application rows use the neutral terminal tile instead of a generic app icon.
- For new observations, the resolver uses a bundle's declared `CFBundleDisplayName`/`CFBundleName` only when the bundle folder is named exactly like its executable (a packaged tool such as `claude.app`) and the declared name is a real name, not a reverse-DNS identifier. Ordinary apps keep their Finder name.
- On relaunch, the next flush updates the stored name under the **same** identity key. A test proves one `apps` row and complete history across the change.

Tests: 7 presentation tests (Claude Code vs Claude Desktop, WebKit Networking in both system paths including the truncated `com.apple.WebKi` name, a fake user-owned helper, system and CLI processes, ordinary apps, declared-name rules), 1 resolver test with synthetic bundles, and 1 SQLite test (label change keeps one identity and all bytes).

## 3. Launch at Login

- **Mechanism:** `SMAppService.mainAppService` (ServiceManagement, macOS 13+), called through `objc2` with the framework linked. There is no launch-agent plist, helper, daemon, shell script or administrator right. `LSMinimumSystemVersion` is 13.0.
- **Source of truth:** macOS stores the registration, and the user can change it in System Settings → General → Login Items. Settings therefore reads the state from the system whenever the sheet opens or regains focus. BandPeek keeps no copy of its own.
- **States shown:**
  - On
  - Off
  - "Waiting for approval in System Settings → General → Login Items", with an **Open Login Items…** link
  - "Available when BandPeek runs as an installed app" (development binary, controls disabled)
- **Login launch:** macOS opens a login item with the `kAEOpenApplication` Apple event carrying `keyAEPropData = keyAELaunchedAsLogInItem` ('lgit'). Tauri's setup hook runs while that event is current. When it's present, BandPeek sets the `Accessory` policy and opens no window; otherwise the main window opens as before. A Dock reopen (`rapp`) is never treated as a login launch.

Verified on the bundled app, with an isolated database:

| Check | Result |
|---|---|
| Enable from the app (the same command Settings uses) | `enabled`; ServiceManagement record `enabled, allowed` |
| State after quitting and relaunching | `enabled` |
| Disable | `disabled`; still `disabled` after another relaunch |
| Normal manual launch (`open`) | launch event `oapp`, no property → main window, `Foreground` |
| Login launch (reproduced with the real `oapp`+`lgit` event through `NSWorkspace`, `scripts/m5_login_launch.js`) | `login-launch=true`, no window, `UIElement` (menu bar only), collector running |
| Quit after a login launch (Apple Event `quit`) | clean exit, `nettop` gone |
| Development binary | `Unavailable`; controls disabled with explanation |

Tests: the login-event decision is unit-tested against real `NSAppleEventDescriptor`s: `oapp`+`lgit`, plain `oapp`, another property, and `rapp` + `lgit`. A second test checks that the development binary reports unavailable. **Not verified:** a real logout/login cycle (listed in the manual checks). The registration left on this Mac at the end of testing is **disabled**.

## 4. Accuracy wording

The main UI is unchanged. The Settings note now says:
- BandPeek shows a best-effort estimate of per-app TCP/UDP socket traffic on all interfaces.
- It is not an ISP, billing or wire-level meter, and totals won't match the provider's.
- Local/LAN traffic is included.
- Very short-lived processes can be missed.
- Shared helpers are listed on their own.
- An abrupt crash can lose up to a minute of recent activity.

The README carries the same caveats in its "What the numbers mean" section.

## 5. Interaction checks

`scripts/m5_interaction_checks.py`: **21 passed, 0 failed**. The checks cover:
- The app-menu Quit item (`terminate:`, dispatched from the run loop): exit 0, `nettop` gone.
- The popup's Quit button: exit 0, `nettop` gone.
- SIGTERM and SIGKILL: no orphaned `nettop`.
- `performClose:` (the close button and Cmd+W path): the window hides and the app keeps running. Reopening works.
- The app menu lists Quit BandPeek ⌘Q, Hide BandPeek ⌘H and Close Window ⌘W.
- Accessibility audit of the main window, Settings and popup: every control has an accessible name, decorative images are hidden, segmented controls are labelled groups with pressed state, and Settings is a labelled modal dialog.
- Escape closes Settings and the popup.
- The Settings copy.

Popup placement math has 4 unit tests: centred, right edge by a notch, left edge, and screens left or right of the main display.

A harness pitfall is worth recording: validation commands run inside tao's event-loop callback, and `terminate:` issued from there deadlocks inside tao. Real menu clicks, shortcuts and Apple Events are dispatched from the run loop instead. An Apple Event `quit` exited cleanly, so the harness now defers menu actions through GCD to reproduce the real dispatch.

**Not performed by a person** (see [manual-checks.md](manual-checks.md)): real clicks on the menu-bar item, light versus dark menu bar, a notched display, multiple displays, a real Cmd+Q key press, keyboard Tab navigation with macOS keyboard navigation on, VoiceOver speech, and a real logout/login.

## 6–7. Version and packaging

- **Version 0.1.0-beta.1** in `package.json`/`package-lock.json`, `Cargo.toml`/`Cargo.lock` and `tauri.conf.json`. The bundle's `CFBundleShortVersionString` and `CFBundleVersion` are both 0.1.0-beta.1.
- `npm run desktop:bundle` builds, for aarch64 only:
  - `src-tauri/target/release/bundle/macos/BandPeek.app` (≈ 15 MiB)
  - `src-tauri/target/release/bundle/dmg/BandPeek_0.1.0-beta.1_aarch64.dmg` (≈ 5 MiB, with an Applications link)
- App icon: an `.icns` built from the approved handoff icon at 16–1024 px. The 128 px PNG is byte-identical to the one approved in Milestone 1.
- The development-only `bandpeek-probe` CLI is now behind a `probe` cargo feature, so it's no longer copied into the app bundle.

**Signing and notarization.** This Mac has **no code-signing identity** (`security find-identity -p codesigning`: 0 valid) and no provisioning profiles. `notarytool` and `stapler` are installed. The bundle is **ad-hoc signed**: sealed resources, Info.plist bound, hardened runtime, `codesign --verify --deep --strict` passes. Gatekeeper assessment (`spctl -a`) is **rejected**, as expected without a Developer ID and notarization. A downloaded copy will be blocked on first launch. The README tells users to build from source, or to use Apple's documented Privacy & Security → Open Anyway for a copy they obtained from the project and checksum-verified. It never suggests disabling Gatekeeper or stripping quarantine. Developer ID signing and notarization need an Apple Developer account, which is the owner's decision.

## 8. Repository preparation

- **README:** rewritten for a new visitor. It covers what BandPeek does, a status table (macOS beta; Windows and Linux not implemented), privacy, what the numbers mean, install/build, Open at login, development, docs and the license. Screenshots in `docs/screenshots/` are generated by `scripts/m5_screenshots.py` from a **synthetic demo database** of built-in Apple apps, a CLI tool and shared helpers, never real usage.
- **CONTRIBUTING.md:** principles (accuracy, privacy, lightweight), setup, the CI commands, validation scripts, and a warning never to attach raw validation output or personal databases to issues.
- **CI:** `.github/workflows/ci.yml` on `macos-latest` runs `npm ci`, `npm run build` (tsc + Vite), `cargo fmt --check`, Clippy with `-D warnings` for core and desktop, and Rust tests for core and desktop. It has read-only permissions and no secrets. It hasn't run on GitHub yet; the same commands pass locally.
- **Tracked-file audit:** the tracked files contain no databases, `.validation` output, endpoint data, build output or personal paths. Test fixtures use `/Users/someone/…`.

## 9. Performance

`scripts/m5_performance.py` uses the Milestone 2–4 method: cumulative `ps` CPU time over monotonic wall time (one core = 100%), and RSS sampled every 5 s over about 60 s and summed across processes (shared pages can be counted twice). WebKit helpers are attributed by their Launch Services names. It measures **one instance through four states**, on a copy of the history database, and records the native process's physical footprint, the `nettop` child and the WebKit helpers at each phase.

The M4 baseline is the checkpoint commit built from a scratch worktree, plus one validation-only command. Both builds ran back to back on the same Mac with ordinary apps open, which is not a lab-idle system.

A measurement pitfall found here: a harness-launched app cannot take activation, so the "visible" window can sit behind the frontmost app's windows, where WebKit reports the page as `hidden` and throttles it. The first attempt at this comparison measured a hidden page (about 0.5% CPU) and was discarded. The validation-only `front` command (`orderFrontRegardless`, no activation) now keeps the window on screen, and the script requires the page to report `visible` before and after the measurement.

| State | M4 baseline: CPU / RSS / native footprint | **M5: CPU / RSS / native footprint** |
|---|---|---|
| Fresh, menu bar only (no WebView yet) | 0.522% / 73.8 MiB / 15.9 MiB | **0.522% / 75.3 MiB / 15.7 MiB** |
| Main window visible (page `visible` → `visible`) | 1.257% / 189.0 MiB / 31.2 MiB | **1.305% / 186.5 MiB / 31.9 MiB** |
| After closing the window (90 s settle) | 0.539% / 100.0 MiB / 28.8 MiB | **0.522% / 94.6 MiB / 30.3 MiB** |
| After 20 more open/close cycles (90 s settle) | 0.407% / 105.8 MiB / **45.0 MiB** | **0.326% / 109.8 MiB / 32.8 MiB** |

Components, M5:
- **Menu bar only:** native 0.245%, `nettop` 0.277%.
- **Visible:** native 0.392% / 99.7 MiB, Web Content 0.375% / 49.3 MiB, Graphics and Media 0.310% / 26.7 MiB, Networking 0.000% / 8.2 MiB, `nettop` 0.228%.
- **Closed:** only the native process, `nettop` and the Networking helper remain. The Web Content and GPU helpers are gone.

In every state, exactly one `nettop` child exists and it belongs to the app.

- **Menu bar only and closed-window CPU are unchanged** at about 0.3–0.5% of one core, the same as Milestones 2–4 (M4 published 0.506% fresh and 0.523% closed).
- **Visible-window CPU: 1.305% versus 1.257%.** That is within run-to-run variation (M4 published 1.256%), and the component split is the same, so there's no material regression.
- **Repeated windows:** the M4 build's footprint grew by 16.2 MiB over 20 cycles; the M5 build's grew by 2.5 MiB, almost all of it within the first cycles. RSS is noisier than footprint because it counts shared framework pages.
- Launch at Login and naming add no background work. The login-item state is read only when Settings opens, and names are derived per history query.

Validation-only harness additions made during this milestone (all inert without `--validation-mode`):
- `auto` mode: production startup decision with harness output on
- `perform-close`, `menu [title]`, `front` and `webview-geometry` commands
- `BANDPEEK_VALIDATION_OUT`: harness output to a file, for launches without stdout
- `--validation-no-record`: keeps the collector from writing history, for demo-database screenshots

## 10. Regression

| Check | Result |
|---|---|
| Rust tests (core) | 41 passed |
| Rust tests (desktop shell) | 6 passed |
| Clippy `-D warnings` (core with `probe`, desktop, all targets) | clean |
| rustfmt `--check` | clean |
| TypeScript + Vite build | clean |
| Release build, `.app` + `.dmg` bundle | clean |
| `scripts/m4_validation.py` (history ranges vs SQLite, sort, filter, units, appearance, tray, close → collect → reopen, clear history, quit, settings persistence) | **50 passed, 0 failed** on the final binary. One intermediate run failed the clear-history check. Traffic observed seconds after the clear was compared against a page total rendered before it arrived; the check now waits for the page's next 15 s refresh, and the store-side conditions were unchanged and passing. |
| `scripts/m5_window_cycles.py` (final binary, 50 main + 50 popup opens) | 1 window per surface, 0 retained WebView parents, footprint 33.0 → 35.1 MiB, exit 0 |
| `scripts/recovery.py` (probe: `nettop` SIGKILL → gap, generation 2, monotonic totals, no orphans) | pass |
| Desktop collector recovery (the app's `nettop` killed) | generation 1 → 2, gap exposed, tracking resumed, monotonic totals, new child owned by the app; after quit no `nettop` |
| `scripts/m5_interaction_checks.py` | 21 passed, 0 failed |
| No orphaned `nettop` after normal quit (menu, popup, Apple Event) and forced termination (SIGTERM, SIGKILL) | pass |

## Remaining before publishing

1. **Developer ID signing and notarization.** There is no identity on this Mac. Without them, every downloaded copy is blocked by Gatekeeper on first launch.
2. **Final bundle identifier.** It's still `org.bandpeek.dev`. Choose the permanent identifier before the first public build: it keys the login-item registration and WebKit data, and changing it later means users must re-enable Open at login.
3. **Human checks** in [manual-checks.md](manual-checks.md): real menu-bar clicks, light and dark menu bars, a notched display, multiple displays, a real Cmd+Q, Tab navigation, VoiceOver, and a real logout/login with Open at login.
4. **CI** has only run locally. The first GitHub run will confirm the `macos-latest` image.
5. **Commit author e-mail.** The local commits use a personal address. Decide whether to publish it, or rewrite it to a GitHub no-reply address before the first push.
6. **Upstream reports:**
   - tao: the unbalanced retain in the window constructor
   - wry: per-WebView URL-scheme-handler leak
7. **Unexplained exit.** The single early exit in the first M5 cycle run was not reproduced in three later runs; keep an eye on it during the beta.
8. **Brief Dock icon at login launch.** The Dock icon may flash briefly before the menu-bar-only policy applies. This has not been observed by a person yet.
