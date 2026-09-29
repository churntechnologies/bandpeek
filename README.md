# BandPeek

**Pre-beta — macOS only.**

BandPeek is a free, MIT-licensed desktop bandwidth monitor answering: “Which applications are using my data, and how much?” It lives in the menu bar (BandPeek mark + live ↓/↑ rates), with a popup showing today's total and top apps, and a main window with Today / Yesterday / 7-day / 30-day history per application, stored locally in SQLite. Windows/Linux collectors, launch at login, installers and auto-update are not implemented yet.

## Run on macOS

Prerequisites: Node.js 22.12+ (tested with 24.19), Rust stable, Xcode Command Line Tools and `/usr/bin/nettop`.

```sh
npm ci
npm run desktop
```

If using the optional project-local Rust installation created during development, first run `source scripts/env.sh` from this repository root. Otherwise use your normal Rust installation. Rust and npm lockfiles are included; `.tools/` is ignored.

```sh
npm run build
cargo test --manifest-path src-tauri/Cargo.toml --no-default-features
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
npm run desktop:build
# No installer; run the release executable:
./src-tauri/target/release/bandpeek
```

Collector-only CLI (same core as the desktop):

```sh
cargo run --release --manifest-path src-tauri/Cargo.toml --no-default-features --bin bandpeek-probe -- 60
```

It prints local JSON snapshots for the requested duration. The optional second probe argument changes its sampling interval for experiments (for example `60 2`); the desktop app always samples every five seconds.

Closing the main window keeps BandPeek running in the menu bar (its WebView is destroyed; collection continues). Quit from the menu-bar popup or the app menu stops the collector and its single `nettop` subprocess.

## What the numbers mean

- History: observed TCP/UDP socket bytes per application, all interfaces (including LAN and loopback), aggregated into one-minute buckets in `~/Library/Application Support/BandPeek/bandpeek.db`. Sampling every **5 seconds**; buffered traffic is written about once a minute, so an abrupt crash can lose the most recent unflushed minute.
- Not ISP/billing usage and not wire-level bytes. Very short-lived processes and traffic during collector gaps can be missed. Shared system helpers (for example `com.apple.WebKit.Networking`) cannot always be attributed to the originating app. See the [accuracy contract](docs/macos-accuracy-contract.md).
- Settings: appearance (System/Light/Dark), units (GB decimal / GiB binary), history retention (30/90/180/365 days, default 90) and clear history.

## Privacy and permissions

Local-only processing. No analytics, telemetry, cloud, accounts, remote assets, update service, packet-content inspection or firewall/VPN feature. The app reads OS byte counters and local process/application metadata; it does not inspect destinations, URLs or payloads. Normal execution requires no root/sudo or additional macOS permission prompts on the tested Mac. Restricted execution sandboxes can deny the statistics socket; the app then reports a collector failure.

Only manually invoked validation scripts download a public 1 MiB test object, send synthetic loopback data, or request the local gateway. The opt-in Milestone 2 Wi-Fi test temporarily disables and restores Wi-Fi; the physical sleep test is user-assisted. Raw validation/measurement output stays in ignored `.validation/` and may contain process names/local paths. Do not publish those raw captures without review. The repository's report contains summarized measurements only. Tauri/WebKit may maintain normal runtime caches. Usage history and `settings.json` stay in `~/Library/Application Support/BandPeek/`.

## Development and evidence

- [Collector decision, accounting semantics and limitations](docs/collector-decision.md)
- [Milestone 1 measured validation report](docs/validation.md)
- [Milestone 2 accuracy, lifecycle and background measurements](docs/milestone-2.md)
- [Proposed macOS accuracy contract](docs/macos-accuracy-contract.md)
- [Milestone 2.5 cadence report](docs/milestone-2.5.md), [Milestone 3 persistence report](docs/milestone-3.md)
- [Milestone 4 final UI, lifecycle, validation and performance](docs/milestone-4.md)
- `scripts/m4_validation.py`: drives the final UI on a copy of the history database and checks it against backend and SQLite.
- `scripts/m4_performance.py`: visible / window-closed / fresh tray-only CPU and RSS, WebKit helpers attributed by Launch Services name.
- `scripts/validate.py`: independent nettop comparison with known local payloads and public HTTPS download.
- `scripts/recovery.py`: forced collector failure/restart, monotonic session totals and child cleanup.
- `scripts/measure.py`: release app + nettop + new WebKit helper CPU/RSS over ~60 seconds, after 20 seconds settling. Does not measure Vite or a debug build.

Build release binaries before running the scripts. Run them from a normal terminal that can use nettop. The scripts leave private evidence under `.validation/`. Some terminal supervisors close child apps when the measurement script exits; launch the development app normally for continued use.

## Layout

`src-tauri/src/collectors/` defines the platform collector boundary; `macos/` owns nettop, CSV parsing and identity resolution. `aggregate.rs` handles platform-independent counters, `db/` the SQLite history, `settings.rs` preferences, `model.rs` typed observations/snapshots. `src-tauri/src/shell/` is the desktop shell (commands, window/popup lifecycle, menu-bar item, AppKit helpers). `src/` is the React UI (`MainWindow`, `TrayPopup`, `Settings`). No fabricated Windows/Linux collectors exist.

The UI implements the final design handoff (`BandPeek-design-handoff/`: main window dark/light, tray popup, final 4a open-lens mark). `src-tauri/icons/icon.png` is the approved 128px app icon; `src-tauri/icons/tray-template.png` is the approved monochrome mark (`bandpeek-mark-mono-36.png`) used as a macOS template image.

License: [MIT](LICENSE).
