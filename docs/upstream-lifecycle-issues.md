# Draft upstream reports (not submitted)

These reports use framework names only; attach a minimal standalone reproducer,
not BandPeek's private measurements or local app data. They do not block the beta.

## tao 0.37.1: macOS native windows remain retained after close

**Environment:** macOS 27, Apple Silicon, Tauri 2.12, wry 0.57, tao 0.37.1.

**Reproducer:** create a window with a WebView, show it, close/destroy both;
repeat 10, 50 and 100 times in one process. After settling, inspect live
`TaoWindow`/delegate/view counts and physical footprint using `heap` and `vmmap`.

**Expected:** destroyed native windows and their content views are released.
**Observed in BandPeek's Milestone 5 comparison:** live `TaoWindow` count tracks
opens (10, 50, 101); physical footprint grows by approximately 0.74 MiB per open.
The constructor appears to take a retain not balanced by destruction. Reusing one
native window and destroying/recreating only its WebView holds the window count
at one and removes the material growth. No retain-count workaround is used.

**Request:** review constructor/destructor ownership and confirm the correct
public lifecycle. The application workaround remains valid when fixed upstream.

## wry 0.57: URL scheme handlers retained after WebView close

Use the same loop while reusing one native window. Count objects matching
`URLSchemeHandler_` after destroying each WebView. Milestone 5 observed four
small handlers retained per open, while `WryWebViewParent` returns to zero. Each
is well below 1 KiB, so this is not a release blocker. Review the handler
ownership in the macOS custom protocol registration/destruction path.

Both reports need a standalone reproducer verified against the latest upstream
release before submission. This task does not publish issues.
