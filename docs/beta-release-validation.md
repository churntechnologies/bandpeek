# Final macOS beta candidate validation

Validated on 29 September 2026, macOS 27.0, Apple Silicon, as the ordinary logged-in user. Version remains **0.1.0-beta.1**. This is a local candidate, not an approved public release. The repository remains private; no release, tag or upstream issue was published.

## 1. Branding

The authoritative Packet package supplies the app icon, native template PNGs, SVG geometry and colour tokens. The header uses the exact Packet paths at 16 pt, with a 6 pt wordmark gap. README branding and all four screenshots now use the new identity; screenshots use synthetic history. The previous template asset was removed. The current asset inventory contains no old C/open-lens image. Historical milestone prose remains labelled as historical.

## 2. Menu-bar modes

Settings persists `speeds_only` (new-user/default migration), `icon_and_speeds` and `icon_only`. The native status button renders two right-aligned lines with 10 pt medium system font and monospaced digits, one decimal and decimal B/s, KB/s, MB/s, GB/s independently of history units. Actual text measurement on this Mac reserves **68 pt** for the rate column. Total lengths are **80 / 98 / 28 pt** respectively, including 6 pt side padding. Icon + speeds has a 14 pt Packet and 4 pt visible gap; Icon only centres a 16 pt Packet. Native template tinting is enabled.

Release validation checks the font, template flag, geometry, default, on-disk setting and relaunched setting. Both the item length and actual native text-column/icon position remain fixed across zero, typical and wide rate strings. Human contrast, tint and highlight checks remain pending.

## 3. Permanent identity

Runtime configuration and bundle metadata use `io.github.churntechnologies.bandpeek`. The old identifier is absent from runtime sources/configuration, workflows, scripts and current README. Remaining historical references describe earlier validation only. Packaged `SMAppService` checks passed enable, persistence after relaunch and disable. A simulated real macOS login Apple event starts in the tray. **Launch at Login was left disabled.** A real logout/login remains a human check.

## 4–5. Updater architecture and behavior

The official Tauri 2 updater verifies signatures; a Rust worker discovers published GitHub Releases, including prereleases, and loads their official static manifest. Checks are scheduled 30 seconds after startup and every six hours. No repository token, telemetry or custom production server is used. The main WebView's lifetime gates installation, including minimized windows; closing it wakes installation. Native open/install reservation runs on the UI thread.

Installation stops/reaps the collector, requires successful SQLite flush, backs up the user-owned writable application, invokes the official installer and requests Tauri restart. A one-use marker returns that restart to the tray. Failed installation rolls back the bundle and resumes collection with totals retained and counters rebaselined; failed checks retry on the next schedule. Settings/history paths are unchanged and ServiceManagement is not unregistered. Logging stays local and bounded.

Two concrete correctness fixes were needed: SQLite must retain pending deltas and its identity cache after a failed transaction; collector resume must preserve session totals while rebaselining its new child. Tests cover both. The independent app backup handles the official macOS installer's move-before-final-rename failure case.

## 6. Signing key

**Pending owner handling.** No private updater key was generated or exposed. The embedded public key is intentionally empty, so this candidate logs an inactive updater and makes no update requests. Trusted-release validation rejects that state. The owner must run the safe commands in [release engineering](release-engineering.md), safeguard the private key/password and embed only its public value. Losing the private key prevents existing clients from trusting future updates.

Public-fixture cryptographic tests verify a genuine signature and reject changed bytes/signatures. They do not establish a matching production key or a signed end-to-end install. That exercise remains a release gate. An installed validation-only bundle supports an HTTPS fixture endpoint and immediate test check; embedded-key, certificate and signature verification remain mandatory. Normal launches use GitHub Releases.

## 7. GitHub workflow

The candidate workflow validates versions/tag/identity, frontend, Rust tests, rustfmt and Clippy, then builds Apple Silicon only. When configured, Tauri creates signed updater archives; a release-only verifier checks the signature against the embedded key before generating `latest.json` (`darwin-aarch64`) and checksums. Apple credentials enable Developer ID signing/notarization. Missing credentials yield private Actions artifacts labelled LOCAL-VALIDATION-ONLY.

Publication is a separate trusted-tag job gated by the exact `PUBLIC_RELEASE_APPROVED_TAG` variable and the `public-release` environment, which the owner must configure with required reviewers. No publishing action was invoked. Workflow YAML was parsed locally. Existing accepted-commit CI was green; **CI has not run for these new changes**.

## 8–9. Apple trust and version

The environment has zero valid code-signing identities. The local `.app` and DMG were built successfully; the bundle is ad-hoc signed with hardened runtime, and `codesign --verify --deep --strict` passes. No Developer ID, notarization or stapling success is claimed. The local app is approximately 20.22 MiB and DMG 5.49 MiB.

Package metadata and lockfiles, Cargo package/lockfile and Tauri all agree on `0.1.0-beta.1`. The generated Info.plist has both bundle version fields at that version. Release tooling requires the exact `v0.1.0-beta.1` tag and derives manifest version from checked metadata.

## 10. Author privacy

Both existing commits and local Git author configuration were audited. They use the same corporate-domain address, not Gmail and not a GitHub no-reply address. The exact address was reported privately to the owner. History was not rewritten and no identity was invented. Owner confirmation that this address may be public, or an owner-provided actual no-reply address plus a deliberate history rewrite, remains a source-publication gate.

## 11. Early exit and lifecycle regression

The accepted Milestone 5 unexplained main-process exit remains a beta caveat; it has not been claimed fixed. Available diagnostic reports were reviewed. Separate reports identify a child-side, pre-exec Foundation/libplatform abort in the collector's `fork` path, with BandPeek as the parent. Those reports are not evidence that the main application exited; collection recovered. This macOS 27 diagnostic caveat should be included when reviewing future reports.

The final reasonable stress pass is recorded below. Draft tao/wry reports are in [upstream lifecycle issues](upstream-lifecycle-issues.md); they contain no personal paths and were not submitted.

## 12. Performance

Final measurements are recorded after 30 seconds settling and at least 60 seconds sampling for each mode. CPU uses cumulative process CPU time divided by monotonic wall time (100% = one core); RSS is sampled every five seconds and summed, so shared pages may be counted twice. WebKit helpers are attributed by Launch Services name. Only one test instance runs at a time, on isolated history/settings, without concurrent builds or stress loads. The three tray samples share an instance. The visible sample uses a fresh instance after the initial visibility check detected an occluded page before sampling; no hidden-window measurement was accepted. Main-window visibility is required before and after its sample.

The updater integration is present but its worker is inactive until the owner's public key is embedded. Repeat measurements with the configured updater as part of the signed update exercise. An intermediate custom status-subview implementation caused approximately 35% CPU through continual AppKit redraw; it was discarded and replaced by the native status-button cell before the final measurements.

| State | BandPeek CPU | nettop CPU | Helpers CPU | Combined CPU | Combined mean RSS | Sample |
|---|---:|---:|---:|---:|---:|---:|
| speeds only | 0.211% | 0.276% | 0.000% | 0.487% | 73.46 MiB | 61.49 s |
| icon and speeds | 0.229% | 0.277% | 0.000% | 0.506% | 71.23 MiB | 61.26 s |
| icon only | 0.180% | 0.228% | 0.000% | 0.408% | 70.35 MiB | 61.28 s |
| main visible | 0.392% | 0.261% | 0.538% | 1.191% | 188.58 MiB | 61.29 s |

RSS component means (MiB, native / nettop / helpers):
- speeds only: 70.87 / 2.59 / 0.00
- icon and speeds: 68.51 / 2.72 / 0.00
- icon only: 67.79 / 2.56 / 0.00
- main visible: 104.97 / 2.41 / 81.20

Visible helpers: Web Content 0.261% CPU / 48.53 MiB RSS; Graphics and Media 0.261% / 25.08 MiB; Networking 0.016% / 7.59 MiB. No helpers existed during the tray samples. Every phase had exactly one collector owned by its app. Both test instances exited with status 0.

Compared with Milestone 5 (fresh tray 0.522% CPU / 75.3 MiB RSS; visible 1.305% / 186.5 MiB), these results show no material regression. Visible RSS differs by about 2.1 MiB; native footprint is 29.7 MiB versus 31.9 MiB. Tray footprints are 15.6–16.4 MiB versus 15.7 MiB. These are ordinary-desktop measurements, not an idle laboratory benchmark.


## 13. Tests and builds

| Check | Result |
|---|---|
| Core plus release signature tests | 52 passed |
| Desktop tests | 50 core tests plus 8 shell tests passed |
| rustfmt | Passed |
| Clippy, core/probe/release tools and desktop, warnings denied | Passed |
| TypeScript and Vite | Passed |
| Release desktop binary, app and DMG | Passed |
| Native status modes/persistence/widths | Passed |
| Milestone 4 functional checks | 50 passed, 0 failed |
| Milestone 5 interactions | 21 passed, 0 failed |
| Collector kill/recovery/cleanup | Generation 2 recovered, monotonic totals, no orphan children |
| Packaged updater restart seam | Old collector reaped, one new collector, SQLite integrity OK, settings and disabled login state retained, tray-only relaunch |
| Failed restart preparation | Current process resumes with exactly one new collector |
| Actual matching-key signed replacement | Pending owner key; restart seam is not an artifact-install test |

Milestone 4's Python expected-value helper was corrected to match the already accepted JavaScript decimal tie behavior; product formatting was unchanged. For checks that read historical data, an isolated copy was used. The system Python SQLite read-only WAL open failed before launching the interaction test; only the disposable source copy was converted to a rollback journal. `BANDPEEK_VALIDATION_SOURCE_DB` can select such a prepared fixture. Raw evidence is git-ignored and must not be published without privacy review.

### Final lifecycle stress

30 main-window open/close cycles all rendered the same 840 × 600 viewport. Of 50 popup cycles, 49 rendered successfully; cycle 36 was not observed before the harness timeout. A harness-launched popup can lose focus and close before inspection (the interaction harness already retries this case), but that explanation is not proven for this single timeout. Retain it for the human popup check; this is not a claim of 50/50 popup success. The process remained alive throughout and quit with exit 0. No unexplained main-process exit reproduced, and no new main-process crash report was found.

| Checkpoint | Native windows | Closed WebView/parent counts | Native footprint |
|---|---:|---:|---:|
| Fresh tray | 0 | 0 | 14.8 MiB |
| 10 main opens | 1 | 0 | 34.4 MiB |
| 30 main opens | 1 | 0 | 35.8 MiB |
| Then 50 popup attempts | 2 | 0 | 35.8 MiB |

The small upstream scheme-handler objects still accumulate, as accepted in Milestone 5; native window reuse and WebView destruction remain effective. Cleanup checks found no surviving test app or collector.

## 14. Commit and publication

Engineering changes are committed locally using the repository's existing legitimate identity. No remote push, tag, public visibility change, release or upstream issue is authorized or performed by this work. The commit hash is supplied in the owner's completion report.

## 15. Owner inputs

Required secrets: `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD` (app-specific), `APPLE_TEAM_ID`. The first two configure updater signing; the six Apple values must be supplied together. Full safe commands and value descriptions are in [release engineering](release-engineering.md).

Also required: author-email decision, protected release environment/reviewers, exact-tag publication approval, and completion or explicit acceptance of the human checks in [manual checks](manual-checks.md).

## 16. Blockers before making source public

1. Owner-created updater key with its genuine public value embedded, matching-key signed update exercise completed (including deferral, failure and enabled/disabled login preservation).
2. Green GitHub CI for the final commit; current local checks do not satisfy the hosted CI gate.
3. Author privacy decision resolved; use an owner-provided no-reply address if rewriting is desired.
4. Human checks completed or explicitly accepted as beta limitations: real menu-bar clicks/tint/highlight/notch/multiple displays, keyboard and VoiceOver, real logout/login, and the popup harness timeout plus documented early-exit/macOS child-fork caveats.
5. Explicit owner approval to make source public. Public GitHub update discovery cannot be fully exercised from unauthenticated clients while releases remain private; use the signed HTTPS fixture exercise first, then verify production discovery from the approved release as part of release readback.

## 17. Additional blockers before public binary release

Developer ID Application credentials, successful trusted release CI with hardened runtime, notarization/stapling/Gatekeeper checks, and validation of the downloaded candidate. Public updater assets must be accessible without embedded credentials, with a manifest and archive signature matching the embedded key. Configure the protected environment and exact-tag variable only after all applicable gates pass, and obtain explicit owner approval. The present ad-hoc artifacts and empty-key build are not a final trusted public binary.
