# BandPeek 0.1.0-beta.7 validation

Scope: the menu-bar item's width and the visual refresh of the popup, main
window and Settings sheet. Collection, history and the updater are unchanged
from beta.6.

## Checks run (25 October 2026, macOS 27, Apple Silicon)

- `npm run build` (includes `tsc`), `cargo fmt --check`,
  `cargo clippy --all-targets` and `cargo test` (70 tests, including a new
  test for the widen-at-once, narrow-after-15-samples width rule and updated
  popup-position tests for the 340 pt popup): clean.
- Release app (`tauri build --no-bundle`) driven through the validation
  harness with isolated `BANDPEEK_DB_PATH`/`BANDPEEK_SETTINGS_PATH`:
  - Native status item, read with `status-rates`: Speeds only went from 72 pt
    for "↓ 39.8 KB/s / ↑ 90.2 KB/s" to 80 pt for "↓ 888.8 MB/s" and stayed at
    80 pt when the small values returned, as designed. The title rect width
    equalled `rates_width` at every step. Icon + speeds kept the 6 pt icon
    inset and the 4 pt icon-to-text gap.
  - `scripts/m5_screenshots.py` regenerated the README screenshots (main,
    Now and popup, light and dark) from demo data. Each was inspected.
  - WebView snapshots of the Settings sheet in light and dark were inspected.
    A clipped "1 year" retention choice found this way was fixed before
    release.
- No test instance or bundle was left behind.

## Not covered

- The menu bar can't be screenshotted here (no Screen Recording permission),
  so the width change was checked through the native geometry above, not
  visually.
- Real clicks on the menu-bar item and popup are a manual check
  ([manual checks](manual-checks.md), item 6).
