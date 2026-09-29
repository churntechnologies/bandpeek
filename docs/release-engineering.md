# Release engineering

BandPeek 0.1.0-beta.1 targets macOS 13+ on Apple Silicon. The permanent identifier is `io.github.churntechnologies.bandpeek`. The brand source is [Packet](../brand/README.md). No release has been authorized by this work.

## Update behavior

The official [Tauri 2 updater](https://v2.tauri.app/plugin/updater/) runs in the Rust shell. An installed app checks 30 seconds after startup and every six hours afterward. It does not need a WebView. GitHub's releases API discovers published semver tags, including beta prereleases (the `/releases/latest` redirect excludes prereleases). Tauri loads the release's `latest.json`, compares versions, downloads the archive and verifies its signature. HTTPS and signature verification are never disabled. A private GitHub repository returns an unavailable/404 response to unauthenticated clients; there are no repository tokens embedded in the app. Public distribution requires public release assets.

A verified download waits in memory while the main WebView exists, including minimized windows. Closing it wakes the installer; opening and installation reservation are serialized on the UI thread. Collection stops, its `nettop` child is reaped and SQLite flush must succeed before replacement. An independent same-volume backup protects the existing application from an installer rename failure. A failed install restores that bundle and resumes collection with its session totals and sequence preserved; the pause is recorded as a gap. Failed SQLite flushes retain pending observations for retry. Retry occurs at the next scheduled check. An installation that needs administrator privileges is deferred without requesting elevation. Install the app in a location owned by and writable to the current user.

The normal Tauri exit/restart path runs after successful replacement. A one-use local marker makes that launch menu-bar-only. History and settings stay at `~/Library/Application Support/BandPeek/`, independently of bundle identity. Launch at Login stays in ServiceManagement; the updater never unregisters it. Local updater diagnostics are bounded to about 1 MiB plus one rotated file, contain no observations and are never uploaded. They can contain local paths, so review before sharing.

**Current key gate:** `plugins.updater.pubkey` is empty. This is an explicit unconfigured state, not a usable key. The app logs that updates are inactive and makes no update requests. Release validation rejects it for trusted releases. Do not distribute this build as an update-capable beta. `createUpdaterArtifacts` is enabled by the workflow overlay only when the real private signing secret is available.

## Owner step: create and safeguard the updater key

Stop here for owner handling. Do not ask an agent to create or transmit the private key. Run these commands yourself in a terminal from the repository root:

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
2. Push a version tag only when ready to run the candidate workflow. This work does not push a tag.
3. Actions runs the frontend build, rustfmt, Clippy with warnings denied, core and desktop tests, and the Apple Silicon release build. The platform matrix contains only the validated architecture.
4. A trusted tagged build produces `.app.tar.gz`, `.sig`, DMG, `latest.json` with `darwin-aarch64`, and `SHA256SUMS`. The official Tauri bundler signs updater artifacts; the release-only verifier rejects mismatched keys or tampered artifacts before upload. The updater archive contains the signed/notarized/stapled application. Prerelease tags produce GitHub prereleases.
5. **Publication needs explicit owner approval:** configure required reviewers on the `public-release` GitHub environment and set repository variable `PUBLIC_RELEASE_APPROVED_TAG` to the exact tag after checking every gate below. Unset it after use. Only that trusted tag can enter the publishing job. It creates the GitHub Release and uploads the manifest/artifacts. Candidate builds alone cannot publish.

Do not enable publication until the updater key is embedded, a matching-key signed update has completed end to end, CI is green for the release commit, author privacy is resolved, human checks are completed or explicitly accepted, Developer ID/notarization validation passes, and the owner approves publication. Existing `0.1.0-beta.1` clients cannot update to another artifact with the same version; use a newer version in a disposable validation bundle for the end-to-end update exercise. Keep test keys/configuration out of public builds.

## Required signed update exercise (pending owner key)

Use a disposable, user-writable copy of the installed bundle and isolated history/settings. With genuine signed metadata/artifacts available, verify: no-update, newer valid artifact, corrupt signature, corrupt archive, main-window-open deferral (including minimized), close-to-install, failure rollback/resumed collector, and tray-only relaunch. Check SQLite integrity and recorded pending observations, settings and ServiceManagement state across the actual replacement; test enabled and disabled login registration, then leave it disabled. Verify the old `nettop` PID is gone, the new child belongs only to the relaunched process, and there is never more than one collector. Unit tests and the validation-only restart seam do not replace this signed install exercise.

### Fixture validation before the repository becomes public

Normal releases always use GitHub Releases. For the owner-authorized signed exercise only, an installed validation bundle accepts `--validation-mode auto --validation-update-endpoint https://<fixture-host>/latest.json`. The fixture must have a valid HTTPS certificate and genuinely signed updater bytes matching the embedded public key; no TLS or signature bypass exists. Use isolated `BANDPEEK_DB_PATH` and `BANDPEEK_SETTINGS_PATH`. Send `update-check` on stdin for a check now and `update-state` for the lifecycle phase. An open main window must remain alive in `Ready`; close it to exercise actual verified installation/relaunch. Normal launches ignore the fixture flag. A temporary test host is unnecessary for production V1.

The key-configured candidate workflow can generate signed updater archives and manifests even before Apple credentials are available, but labels such artifacts LOCAL-VALIDATION-ONLY and cannot publish them. This allows ad-hoc signed replacement testing without representing the test bundle as a trusted public binary.
