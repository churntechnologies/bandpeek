# Contributing to BandPeek

Thanks for your interest. BandPeek is a small MIT-licensed menu-bar app. The macOS build is in public beta. Windows and Linux are not implemented yet.

## Before you start

- **Accuracy comes first.** BandPeek shows observed per-process socket traffic, not ISP, billing or wire-level usage. Read the [accuracy contract](docs/macos-accuracy-contract.md) before changing the collector, identity or history code. Never attribute traffic to an app without evidence. For example, don't fold shared system helpers such as WebKit Networking into a browser.
- **Privacy is a feature.** No analytics, telemetry, network calls, remote assets, destination/URL/domain collection or packet inspection. Everything stays on the device.
- **Lightweight.** Tray/background CPU is expected to stay around half a percent of one core. Any change to collection, the shell or the UI loop should come with a before/after measurement (`scripts/m5_performance.py`).
- For larger changes (new platforms, collectors, UI), please open an issue first.

## Development setup (macOS)

Requirements: Node.js 22.12+, Rust stable, Xcode Command Line Tools.

```sh
npm ci
npm run desktop            # development app with hot reload
```

Checks that CI runs (please run them before opening a pull request):

```sh
npm run build
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
cargo clippy --manifest-path src-tauri/Cargo.toml --no-default-features --features probe --all-targets -- -D warnings
cargo test --manifest-path src-tauri/Cargo.toml --no-default-features
cargo test --manifest-path src-tauri/Cargo.toml
```

## Validation scripts

The scripts in `scripts/` drive a release build through validation-only stdin commands (active only with `--validation-mode`). Run them from a normal terminal: `nettop` needs the logged-in user's session and fails inside restricted sandboxes. They use a copy of your history database and a temporary settings file.

- `m5_interaction_checks.py`: close/reopen, quit paths, no orphaned `nettop`, accessibility audit
- `m5_window_cycles.py`: memory across repeated window and popup open/close cycles
- `m5_performance.py`: CPU/RSS in tray, visible, closed and cycled states
- `m4_validation.py`: history ranges, sorting, filtering, units and clear-history against SQLite

Raw output goes to `.validation/`, which is git-ignored. It can contain the names of apps on your Mac and local paths. **Don't attach raw validation output, databases or screenshots of your own usage to issues**; summarise them instead.

## Pull requests

- Keep changes focused. Match the surrounding code style.
- Add tests for identity, persistence or formatting changes.
- Describe how you verified the change (tests, scripts, manual checks from [docs/manual-checks.md](docs/manual-checks.md)).
- By contributing, you agree your contribution is licensed under the MIT License.
