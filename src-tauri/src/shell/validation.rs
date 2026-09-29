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
    let Some(webview) = app.get_webview(label) else {
        return;
    };
    let _ = webview.with_webview(move |webview| {
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
    if let Some(rect) = app
        .tray_by_id(TRAY_ID)
        .and_then(|t| t.rect().ok().flatten())
    {
        windows::toggle_popup(app, rect);
    }
}

pub fn popup_identity(app: &AppHandle) {
    let window_count = app.windows().len();
    let webview_count = app.webviews().len();
    let visible = app
        .get_window(windows::POPUP)
        .is_some_and(|w| w.is_visible().unwrap_or(false));
    if let Some(webview) = app.get_webview(windows::POPUP) {
        let _ = webview.with_webview(move |view| {
            super::validation_line(format_args!("validation-popup-identity={}", serde_json::json!({
                "pointer":view.inner() as usize,"windows":window_count,"webviews":webview_count,"visible":visible
            })));
        });
    }
}

/// `geometry <label>`: native window geometry, for layout validation.
pub fn geometry(app: &AppHandle, label: &str) {
    let Some(window) = app.get_window(label) else {
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

/// `webview-geometry <label>`: the WKWebView's frame in window coordinates
/// (origin bottom-left) and the window's title-bar inset, to verify where the
/// page sits on screen without a screen capture.
pub fn webview_geometry(app: &AppHandle, label: &str) {
    let Some(webview) = app.get_webview(label) else {
        return;
    };
    let label = label.to_string();
    let _ = webview.with_webview(move |platform| {
        let view: &objc2_app_kit::NSView =
            unsafe { &*(platform.inner() as *const objc2_app_kit::NSView) };
        let in_window = view.convertRect_toView(view.bounds(), None);
        let window = view.window();
        let content = window.as_ref().map(|w| w.contentLayoutRect());
        let frame = window.as_ref().and_then(|w| w.contentView()).map(|v| v.frame());
        super::validation_line(format_args!(
            "validation-webview-geometry={label} webview_in_window={in_window:?} content_view={frame:?} layout={content:?} superview_flipped={:?}",
            unsafe { view.superview() }.map(|s| s.isFlipped())
        ));
    });
}

/// `resize <width> <height>`: content size below the title bar, as a user would drag it.
pub fn resize(app: &AppHandle, argument: &str) {
    let mut parts = argument
        .split_whitespace()
        .filter_map(|v| v.parse::<f64>().ok());
    let (Some(width), Some(height)) = (parts.next(), parts.next()) else {
        return;
    };
    if let Some(window) = app.get_window(windows::MAIN) {
        let inset = window
            .ns_window()
            .map(|w| unsafe { super::macos::title_bar_inset(w) })
            .unwrap_or(0.0);
        let _ = window.set_size(tauri::LogicalSize::new(width, height + inset));
    }
}

/// `front`: puts the main window on screen above other apps' windows without
/// activating BandPeek (a harness-launched app cannot take activation). WebKit
/// throttles occluded pages, so visible-state measurements need this.
pub fn front(app: &AppHandle) {
    if let Some(ptr) = app
        .get_window(windows::MAIN)
        .and_then(|w| w.ns_window().ok())
    {
        let window: &objc2_app_kit::NSWindow = unsafe { &*(ptr as *const objc2_app_kit::NSWindow) };
        window.orderFrontRegardless();
    }
}

/// `perform-close`: `-[NSWindow performClose:]` on the main window, the AppKit
/// path behind the red close button and Cmd+W (works without key focus).
pub fn perform_close(app: &AppHandle) {
    if let Some(ptr) = app
        .get_window(windows::MAIN)
        .and_then(|w| w.ns_window().ok())
    {
        let window: &objc2_app_kit::NSWindow = unsafe { &*(ptr as *const objc2_app_kit::NSWindow) };
        window.performClose(None);
    }
}

/// `menu`: lists the app menus with key equivalents. `menu <title>` performs
/// that item's action exactly as clicking it (or pressing its shortcut) does,
/// e.g. `menu Quit BandPeek` (Cmd+Q) or `menu Close Window` (Cmd+W).
///
/// The action is deferred to the main dispatch queue: harness commands run
/// inside tao's event-loop callback, and `terminate:` from there would
/// re-enter tao's handler (it deadlocks). AppKit dispatches real menu clicks
/// and shortcuts from the run loop, which is what the deferral reproduces.
pub fn menu(title: &str) {
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };
    let Some(main) = objc2_app_kit::NSApplication::sharedApplication(mtm).mainMenu() else {
        super::log_error(format_args!("no main menu"));
        return;
    };
    for i in 0..main.numberOfItems() {
        let Some(top) = main.itemAtIndex(i) else {
            continue;
        };
        let Some(submenu) = top.submenu() else {
            continue;
        };
        for j in 0..submenu.numberOfItems() {
            let Some(item) = submenu.itemAtIndex(j) else {
                continue;
            };
            let name = item.title().to_string();
            if title.is_empty() {
                if !name.is_empty() {
                    super::validation_line(format_args!(
                        "validation-menu-item={} > {name} [{}{:?}]",
                        top.title(),
                        item.keyEquivalent(),
                        item.keyEquivalentModifierMask()
                    ));
                }
            } else if name == title {
                super::validation_line(format_args!(
                    "validation-menu-perform={name} enabled={}",
                    item.isEnabled()
                ));
                on_main_queue(Box::new(move || submenu.performActionForItemAtIndex(j)));
                return;
            }
        }
    }
    if !title.is_empty() {
        super::log_error(format_args!("menu item not found: {title}"));
    }
}

type MainQueueWork = Box<dyn FnOnce()>;

/// Runs `work` from the main run loop via GCD (`dispatch_async_f`).
fn on_main_queue(work: MainQueueWork) {
    #[repr(C)]
    struct DispatchQueue {
        _private: [u8; 0],
    }
    unsafe extern "C" {
        static _dispatch_main_q: DispatchQueue;
        fn dispatch_async_f(
            queue: *const DispatchQueue,
            context: *mut std::ffi::c_void,
            work: extern "C" fn(*mut std::ffi::c_void),
        );
    }
    extern "C" fn trampoline(context: *mut std::ffi::c_void) {
        let work = unsafe { Box::from_raw(context as *mut MainQueueWork) };
        work();
    }
    let context = Box::into_raw(Box::new(work)) as *mut std::ffi::c_void;
    unsafe { dispatch_async_f(&raw const _dispatch_main_q, context, trampoline) };
}
