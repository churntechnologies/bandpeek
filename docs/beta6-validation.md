# BandPeek 0.1.0-beta.6 validation

Scope: the **Updates** row in the Settings sheet and the shared update-request
path behind it. Everything else is unchanged from beta.5.

## Checks run (24 October 2026, macOS 27, Apple Silicon)

- `tsc --noEmit`, `cargo fmt --check`, `cargo clippy --all-targets`,
  `cargo test` and `scripts/test_release_policy.py`: clean.
- Release app bundle launched from its own path with isolated
  `BANDPEEK_DB_PATH`/`BANDPEEK_SETTINGS_PATH` and `--validation-mode visible`.
  The harness opened Settings and read the row: `idle` → click → `checking`
  (button disabled) → `up_to_date` ("You have the latest version", button
  "Check Again") after one real GitHub check; `updates.log` shows one check. The
  app-menu item read "Up to Date — Check Again" at the same time. A WebView
  snapshot of the sheet was inspected.
- Failure path: launched with the validation-only
  `--validation-update-endpoint https://127.0.0.1:1/latest.json` (nothing
  listening). The row ended on `failed` ("Try Again") and the menu item on
  "Update Failed — Try Again".
- Test bundles were deleted afterwards; no instance was left running.

## Not covered

- **Restart to Update** after a real verified download needs an update signed
  with the owner's private key; this validation never reads that key. beta.5's
  own signed install (beta.4 → beta.5) completed on the owner's Mac on
  24 October, which exercised the install and relaunch it triggers.
