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

**Owner provisioning completed.** Commit `129de7d` embeds the permanent updater public key. The owner reports both GitHub updater-signing secrets configured. This validation did not read, generate, replace, transmit or commit private key material. Losing the permanent private key prevents existing clients from trusting future updates.

The matching-production-key signed updater exercise is **complete**. Both owner-signed fixtures verify against the permanent embedded public key. Normal trusted localhost HTTPS delivered the artifacts to disposable installed bundles. Actual replacement, rejection, deferral, rollback, SQLite flush, collector lifecycle and enabled/disabled login preservation passed; concrete results are in section 18. No permanent private signing key or password was accessed, and real user settings/history were untouched.

Public-fixture unit tests and the earlier restart seam remain supporting checks. The completed exercise used the genuine production-key signatures and the official installer, without TLS/signature bypasses or changes to application trust configuration. Normal launches continue to use GitHub Releases.

## 7. GitHub workflow

The candidate workflow validates versions/tag/identity, frontend, Rust tests, rustfmt and Clippy, then builds Apple Silicon only. When configured, Tauri creates signed updater archives; a release-only verifier checks the signature against the embedded key before generating `latest.json` (`darwin-aarch64`) and checksums. Apple credentials enable Developer ID signing/notarization. Missing credentials yield private Actions artifacts labelled LOCAL-VALIDATION-ONLY.

Publication is a separate trusted-tag job gated by the exact `PUBLIC_RELEASE_APPROVED_TAG` variable and the `public-release` environment, which the owner must configure with required reviewers. No publishing action was invoked. Workflow YAML was parsed locally. PR #1 CI run `36542010010` is green at `129de7d1f05708cc5639a53bd75e4360be0f58af`. It contains no candidate artifacts. Hosted CI has not run for the subsequent local validation-documentation/preparation changes.

## 8–9. Apple trust and version

The environment has zero valid code-signing identities. The local `.app` and DMG were built successfully; the bundle is ad-hoc signed with hardened runtime, and `codesign --verify --deep --strict` passes. No Developer ID, notarization or stapling success is claimed. The local app is approximately 20.22 MiB and DMG 5.49 MiB.

Package metadata and lockfiles, Cargo package/lockfile and Tauri all agree on `0.1.0-beta.1`. The generated Info.plist has both bundle version fields at that version. Release tooling requires the exact `v0.1.0-beta.1` tag and derives manifest version from checked metadata.

## 10. Author privacy

The owner explicitly approved `ChurnTech <tech@churntech.com>` for public use. Local Git identity matches that approved author. Author privacy is resolved; no history rewrite is required or performed.

## 11. Early exit and lifecycle regression

The accepted Milestone 5 unexplained main-process exit remains a beta caveat; it has not been claimed fixed. Available diagnostic reports were reviewed. Separate reports identify a child-side, pre-exec Foundation/libplatform abort in the collector's `fork` path, with BandPeek as the parent. Those reports are not evidence that the main application exited; collection recovered. This macOS 27 diagnostic caveat should be included when reviewing future reports.

The final reasonable stress pass is recorded below. Draft tao/wry reports are in [upstream lifecycle issues](upstream-lifecycle-issues.md); they contain no personal paths and were not submitted.

## 12. Performance

### Earlier candidate samples (updater key unconfigured)

Earlier measurements are recorded after 30 seconds settling and at least 60 seconds sampling for each mode. CPU uses cumulative process CPU time divided by monotonic wall time (100% = one core); RSS is sampled every five seconds and summed, so shared pages may be counted twice. WebKit helpers are attributed by Launch Services name. Only one test instance runs at a time, on isolated history/settings, without concurrent builds or stress loads. The three tray samples share an instance. The visible sample uses a fresh instance after the initial visibility check detected an occluded page before sampling; no hidden-window measurement was accepted. Main-window visibility is required before and after its sample.

These historical performance samples used the then-unconfigured updater key. The permanent public key is now embedded; configured-updater measurements are recorded separately below. An intermediate custom status-subview implementation caused approximately 35% CPU through continual AppKit redraw; it was discarded and replaced by the native status-button cell before the final measurements.

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


### Configured updater repeat

All four modes were repeated with the permanent public key in a disposable installed beta.1 bundle and fresh isolated history/settings. The same instance served all four samples. Each had 30 seconds settling and at least 60 seconds sampling, with the same CPU/RSS attribution method; no build, traffic exercise or other BandPeek instance ran concurrently. The native updater performed its scheduled HTTPS no-update check using normal macOS certificate trust. The visible page reported `visible` before and after sampling. Raw measurements and the updater log are in `.validation/signed-updater/performance-configured.json` and `performance-configured.updater.log`.

| State | BandPeek CPU | nettop CPU | Helpers CPU | Combined CPU | Combined mean RSS | Sample |
|---|---:|---:|---:|---:|---:|---:|
| speeds only | 0.196% | 0.261% | 0.000% | 0.457% | 78.04 MiB | 61.29 s |
| icon and speeds | 0.228% | 0.326% | 0.000% | 0.554% | 77.76 MiB | 61.33 s |
| icon only | 0.278% | 0.310% | 0.000% | 0.588% | 52.65 MiB | 61.23 s |
| main visible | 0.536% | 0.276% | 0.601% | 1.413% | 146.87 MiB | 61.51 s |

Native physical footprints were 16.6 / 16.9 / 17.0 MiB in tray modes and 30.4 MiB visible. No WebKit helpers existed in the tray samples. Visible helper means were Web Content 37.23 MiB / 0.260% CPU, Graphics and Media 22.23 MiB / 0.325%, and Networking 5.29 MiB / 0.016%. Every component was present throughout its sample; the same single collector PID was owned by the app in all four samples. The process exited with status 0 and its collector was gone afterward.

Tray combined CPU remained below 0.59%, and visible combined CPU was 1.413% (0.222 percentage points above the earlier candidate sample). Native CPU remained below 0.54%. The new run used fresh history rather than the earlier copied-history workload, and ordinary desktop activity/RSS varied; this is a concrete repeat measurement, not a controlled attribution of every difference to the updater.

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
| Actual matching-key signed replacement | Passed all required runtime scenarios; four successful replacements, verified rejection and rollback |
| Configured updater performance | All four release modes sampled; real HTTPS check observed; one collector per sample |

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

Updater key provisioning, matching-key signed updater validation, localhost TLS trust setup and author-email approval are completed. Still required: protected release environment/reviewers, exact-tag publication approval, and completion or explicit acceptance of the human checks in [manual checks](manual-checks.md). The temporary exercise CA must be uninstalled by the owner after validation; the safe command is in [release engineering](release-engineering.md#owner-cleanup-of-the-exercise-ca).

## 16. Blockers before making source public

Permanent public-key embedding, owner signing, actual signed updater validation and author privacy are resolved.

1. Green GitHub CI for the final commit. PR CI at `129de7d` is green; subsequent local validation changes have not been pushed or checked by hosted CI.
2. Human checks completed or explicitly accepted as beta limitations: real menu-bar clicks/tint/highlight/notch/multiple displays, keyboard and VoiceOver, real logout/login, and the popup harness timeout plus documented early-exit/macOS child-fork caveats.
3. Explicit owner approval to make source public. Unauthenticated production GitHub release discovery remains untested while the repository/releases stay private; verify it from the approved public release as part of release readback. The matching-key localhost HTTPS fixture exercise is complete.

## 17. Additional blockers before public binary release

Developer ID Application credentials, successful trusted release CI with hardened runtime, notarization/stapling/Gatekeeper checks, and validation of the downloaded candidate. Public updater assets must be accessible without embedded credentials, with a manifest and archive signature matching the embedded key. Configure the protected environment and exact-tag variable only after all applicable gates pass, and obtain explicit owner approval. The present ad-hoc artifacts are not a final trusted public binary.

## 18. Completed matching-production-key signed updater exercise — 29 September

Source artifact commit: `129de7d1f05708cc5639a53bd75e4360be0f58af`. Embedded public-key configuration SHA-256: `b325059807642cf0278c371f77efd856581f8ffe271312f4069b303a18e72375`. The archive hashes remained unchanged after owner signing. Both genuine signatures passed `bandpeek-verify-update`; offline changed-byte checks also rejected signature/archive tampering.

The owner installed an exercise-specific CA in the macOS system trust store and generated a certificate with SANs for `localhost` and `127.0.0.1`. The Python HTTPS fixture listened only on `127.0.0.1:18443`; ordinary macOS `curl` and the actual installed updater verified TLS without insecure options, extra client roots or app changes. Nothing was uploaded or publicly hosted.

Each case used a separate disposable `BandPeek.app`, fresh `BANDPEEK_DB_PATH` and `BANDPEEK_SETTINGS_PATH`, and synthetic history. A config overlay made the baseline `0.1.0-beta.0` with the permanent public key; tracked versions stayed `0.1.0-beta.1`. Genuine owner-signed `0.1.0-beta.1` bytes were installed through the official updater. Evidence is git-ignored in `.validation/signed-updater/runtime-all.json`, `runtime-sqlite-flush.json`, `signature-results.json` and individual run logs.

| Required runtime scenario | Concrete result |
|---|---|
| No update | Same-version manifest returned `Idle`; bundle and collector stayed unchanged |
| Newer valid signed update | Four real beta.0 → beta.1 replacements; installed executable hash equals the signed target; strict bundle signature verification passed |
| Changed signature rejected | Official HTTPS download returned `Failed`; no installation, unchanged beta.0 bundle and original collector |
| Archive modified after signing rejected | Original signature plus changed bytes returned `Failed`; no installation, unchanged bundle and collector |
| Main window open defers install | Verified update stayed `Ready`; process and beta.0 bundle stayed intact |
| Minimized window defers install | Native Minimize menu action plus `is_minimized=true`; phase stayed `Ready`, original collector and beta.0 bundle remained |
| Closing window triggers install | Closing the minimized main window released installation; successful replacement and restart followed |
| Tray-only install/relaunch | Additional installs began after the main WebView was closed; restart reported `tray` before any deliberate main open; one-use marker consumed |
| SQLite flush before replacement | 16,826,928 download and 16,813,273 upload bytes were still buffered before installation; independent SQLite read at first observed bundle replacement contained them |
| History/settings preserved | SQLite integrity `ok`, synthetic 12,345/6,789-byte history row unchanged; appearance `dark`, units `binary`, retention 365 days and `icon_and_speeds` preserved byte-for-byte and read back from relaunched app |
| Intentional install failure/rollback | Production-key-signed valid gzip with invalid tar checksum passed download verification, failed official extraction; independent backup restored beta.0 with unchanged executable hash and a new bundle inode; no backup/marker left |
| Collector resumes after rollback | Same application remained alive; collector generation 1 → 2, sample sequence 3 → 4, session totals retained, pause recorded as a history gap |
| Old nettop gone | Every successful replacement observed the old child gone at replacement; rollback also reaped its old child |
| Exactly one collector after relaunch | Each successful successor had exactly one nettop child with the successor as parent; current app also resumed with one child after rollback |
| Login disabled survives | ServiceManagement remained `disabled` across signed replacements |
| Login enabled survives; disabled afterward | Enabled through `SMAppService`, updated/relaunched, read back `enabled`, then unregistered and read back `disabled`; final state is disabled |

Collector evidence (old → new): rollback `14530 → 14542`; close-to-install `14562 → 14595`; tray/disabled `14617 → 14644`; enabled-login `14667 → 14694`; buffered-SQLite install `14860 → 14901`. Successful original processes exited with status 0. The installer-failure case exercises genuine extraction failure and backup restoration; the existing shell test additionally covers a partially replaced bundle. No claim of a real logout/login or Developer ID/notarization success is made.

The locked core/release-signature suite (52 passed), locked desktop suite (50 core + 8 shell passed) and rustfmt passed. Validation scripts were syntax-checked and exercised directly. No release tag, GitHub Release, merge, repository visibility change or remote push occurred.

### Cleanup and final checks

The loopback HTTPS server was stopped and port 18443 no longer had a listener. All 18 observed collector PIDs and all disposable test app processes were absent. Temporary signed/corrupted fixture bytes, signatures, copied app bundles, isolated test DB/settings and localhost leaf certificate/key were removed. Runtime/performance results and local logs remain git-ignored. `git check-ignore` confirms the entire `.validation/` tree, including the remaining exercise-specific CA files, is excluded; no signing/TLS material is tracked or staged.

The exercise CA remains installed solely pending owner cleanup. No interactive trust removal was attempted. The exact owner command is in [release engineering](release-engineering.md#owner-cleanup-of-the-exercise-ca). After performance and runtime cleanup, the locked 52-test core/signature suite, 50 core + 8 shell desktop suite, rustfmt and release metadata/public-key validation all passed again. Only intentional validation harness/documentation changes are committed locally. Read-only GitHub checks confirmed the repository is private, PR #1 is open/unmerged, and existing hosted CI remains green at `129de7d`; final local changes have not been pushed.
