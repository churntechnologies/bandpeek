# BandPeek 0.1.0-beta.5 validation

Scope: the **Check for Updates…** app-menu item. Everything else is unchanged
from beta.4.

## Checks run (24 October 2026, macOS 27, Apple Silicon)

- `cargo test`, `cargo clippy --all-targets` and `cargo fmt --check`: clean.
- Release app bundle built from the branch and launched from its own path with
  isolated `BANDPEEK_DB_PATH`/`BANDPEEK_SETTINGS_PATH` and
  `--validation-mode visible`. The harness `menu` command listed the item under
  "About BandPeek" as "Check for Updates…" and performed it as a menu click.
  `updates.log` recorded one `checking GitHub Releases` followed by
  `no newer release`, and the title became "Up to Date — Check Again".
- A second click inside the 30 second cooldown performed the menu action but did
  not start another check (the log still shows one).
- Failure path: the same bundle launched with the validation-only
  `--validation-update-endpoint https://127.0.0.1:1/latest.json` (nothing
  listening). The check failed, the title became "Update Failed — Try Again" and
  the log recorded `check/download failed; retry at next scheduled check`.
- The test bundles were deleted afterwards; no instance was left running.

## Not covered

- **Restart to Update** after a real verified download, and the install it
  triggers, need an update signed with the owner's private key; this validation
  never reads that key. The click only calls the same `close_main` path as the
  window's close button, which the 29 September signed-update exercise (close-to-install) already covers.
