# BandPeek 0.1.0-beta.8 validation

Scope: host-named WebKit Networking rows (PR #12) and the 2 pt menu-bar
padding (PR #11). Everything else is unchanged from beta.7.

## Checks run (25 October 2026, macOS 27, Apple Silicon)

- On the combined release tree: `npm run build` (includes `tsc`),
  `cargo fmt --check`, `cargo clippy --all-targets -D warnings`, `cargo test`
  (78 tests), `scripts/test_release_policy.py` and the release-config check
  for `v0.1.0-beta.8`: clean.
- Release app (`tauri build --no-bundle`), isolated
  `BANDPEEK_DB_PATH`/`BANDPEEK_SETTINGS_PATH`:
  - `scripts/release_status_checks.py`: PASS in all three menu-bar modes
    (font, template image, geometry, widths, formatting, persisted relaunch).
    Speeds only pads the rates by 2 pt each side; Icon + speeds puts the mark
    at 2 pt and the text at 20 pt; Icon only stays 28 pt.
  - Passive live check over 20 s with Safari already running: live rates
    listed `helper:Safari Networking`, shown as "Safari Networking" with
    `host_app` "Safari" and the shared-process kind.
- PR #12's own end-to-end run (`scripts/webkit_host_names_validation.py`):
  Safari streamed 3 MB/s from a local page for 24 s. It appeared at
  2.85 MB/s on **Safari Networking** only (not on **WebKit Networking** or
  Safari's app row), and history recorded 72.04 MB against 72.00 MB served.
- No test instance or bundle was left behind.

## Not covered

- Host names in languages other than English: a localized instance name that
  isn't "<App> Networking" stays on the shared **WebKit Networking** row by
  design. Only English was exercised.
- The menu bar can't be screenshotted here (no Screen Recording permission),
  so the padding was checked through native geometry, not visually.
