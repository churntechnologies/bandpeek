# macOS interaction checks

Use this checklist before a release. Some paths are automated through BandPeek's own code (see *Automated* below). Everything under *Manual* needs a person at the Mac, because it involves real input, the real menu bar, or VoiceOver speech. Don't mark a manual item done unless you performed it.

Use the packaged app (`src-tauri/target/release/bundle/macos/BandPeek.app`, or the `.dmg`), not the development binary. Launch at Login and the app-menu names ("Quit BandPeek") only behave as shipped inside the bundle.

## Automated

`python3 scripts/m5_interaction_checks.py` (release binary, copy of the history database) drives the production code paths:

| Path | How it is exercised | Result (Milestone 5) |
|---|---|---|
| Close main window without quitting | `-[NSWindow performClose:]`: the red close button and Cmd+W call this | Window hidden, WebView destroyed, app keeps running |
| Reopen repeatedly | `open` → render → `close`, 3× here and 100× in `scripts/m5_window_cycles.py` | Pass, identical 840 × 600 page viewport each time |
| App menu Quit / Cmd+Q item | The real `NSMenuItem` (`terminate:`), dispatched from the run loop | Exit 0, `nettop` gone |
| Tray Quit | Click on the popup's Quit button | Exit 0, `nettop` gone |
| Forced termination | SIGTERM and SIGKILL | No orphaned `nettop` (PTY hang-up) |
| App menu contents | NSMenu listing | Quit BandPeek ⌘Q, Close Window ⌘W, Hide ⌘H |
| Accessible names | DOM audit of the main window, Settings sheet and popup | Every control named; decorative images/SVG hidden; segmented controls are labelled groups with pressed state; Settings is a labelled modal dialog |
| Keyboard | Escape in Settings and the popup; tab order read from the DOM | Escape closes both; order: ranges → Filter apps → Settings → sort buttons |
| Popup placement | Unit tests of the placement maths | Centred under the item, kept 8 pt inside the screen, correct on screens left/right of the main display |
| Launch at Login | `SMAppService` through the app's own commands; a login launch reproduced with the `oapp`+`lgit` Apple event (`scripts/m5_login_launch.js`) | Enable/disable, state after relaunch, login launch stays in the menu bar |

## Manual

Record the macOS version, Mac model and display setup for each run.

### Menu-bar item
1. Click the BandPeek item. The popup opens directly below it, aligned to the item, within about half a second, with no empty flash.
2. Click the item again. The popup closes, and it doesn't reopen immediately.
3. Open the popup and click elsewhere (desktop, another app). It closes.
4. Open the popup and press Escape. It closes.
5. Right-click the item. It behaves the same as a left click.
6. Watch the ↓/↑ rates for 30 seconds while downloading something. They update about every 5 seconds, and neighbouring menu-bar items don't shift sideways.

### Light and dark menu bar
1. Set System Settings → Appearance to Light. The mark and rates are dark and legible on the light menu bar.
2. Switch to Dark. They turn light without relaunching BandPeek.
3. With a light desktop picture and a translucent menu bar (if your macOS shows one), check contrast again.
4. In BandPeek Settings, set Appearance Light/Dark/System and confirm the main window and popup follow. The menu-bar item always follows the *menu bar*, not the BandPeek setting.

### Display with a notch (MacBook Pro/Air 2021 and later)
1. Add enough menu-bar items that BandPeek's item sits near the notch, or is hidden behind it.
2. When visible next to the notch, the popup opens fully on screen and isn't clipped by the notch.
3. If macOS hides the item behind the notch, note it. This is macOS behaviour, not something BandPeek can override.

### Multiple displays
1. Connect a second display and enable "Displays have separate Spaces".
2. Click the BandPeek item on each display's menu bar. The popup opens under the item you clicked, on that display.
3. Arrange the second display to the *left* of the main display (negative coordinates) and repeat.
4. Open the main window, move it to the second display, close it, and reopen it. It returns where you left it.

### Quit paths
1. With the main window focused, press **Cmd+Q**. BandPeek quits, and Activity Monitor shows no `nettop` process owned by you.
2. Relaunch and use **BandPeek → Quit BandPeek** from the menu bar. It quits the same way.
3. Relaunch, close the main window, and use **Quit** in the popup. It quits the same way.

### Close and reopen
1. Click the red close button. The window disappears, the Dock icon goes away, and the menu-bar item keeps updating.
2. Press **Cmd+W** in a new window. Same result.
3. Reopen from the popup (**Open BandPeek**) ten times in a row. It opens quickly each time, remembers the selected range, sort and filter, and doesn't flash white in Dark appearance.
4. Click the Dock icon while the window is open but behind other windows. The window comes to the front.

### Keyboard navigation
With System Settings → Keyboard → **Keyboard navigation** turned on (macOS only moves Tab focus to buttons when this is on):
1. In the main window, Tab moves through Today → Yesterday → Last 7 Days → Last 30 Days → Filter apps → Settings → the column sort buttons. A visible focus ring appears on each.
2. Space or Return activates the focused button.
3. In the filter field, Escape clears the text.
4. Open Settings: focus moves into the sheet, Tab stays inside it, Escape closes it.

### VoiceOver (Cmd+F5)
1. Main window: VoiceOver reads the range control as "Time range, group" and each range button with its selected state ("selected" or "pressed"), and the filter as "Filter apps, search text field".
2. The status line is read, for example "Tracking · Stored on this device only".
3. In the table, rows read as the application name followed by the values. Hover help text: a shared helper such as WebKit Networking has the help text "Shared system process …".
4. Settings sheet: "Settings, dialog". Each group (Appearance, Units, Open at login, History retention) is announced with its name.
5. Popup: "Open BandPeek, button" and "Quit, button" are reachable.
6. The menu-bar item is announced as "BandPeek".

### Launch at Login (installed app)
1. Copy BandPeek.app to /Applications and open it (see the README for ad-hoc-signed, unnotarized beta first-launch instructions).
2. Settings → Open at login → On. macOS may show a "Login Items added" notification. System Settings → General → Login Items lists BandPeek under "Open at Login".
3. Log out and log back in. BandPeek starts with **only** the menu-bar item; no window opens and there is no Dock icon.
4. Open the popup → Open BandPeek. The window opens normally.
5. Quit, then launch BandPeek manually from Finder. The main window opens.
6. Turn Open at login Off in BandPeek. BandPeek disappears from System Settings → Login Items. Log out and in: BandPeek doesn't start.
7. Turn it On in BandPeek, then Off in System Settings. Reopen BandPeek Settings: it shows Off.

## Final Packet/updater additions

- Repeat menu-bar click, theme, tinted-bar, highlight and notch checks in Speeds only, Icon + speeds and Icon only. Check the Packet shapes and the two-line 10 pt rates visually. Watch neighbouring items while rates change.
- Settings → Menu bar display persists after quit/relaunch; new installs default to Speeds only. Menu-bar rates remain decimal with one digit even if history units are GiB.
- Use the permanent bundle ID for the installed app. Repeat a real logout/login; leave Open at login Off when complete.
- After owner updater-key setup, complete the matching-key signed update exercise in [release engineering](release-engineering.md). Verify enabled/disabled login state through an actual signed replacement and leave it disabled afterward.
- For the approved `0.1.0-beta.1` unnotarized mode, validate a downloaded candidate's strict ad-hoc bundle signature and production-key updater signature, then check first launch through System Settings → Privacy & Security → Open Anyway if needed. Confirm the README and release body disclose that it is not Developer ID signed or notarized. Keep Gatekeeper and quarantine protections enabled. Repeat unauthenticated discovery/download after the separately approved publication.
- For Developer ID releases, check Gatekeeper, Developer ID, notarization and stapling on the downloaded candidate and repeat on the binary actually downloaded from the approved GitHub release.

These human checks remain pending unless separately recorded or explicitly accepted by the owner as beta limitations.
