# Release engineering

BandPeek 0.1.0-beta.1 targets macOS 13+ on Apple Silicon. The permanent identifier is `io.github.churntechnologies.bandpeek`. The brand source is [Packet](../brand/README.md). No release has been authorized by this work.

## Update behavior

The official [Tauri 2 updater](https://v2.tauri.app/plugin/updater/) runs in the Rust shell. An installed app checks 30 seconds after startup and every six hours afterward. It does not need a WebView. GitHub's releases API discovers published semver tags, including beta prereleases (the `/releases/latest` redirect excludes prereleases). Tauri loads the release's `latest.json`, compares versions, downloads the archive and verifies its signature. HTTPS and signature verification are never disabled. A private GitHub repository returns an unavailable/404 response to unauthenticated clients; there are no repository tokens embedded in the app. Public distribution requires public release assets.

A verified download waits in memory while the main WebView exists, including minimized windows. Closing it wakes the installer; opening and installation reservation are serialized on the UI thread. Collection stops, its `nettop` child is reaped and SQLite flush must succeed before replacement. An independent same-volume backup protects the existing application from an installer rename failure. A failed install restores that bundle and resumes collection with its session totals and sequence preserved; the pause is recorded as a gap. Failed SQLite flushes retain pending observations for retry. Retry occurs at the next scheduled check. An installation that needs administrator privileges is deferred without requesting elevation. Install the app in a location owned by and writable to the current user.

The normal Tauri exit/restart path runs after successful replacement. A one-use local marker makes that launch menu-bar-only. History and settings stay at `~/Library/Application Support/BandPeek/`, independently of bundle identity. Launch at Login stays in ServiceManagement; the updater never unregisters it. Local updater diagnostics are bounded to about 1 MiB plus one rotated file, contain no observations and are never uploaded. They can contain local paths, so review before sharing.

**Current key gate:** The owner embedded the permanent updater public key in commit `129de7d` and configured `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` in GitHub. The required matching-key signed installation exercise completed on 29 September; concrete runtime results are recorded in [beta release validation](beta-release-validation.md#18-completed-matching-production-key-signed-updater-exercise--29-september). Remaining distribution gates include Developer ID/notarization, final hosted CI and owner publication approval. `createUpdaterArtifacts` is enabled by the workflow overlay only when the real private signing secret is available.

## Updater key creation reference (owner step completed)

The owner has completed key creation, public-key embedding and GitHub updater-secret configuration. **Do not rerun generation or replace the key.** These commands are retained as provisioning reference only; private-key handling belongs to the owner:

```sh
umask 077
mkdir -p "$HOME/.config/bandpeek-signing"
npm run tauri -- signer generate --write-keys "$HOME/.config/bandpeek-signing/updater.key" > /dev/null
chmod 600 "$HOME/.config/bandpeek-signing/updater.key"
python3 scripts/embed_updater_public_key.py "$HOME/.config/bandpeek-signing/updater.key.pub"
gh secret set TAURI_SIGNING_PRIVATE_KEY --repo churntechnologies/bandpeek < "$HOME/.config/bandpeek-signing/updater.key"
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --repo churntechnologies/bandpeek
```

The generator prompts for a password; store that password in your password manager and enter it into the final `gh secret set` prompt. Standard output is suppressed because the CLI can print private material. Do not run with shell tracing (`set -x`) or verbose logging. The embedding script reads only the `.pub` file. Do not use `--force` on an existing key. Keep an encrypted offline backup of the private file and its password. **Losing the private updater key prevents existing installations from trusting future updates.** A different new key does not fix those existing clients; they would need a separately trusted manual migration.

Only the public value in `tauri.conf.json` belongs in Git. Never commit private keys, passwords, certificates or local secret files. The workflow does not print them, upload them or include them in the manifest.

## GitHub Actions secrets

| Secret | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | Contents of the owner's Tauri private updater key |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Its password |
| `APPLE_CERTIFICATE` | Base64 of exported Developer ID Application `.p12` (certificate **and private key**) |
| `APPLE_CERTIFICATE_PASSWORD` | Export password for that `.p12` |
| `APPLE_SIGNING_IDENTITY` | Exact `Developer ID Application: … (…)` identity |
| `APPLE_ID` | Apple account used for notarization |
| `APPLE_PASSWORD` | Apple **app-specific** password, not normal account password |
| `APPLE_TEAM_ID` | Apple Developer team ID |

For Apple setup, follow [Tauri's macOS signing and notarization instructions](https://v2.tauri.app/distribute/sign/macos/). Do not paste credentials into issues or this document. All six Apple secrets must be configured together. Partial configuration fails the workflow. Complete credentials cause Developer ID signing, hardened runtime, notarization and stapling. The workflow validates the signature, identifier, runtime, stapled app and DMG, and Gatekeeper assessment. Without those credentials it uploads an explicitly labelled **LOCAL-VALIDATION-ONLY** ad-hoc installer as a private Actions artifact; it cannot publish a normal public binary.

## Workflow

1. Change code and bump `package.json`, its lockfile, Cargo package/lock version, and Tauri version together. `scripts/release_config.py` checks all five and the exact `v<version>` tag.
2. Use `workflow_dispatch` on a candidate branch for private validation when the workflow is registered on the default branch. A version tag is used for an approved release candidate; this validation does not push a tag. On 29 September, GitHub registered only CI: `release.yml` exists on the PR branch but is absent from the default branch, so the candidate dispatch route is currently unavailable without a default-branch workflow change. Do not merge the release PR to unblock this exercise.
3. Actions runs the frontend build, rustfmt, Clippy with warnings denied, core and desktop tests, and the Apple Silicon release build. The platform matrix contains only the validated architecture.
4. A trusted tagged build produces `.app.tar.gz`, `.sig`, DMG, `latest.json` with `darwin-aarch64`, and `SHA256SUMS`. The official Tauri bundler signs updater artifacts; the release-only verifier rejects mismatched keys or tampered artifacts before upload. The updater archive contains the signed/notarized/stapled application. Prerelease tags produce GitHub prereleases.
5. **Publication needs explicit owner approval:** configure required reviewers on the `public-release` GitHub environment and set repository variable `PUBLIC_RELEASE_APPROVED_TAG` to the exact tag after checking every gate below. Unset it after use. Only that trusted tag can enter the publishing job. It creates the GitHub Release and uploads the manifest/artifacts. Candidate builds alone cannot publish.

Do not enable publication until the updater key is embedded, a matching-key signed update has completed end to end, CI is green for the release commit, author privacy is resolved, human checks are completed or explicitly accepted, Developer ID/notarization validation passes, and the owner approves publication. Existing `0.1.0-beta.1` clients cannot update to another artifact with the same version; use a newer version in a disposable validation bundle for the end-to-end update exercise. Keep test keys/configuration out of public builds.

## Required signed update exercise (completed 29 September)

Use a disposable, user-writable copy of the installed bundle and isolated history/settings. With genuine signed metadata/artifacts available, verify: no-update, newer valid artifact, corrupt signature, corrupt archive, main-window-open deferral (including minimized), close-to-install, failure rollback/resumed collector, and tray-only relaunch. Check SQLite integrity and recorded pending observations, settings and ServiceManagement state across the actual replacement; test enabled and disabled login registration, then leave it disabled. Verify the old `nettop` PID is gone, the new child belongs only to the relaunched process, and there is never more than one collector. Unit tests and the validation-only restart seam do not replace this signed install exercise.

### Fixture validation before the repository becomes public

Normal releases always use GitHub Releases. For the owner-authorized signed exercise only, an installed validation bundle accepts `--validation-mode auto --validation-update-endpoint https://<fixture-host>/latest.json`. The fixture must have a valid HTTPS certificate and genuinely signed updater bytes matching the embedded public key; no TLS or signature bypass exists. Use isolated `BANDPEEK_DB_PATH` and `BANDPEEK_SETTINGS_PATH`. Send `update-check` on stdin for a check now and `update-state` for the lifecycle phase. An open main window must remain alive in `Ready`; close it to exercise actual verified installation/relaunch. Normal launches ignore the fixture flag. A temporary test host is unnecessary for production V1.

The key-configured candidate workflow can generate signed updater archives and manifests even before Apple credentials are available, but labels such artifacts LOCAL-VALIDATION-ONLY and cannot publish them. This allows ad-hoc signed replacement testing without representing the test bundle as a trusted public binary.

### 29 September matching-key exercise preparation

PR #1 CI run `36542010010` passed at commit `129de7d1f05708cc5639a53bd75e4360be0f58af`; it produced no artifacts. GitHub lists only the CI workflow. The repository remains private, and no candidate workflow, release tag or publication was invoked.

A fresh Apple Silicon `0.1.0-beta.1` application was built with the permanent embedded public key and ad-hoc macOS signing. Frontend build and strict bundle signature verification passed. `python3 scripts/prepare_signed_update_exercise.py` packages that app under `.validation/signed-updater/` and prepares a separate valid-gzip/invalid-tar fixture for a genuinely signed installer failure. It validates bundle identity/version, archive layout and archived executable bytes. It never reads a private key, signs an artifact or launches BandPeek. Do not rerun preparation over owner-signed fixtures. These artifacts are LOCAL-VALIDATION-ONLY and must never be published.

**Owner signing completed and verified.** Both commands below succeeded with owner-only password entry, and `bandpeek-verify-update` verifies both signatures against the embedded public key. They are retained as a record; do not sign again. No private signing key or password was accessed by this validation.

```sh
# From the repository root:
env -u TAURI_SIGNING_PRIVATE_KEY -u TAURI_SIGNING_PRIVATE_KEY_PASSWORD -u TAURI_SIGNING_PRIVATE_KEY_PATH npm run tauri -- signer sign --private-key-path "$HOME/.config/bandpeek-signing/updater.key" --app-version 0.1.0-beta.1 .validation/signed-updater/BandPeek.app.tar.gz > /dev/null
env -u TAURI_SIGNING_PRIVATE_KEY -u TAURI_SIGNING_PRIVATE_KEY_PASSWORD -u TAURI_SIGNING_PRIVATE_KEY_PATH npm run tauri -- signer sign --private-key-path "$HOME/.config/bandpeek-signing/updater.key" --app-version 0.1.0-beta.1 .validation/signed-updater/install-failure.app.tar.gz > /dev/null
```

The second archive is intentionally invalid and is only for testing installer failure/backup restoration after genuine download signature verification. A separate copy corrupted **after** signing tests download rejection. Neither signature nor TLS verification may be bypassed.

Both `.sig` files have now been verified against `src-tauri/tauri.conf.json` using `bandpeek-verify-update`. An isolated older-version disposable baseline (`0.1.0-beta.0`) was built under `.validation/signed-updater/baseline/` for the exercise and later removed during cleanup without changing tracked release versions. Production-version build outputs were restored from the signed target archive. The completed runtime exercise served those fixtures through normally trusted loopback-only HTTPS and passed every lifecycle, SQLite, collector and login-state check above. Four genuine signed installations completed; a signed malformed archive exercised official installer failure and backup restoration. The new harnesses are `scripts/signed_update_fixture.py` and `scripts/signed_update_exercise.py`; they never read the private updater key.

### Local HTTPS trust setup (owner step completed)

The owner requires the fixture server to bind only `127.0.0.1`, using a certificate with SANs for `localhost` and `127.0.0.1`. No fixture upload or public tunnel is authorized. The owner installed an exercise-specific mkcert CA and generated the localhost certificate. Ordinary macOS `curl` and the actual updater verified its TLS normally. BandPeek TLS configuration remained unchanged.

The following owner setup commands are retained as a completed-step reference; do not reinstall the CA. They created a CA specifically for this exercise in a git-ignored directory, independently of the permanent updater key.

```sh
brew install mkcert
umask 077
mkdir -p .validation/signed-updater/tls/ca
CAROOT="$PWD/.validation/signed-updater/tls/ca" TRUST_STORES=system mkcert -install
CAROOT="$PWD/.validation/signed-updater/tls/ca" TRUST_STORES=system mkcert -cert-file .validation/signed-updater/tls/localhost.pem -key-file .validation/signed-updater/tls/localhost-key.pem localhost 127.0.0.1
```

The completed exercise verified macOS trust with an ordinary HTTPS request and the actual updater, with no insecure flags or additional app-specific roots. The fixture server is stopped; temporary leaf keys/certificates, updater fixtures, copied bundles and isolated DB/settings are removed. Logs/results and the exercise CA files remain git-ignored. CA trust removal is the owner step below; no pre-existing CA is removed.

### Owner cleanup of the exercise CA

The exercise-specific CA remains in the system trust store until the owner removes it. Do not perform interactive trust removal through the agent. From the repository root, run this exact command yourself and enter any system password only locally:

```sh
CAROOT="$PWD/.validation/signed-updater/tls/ca" TRUST_STORES=system mkcert -uninstall
```

This targets the CA created specifically for this exercise. Keep its `rootCA.pem` available until uninstall completes. It does not target any existing mkcert CA or the permanent updater key. After successful uninstall, the owner may delete `.validation/signed-updater/tls/ca/`.

The configured-updater performance repeat also completed in all four release modes. The performance harness now defaults to an installed bundle with fresh isolated history; it accepts an explicit synthetic source fixture and a normally trusted validation HTTPS endpoint. Measured combined CPU was 0.457% / 0.554% / 0.588% in tray modes and 1.413% visible, with exactly one collector in every sample. See [beta release validation](beta-release-validation.md#12-performance) for the full measurements and their limits.
