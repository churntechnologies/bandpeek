# BandPeek

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/mark-color-on-dark.svg">
  <img src="brand/mark-color.svg" width="56" alt="BandPeek Packet mark">
</picture>

**See which apps are using your network, and how much, from the menu bar.**

BandPeek is a free, open-source (MIT) menu-bar app. It shows live download and upload speeds in the menu bar, today's usage and top apps in a popup, and a history window with per-app totals for Today, Yesterday, the last 7 days and the last 30 days. Usage history stays on your Mac.

<p align="center">
  <img src="docs/screenshots/main-dark.png" width="640" alt="BandPeek main window in dark appearance: per-application download, upload and total for Today">
</p>
<p align="center">
  <img src="docs/screenshots/popup-light.png" width="300" alt="BandPeek menu-bar popup in light appearance: live speeds, today's total and top apps">
  <img src="docs/screenshots/main-light.png" width="520" alt="BandPeek main window in light appearance">
</p>

<sub>Screenshots use a demo database, not real usage.</sub>

## Status

| Platform | Status |
|---|---|
| **macOS** | **Beta.2 candidate, 0.1.0-beta.2; not tagged or published.** Apple Silicon, macOS 13 or later. Tested on macOS 27 only. |
| Windows | Not implemented. |
| Linux | Not implemented. |

The collector architecture is designed so other platforms can be added later, but nothing for Windows or Linux exists yet.

The owner has selected an **ad-hoc-signed, unnotarized public beta** for `0.1.0-beta.1`. It is **not Apple Developer ID signed or notarized**; Apple signing/notarization is deferred to a later release. Publication still requires exact-tag owner approval and the remaining release gates. The permanent production updater key is embedded, and updater signature verification remains mandatory. See [release engineering](docs/release-engineering.md).

## What it does

- **Menu bar:** Speeds only (default), Icon + speeds, or Icon only. Rates use a fixed-width, stacked ↓/↑ layout in decimal B/s, KB/s, MB/s or GB/s, with one decimal place; updated with each 2-second collector sample. The Packet mark is a native macOS template image.
- **Popup:** current speeds, today's total, and the top five apps today.
- **Main window:** per-app download, upload, total and share for Today, Yesterday, Last 7 Days and Last 30 Days. You can sort and filter the list.
- **Settings:** appearance (System/Light/Dark), units (GB or GiB), history retention (30 days to 1 year), clear history, **Menu bar display**, and **Open at login**.
- Closing the window keeps BandPeek in the menu bar. Quit from the popup or with Cmd+Q.

## What the numbers mean

BandPeek shows a **best-effort estimate of the TCP/UDP traffic each app's sockets sent and received**, as reported by macOS (`nettop`), sampled every 2 seconds. It is **not** an ISP meter, a billing meter or a wire-level (packet) meter, and its totals will not match your provider's.

- **All network interfaces**, including local network (LAN) and loopback traffic. There is no Wi-Fi/Ethernet split.
- **Very short-lived processes can be missed.** A process that starts, transfers data and exits between samples may not be counted.
- **Shared system helpers stay separate.** Processes such as *WebKit Networking*, *mDNSResponder* or *nsurlsessiond* carry traffic for other apps. BandPeek lists them on their own and doesn't guess which app they worked for.
- **Durability.** Traffic is saved about once a minute, so an abrupt crash or power loss can lose up to about a minute of recent activity.
- Time asleep and collector restarts are recorded as gaps; nothing is estimated for them.

The full evidence and limits are in the [macOS accuracy contract](docs/macos-accuracy-contract.md) and the milestone reports in [`docs/`](docs).

## Privacy

- Usage data is local only: no accounts, cloud history, analytics, telemetry, remote crash reporting or remote UI assets.
- Configured public builds contact GitHub to check for and download updates. GitHub receives ordinary request metadata such as your IP address; BandPeek never sends usage history or settings. No GitHub credentials are stored in the app.
- BandPeek reads per-process byte counters and local app metadata (names, bundle IDs, icons). It does **not** record destinations, domains, URLs or packet contents.
- No administrator rights, Full Disk Access, network extension or packet capture.
- History (`bandpeek.db`, per-minute totals per app) and `settings.json` are stored in `~/Library/Application Support/BandPeek/`. **Settings → Clear History** deletes the history. History older than your retention setting is deleted automatically.

## Install

### Build from source

Requirements: macOS 13+ on Apple Silicon, Node.js 22.12+, Rust stable, Xcode Command Line Tools.

```sh
git clone https://github.com/churntechnologies/bandpeek.git
cd bandpeek
npm ci
npm run desktop:bundle
```

This produces `src-tauri/target/release/bundle/macos/BandPeek.app` and `src-tauri/target/release/bundle/dmg/BandPeek_0.1.0-beta.2_aarch64.dmg`. Copy BandPeek.app to `/Applications`. Apps you build yourself open normally.

### Pre-built beta

`0.1.0-beta.2` is a locally validated candidate with live-rate and popup latency fixes; it has not been tagged or published. See the [beta.2 validation report](docs/beta2-validation.md). The prior exact-tag beta.1 release approval does not authorize publishing beta.2. When approved and published, download its DMG from [GitHub Releases](https://github.com/churntechnologies/bandpeek/releases), then copy BandPeek.app to `/Applications` or a user-owned Applications folder.

**This beta is not Developer ID signed or notarized.** On first launch, macOS may block it. Try opening BandPeek in Finder; if blocked and you choose to trust this beta, use **System Settings → Privacy & Security → Open Anyway**, then confirm **Open**. Follow [Apple's instructions](https://support.apple.com/en-us/102445). Keep Gatekeeper enabled and retain quarantine protections. Private Actions artifacts labelled LOCAL-VALIDATION-ONLY remain for validation.

The prerelease will include the DMG, signed updater archive and `.sig`, `latest.json`, and `SHA256SUMS`. See the [beta.2 candidate notes](docs/releases/0.1.0-beta.2.md) for limitations and installation details.

### Automatic updates

The official Tauri 2 updater is integrated with the permanent production public key. Installed Apple Silicon builds check shortly after launch and approximately every six hours, discovering beta prereleases through GitHub Releases. Public assets become available after publication. Valid newer updates download silently; installation waits for the main window to close, flushes SQLite, stops the collector, installs and relaunches into the menu bar. Failures leave the current process running and retry at a later check. Settings, history and macOS Launch at Login registration are preserved.

Matching-production-key signed installation/relaunch and rejection of tampered signatures/archives have completed end-to-end validation. HTTPS and updater signature verification are always required, including for this unnotarized beta. See [the owner setup and release gates](docs/release-engineering.md). Losing the private updater key prevents existing installations from trusting future updates.

### Open at login

Settings → **Open at login** registers BandPeek with macOS (`SMAppService`, the same list as System Settings → General → Login Items). When macOS starts it at login, BandPeek opens in the menu bar only, without a window. You can also switch it off in System Settings. Open at login is only available in the installed app, not in the development build.

## Development

```sh
npm ci
npm run desktop                    # development app
npm run build                      # TypeScript check + Vite build
cargo test --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
npm run desktop:build              # release binary without bundling
npm run desktop:bundle             # .app + .dmg
```

A collector-only CLI prints JSON snapshots (same core as the app):

```sh
cargo run --release --manifest-path src-tauri/Cargo.toml --no-default-features --features probe --bin bandpeek-probe -- 60
```

**Layout.** `src-tauri/src/collectors/` holds the platform collector boundary; `macos/` owns the `nettop` worker, CSV parsing and process identity. The other core modules:
- `aggregate.rs`: counters
- `db/`: SQLite history
- `presentation.rs`: display names
- `settings.rs`: preferences

`src-tauri/src/shell/` is the desktop shell: commands, windows, menu-bar item, Launch at Login, and validation-only hooks. `src/` is the React UI.

The validation and measurement scripts in `scripts/` run against release builds; see [CONTRIBUTING.md](CONTRIBUTING.md). Their raw output goes to the git-ignored `.validation/` directory and can contain local app names and paths, so don't publish it unreviewed. Release checks that need a person are listed in [docs/manual-checks.md](docs/manual-checks.md).

## Documentation

- [macOS accuracy contract](docs/macos-accuracy-contract.md) and [collector decision](docs/collector-decision.md)
- Milestone reports:
  - [1: collector](docs/validation.md)
  - [2: accuracy](docs/milestone-2.md)
  - [2.5: cadence](docs/milestone-2.5.md)
  - [3: persistence](docs/milestone-3.md)
  - [4: UI](docs/milestone-4.md)
  - [5: beta hardening](docs/milestone-5.md)
- [Release engineering and owner setup](docs/release-engineering.md)
- [Final beta validation](docs/beta-release-validation.md)
- [Manual release checks](docs/manual-checks.md)

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Please don't attach your own usage databases or raw validation output.

## License

[MIT](LICENSE)
