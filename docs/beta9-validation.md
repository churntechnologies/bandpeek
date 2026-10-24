# BandPeek 0.1.0-beta.9 validation

Scope: left-aligned menu-bar rates (PR #14). Everything else is unchanged from
beta.8.

## Why beta.8 didn't fix the gap

In the owner's beta.8 menu bar BandPeek sat about 39 pt from the item on its
left, against about 22 pt between the other items. Logging the real collector
for 70 s showed the item sized for recent bursts (66 pt for "↓ 100.7 KB/s")
while showing rates that needed about 52 pt. The right-aligned text put that
spare room on the left. Rendering the status button after a wide rate showed
"↓ 1.2 KB/s / ↑ 134.0 B/s" starting 10 pt into the item instead of 3 pt.

## Checks run (25 October 2026, macOS 27, Apple Silicon)

- `npm run build` (includes `tsc`), `cargo fmt --check`,
  `cargo clippy --all-targets -D warnings`, `cargo test` (78 tests),
  `scripts/test_release_policy.py` and the release-config check for
  `v0.1.0-beta.9`: clean.
- Release app (`tauri build --no-bundle`), isolated
  `BANDPEEK_DB_PATH`/`BANDPEEK_SETTINGS_PATH`:
  - `scripts/release_status_checks.py`: PASS in all three menu-bar modes. It
    now also renders the status button after a wide and then a narrow rate
    and asserts that the text keeps its left edge, a check beta.8 fails.
  - Status button rendering at 2×, after "↓ 100.7 KB/s" then "↓ 1.2 KB/s /
    ↑ 134.0 B/s": the left edge of the text stayed at 6 px in Speeds only
    (beta.8: 20 px) and 5 px in Icon + speeds. The spare 19 px sits on the
    right.
- No test instance or bundle was left behind.

## Not covered

- The menu bar can't be screenshotted here (no Screen Recording permission).
  The button was checked through its own rendering, not in the live menu bar.
