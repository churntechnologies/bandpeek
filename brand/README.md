# BandPeek brand — final (Packet)

This package replaces every earlier BandPeek mark. The open-lens / C-shaped mark is retired; do not ship it anywhere.

## The mark
A data packet (solid dot) preceded by a short trail segment, moving left to right. Two solid shapes, one 2 px gap.

Geometry, 14 × 14 grid (units = pt at 14 pt):
- Dot: circle, centre (10, 7), radius 3.5 → spans x 6.5–13.5, y 3.5–10.5
- Trail: pill, x 0.5–4.5, y 5.5–8.5 (4 × 3), end radius 1.5
- Gap between trail and dot: 2
- Optical bounds: x 0.5–13.5, y 3.5–10.5 (13 × 7), vertically centred on 7

SVG paths:
- Dot: `M13.5 7A3.5 3.5 0 1 1 6.5 7A3.5 3.5 0 1 1 13.5 7Z`
- Trail: `M2 5.5H3A1.5 1.5 0 0 1 3 8.5H2A1.5 1.5 0 0 1 2 5.5Z`

Scale uniformly only. Do not add strokes, outlines, a tail taper, extra trail segments or a pointer tip. At @2x every edge falls on a whole pixel.

## Colours
| Use | Dot | Trail |
|---|---|---|
| Template (menu bar) | #000000 | #000000 |
| mark-light.svg (on light) | #1d1d1f | #1d1d1f |
| mark-dark.svg (on dark) | #ececea | #ececea |
| mark-color.svg (on light) | #037ac0 (down, light) | #c8741d (up, light) |
| mark-color-on-dark.svg | #65b4e9 (down, dark) | #dea45f (up, dark) |
| App icon | #65b4e9 | #dea45f |

Dot = download blue, trail = upload amber, the same tokens as the app (see specs/colors.md in the app handoff).
App icon background: vertical gradient #2c2d31 → #18191b.

## Menu bar
Files: `menu-bar/template.svg`, `12.png`, `14.png`, `16.png` and 2× variants `12-2x.png`, `14-2x.png`, `16-2x.png`. Black on transparent.

File naming: 2× files use `-2x` in this package. Rename `-2x` to `@2x` when adding them to Xcode or an `.iconset`.
- Load as a template image (`NSImage.isTemplate = true`; for PNG assets name them e.g. `BandPeekTemplate`) so macOS tints them for light/dark bars, the highlighted state and tinted menu bars. Never colour the menu-bar mark.
- Text: system font 10 pt medium, monospaced digits (`NSFont.monospacedDigitSystemFont(ofSize: 10, weight: .medium)`), two lines at 10 pt line height, right-aligned, label colour (let the system tint it).
- Format: `↓ 14.3 KB/s` over `↑ 17.8 KB/s`. One decimal; units B/s, KB/s, MB/s, GB/s.
- Fixed width: reserve the width of the widest string, `↓ 888.8 MB/s`, measured once with the menu-bar font, and set a fixed `NSStatusItem.length`. The item must never resize as rates change.
- Reference width for that string at 10 pt medium: about 62 pt. Always measure it at runtime rather than hard-coding it.
- Horizontal padding: 6 pt each side of the content.

Display modes (user setting):
1. **Speeds only (default)**: stacked rates only.
2. **Icon + speeds**: 14 pt mark, 4 pt gap, stacked rates.
3. **Icon only**: 16 pt mark, centred.

A one-line layout was tested and dropped: with both rates on one row it is wider than the stacked layout.

## App header / About / GitHub
- App header: `mark-color.svg` (light theme) or `mark-color-on-dark.svg` (dark theme), 16 pt, 6 pt gap before the "BandPeek" name.
- README / GitHub / About: `mark-color.svg` on light, `mark-color-on-dark.svg` on dark, or the 1024 app icon for social previews.
- Single-colour contexts: `mark-light.svg` / `mark-dark.svg`.
- Clear space: at least the dot radius (3.5 grid units) on all sides.

## App icon
`app-icon/source.svg` (1024 master, macOS 824 px squircle content area with 100 px margin; mark scaled 44×, centred) and PNGs for an `.iconset`:
Names below use `@2x`; in this package those files are named `-2x` (rename before running iconutil).
icon_16x16, icon_16x16@2x, icon_32x32, icon_32x32@2x, icon_128x128, icon_128x128@2x, icon_256x256, icon_256x256@2x, icon_512x512, icon_512x512@2x, plus icon-1024.png.
Build: `iconutil -c icns BandPeek.iconset` after renaming the folder to `BandPeek.iconset`, or drop the PNGs into an Xcode AppIcon asset.
