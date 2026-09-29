//! Validation-only helpers, reachable only through `--validation-mode` stdin
//! commands. They let harnesses capture BandPeek's own surfaces without the
//! Screen Recording permission: WebKit's public snapshot API for WebViews, and
//! AppKit's view caching for the menu-bar button (drawn in-process).
use super::{tray::TRAY_ID, windows};
use block2::RcBlock;
use objc2::MainThreadMarker;
use objc2_app_kit::{NSBitmapImageFileType, NSBitmapImageRep, NSImage};
use objc2_foundation::{NSDictionary, NSError};
use objc2_web_kit::WKWebView;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

fn write_png(image: &NSImage, path: &Path) {
    let png = image
        .TIFFRepresentation()
        .and_then(|tiff| NSBitmapImageRep::imageRepWithData(&tiff))
        .and_then(|rep| unsafe {
            rep.representationUsingType_properties(NSBitmapImageFileType::PNG, &NSDictionary::new())
        });
    match png {
        Some(data) => match std::fs::write(path, data.to_vec()) {
            Ok(()) => {
                super::validation_line(format_args!("validation-snapshot={}", path.display()))
            }
            Err(e) => super::log_error(format_args!("snapshot write failed: {e}")),
        },
        None => super::log_error(format_args!("snapshot encoding failed: {}", path.display())),
    }
}

fn snapshot_webview(app: &AppHandle, label: &str, path: PathBuf) {
    let Some(window) = app.get_webview_window(label) else {
        return;
    };
    let _ = window.with_webview(move |webview| {
        let view: &WKWebView = unsafe { &*(webview.inner() as *const WKWebView) };
        let block = RcBlock::new(move |image: *mut NSImage, _error: *mut NSError| {
            if let Some(image) = unsafe { image.as_ref() } {
                write_png(image, &path);
            }
        });
        unsafe { view.takeSnapshotWithConfiguration_completionHandler(None, &block) };
    });
}

fn snapshot_menu_bar_item(app: &AppHandle, path: PathBuf) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    let _ = tray.with_inner_tray_icon(move |inner| {
        let Some(mtm) = MainThreadMarker::new() else {
            return;
        };
        let Some(button) = inner.ns_status_item().and_then(|item| item.button(mtm)) else {
            return;
        };
        let bounds = button.bounds();
        if let Some(rep) = button.bitmapImageRepForCachingDisplayInRect(bounds) {
            button.cacheDisplayInRect_toBitmapImageRep(bounds, &rep);
            if let Some(data) = unsafe {
                rep.representationUsingType_properties(
                    NSBitmapImageFileType::PNG,
                    &NSDictionary::new(),
                )
            } {
                let _ = std::fs::write(&path, data.to_vec());
                super::validation_line(format_args!("validation-snapshot={}", path.display()));
            }
        }
    });
}

/// `snapshot <name>`: writes <dir>/<name>-{main,tray,menubar}.png for whichever exist.
pub fn snapshot(app: &AppHandle, dir: &Path, name: &str) {
    let _ = std::fs::create_dir_all(dir);
    snapshot_webview(app, windows::MAIN, dir.join(format!("{name}-main.png")));
    snapshot_webview(app, windows::POPUP, dir.join(format!("{name}-tray.png")));
    snapshot_menu_bar_item(app, dir.join(format!("{name}-menubar.png")));
}

/// `popup`: opens the menu-bar popup exactly as a click on the item does.
pub fn open_popup(app: &AppHandle) {
    if app.get_webview_window(windows::POPUP).is_some() {
        return;
    }
    if let Some(rect) = app
        .tray_by_id(TRAY_ID)
        .and_then(|t| t.rect().ok().flatten())
    {
        windows::toggle_popup(app, rect);
    }
}

/// `geometry <label>`: native window geometry, for layout validation.
pub fn geometry(app: &AppHandle, label: &str) {
    let Some(window) = app.get_webview_window(label) else {
        return;
    };
    let scale = window.scale_factor().unwrap_or(1.0);
    let inner = window.inner_size().map(|s| s.to_logical::<f64>(scale));
    let outer = window.outer_size().map(|s| s.to_logical::<f64>(scale));
    if let Ok(ptr) = window.ns_window() {
        let ns: &objc2_app_kit::NSWindow = unsafe { &*(ptr as *const objc2_app_kit::NSWindow) };
        let content = ns.contentView().map(|v| v.frame());
        super::validation_line(format_args!(
            "validation-geometry={label} inner={inner:?} outer={outer:?} frame={:?} content_view={content:?} layout={:?} style_mask={:?} visible={} key={} level={} app_active={}",
            ns.frame(),
            ns.contentLayoutRect(),
            ns.styleMask(),
            ns.isVisible(),
            ns.isKeyWindow(),
            ns.level(),
            objc2_app_kit::NSApplication::sharedApplication(MainThreadMarker::new().unwrap()).isActive()
        ));
    }
}

/// `resize <width> <height>`: content size below the title bar, as a user would drag it.
pub fn resize(app: &AppHandle, argument: &str) {
    let mut parts = argument
        .split_whitespace()
        .filter_map(|v| v.parse::<f64>().ok());
    let (Some(width), Some(height)) = (parts.next(), parts.next()) else {
        return;
    };
    if let Some(window) = app.get_webview_window(windows::MAIN) {
        let inset = window
            .ns_window()
            .map(|w| unsafe { super::macos::title_bar_inset(w) })
            .unwrap_or(0.0);
        let _ = window.set_size(tauri::LogicalSize::new(width, height + inset));
    }
}
