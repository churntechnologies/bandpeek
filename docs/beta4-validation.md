# beta.4 live per-app speeds

Local validation on Apple Silicon, macOS 27.0.1 (26A434), 2026-10-24.
Candidate version: `0.1.0-beta.4`. No tag, publication or installed-app replacement.

## Change

The collector already computed per-process byte deltas every two-second sample,
but used them only for the total rate and minute history. `Aggregator::apply`
now also sums each sample's deltas per application identity (`stable_key`, the
same key as history rows, so helpers inside an app bundle count as the app) and
divides by the measured sample spacing. The 25 busiest apps travel in the
existing `live-rates` notification, so no window polls for them. Restarts,
topology rebuilds and discarded frames clear the list together with the total
rate. The main window's tab state accepts `now` alongside the history ranges.

## Automated checks

- `npm run build`, `cargo fmt --check`, clippy (both CI feature sets) and
  `cargo test` (58 library and 11 application tests). New tests cover helper
  grouping into one app, busiest-first ordering, idle apps dropping out of the
  next sample, no rate before a baseline, gap clearing, the 25-app cap and the
  `now` tab name round-trip.
- `scripts/release_config.py --require-key --tag v0.1.0-beta.4` and the release
  policy unit tests (22) pass.

## Measured validation

`scripts/beta4_live_apps_validation.py` ran the release binary with an isolated
database and settings, without recording history. A local HTTP server streamed
zeros and `curl --limit-rate 4000000` downloaded them:

| Check | Result |
|---|---|
| curl in the Now tab | `3.97 MB/s` download, `0 KB/s` upload, 49% share |
| Rate accuracy | 3,970,000 B/s against a 4,000,000 B/s limit (−0.75%) |
| curl in popup "Using now" | listed, `3.97 MB/s`; three lines shown |
| After curl exits (7 s) | gone from both views |
| Popup height busy / idle | 336 / 336 pt |
| Now tab persisted natively | `{"range":"now"}` |
| App exit | clean; the installed BandPeek and its `nettop` were untouched |

The serving side appeared as "Xcode" at the same rate: `/usr/bin/python3` runs
Xcode's bundled Python, and BandPeek attributes a process inside an application
bundle to that bundle. This is the existing identity rule, not a new behavior.

Light and dark snapshots of the Now tab and the popup were reviewed. An earlier
manual run during development showed a real process downloading about 10 MB/s
at the top of both views.

## Limits

Speeds lag by about one sample. Traffic carried by shared macOS helpers is
shown under the helper (WebKit Networking, `nsurlsessiond`, `mDNSResponder`).
Processes that live shorter than one sample may not appear. Human checks of
real clicks on the Now tab and popup remain manual, as in earlier betas.

## Reproduction tools and private evidence

```bash
npm run desktop:build
python3 scripts/beta4_live_apps_validation.py
```

Run outside the agent sandbox (`nettop`). Snapshots and `report.json` are kept
in ignored `.validation/beta4/`; they contain local process names.
