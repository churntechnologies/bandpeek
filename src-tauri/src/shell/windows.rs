//! Window lifecycle. Closing a surface destroys its WebView (Milestone 2
//! measured hidden WebViews keeping WebKit helpers busy); collection continues
//! in the native process.
//!
//! Each surface keeps one native window for the life of the process: it is
//! hidden, not destroyed, and a fresh WebView is attached on every open.
//! Destroying the NSWindow itself is not an option with tao 0.37: its window
//! constructor takes an extra retain that is never balanced, so every destroyed
//! window (plus its content view) stayed allocated — ~0.7 MB per main-window
//! open, ~50 KB per popup open (Milestone 5 report).
use super::Shell;
use std::time::{Duration, Instant};
use tauri::{
    webview::WebviewBuilder, window::WindowBuilder, AppHandle, LogicalPosition, LogicalSize,
    Manager, WebviewUrl, Window,
};

pub const MAIN: &str = "main";
pub const POPUP: &str = "tray";
/// Approved content size and the handoff's suggested minimum (logical points).
const MAIN_SIZE: (f64, f64) = (840.0, 600.0);
const MAIN_MIN: (f64, f64) = (760.0, 480.0);
const POPUP_WIDTH: f64 = 320.0;
const POPUP_INITIAL_HEIGHT: f64 = 336.0;
const POPUP_GAP: f64 = 6.0;
const SCREEN_MARGIN: f64 = 8.0;

#[derive(Default)]
pub struct PopupState {
    /// When the popup last closed by losing focus. A click on the menu-bar item
    /// itself first blurs the popup; that click must not immediately reopen it.
    closed_at: Option<Instant>,
    focused: bool,
}

/// Attaches a new WebView filling the window's content area.
fn attach_webview(window: &Window, label: &str, url: &str) -> tauri::Result<()> {
    let scale = window.scale_factor()?;
    let size = window.inner_size()?.to_logical::<f64>(scale);
    window.add_child(
        WebviewBuilder::new(label, WebviewUrl::App(url.into()))
            .auto_resize()
            .focused(true),
        LogicalPosition::new(0.0, 0.0),
        size,
    )?;
    Ok(())
}

fn build_main_window(app: &AppHandle) -> tauri::Result<Window> {
    let window = WindowBuilder::new(app, MAIN)
        .title("BandPeek")
        .inner_size(MAIN_SIZE.0, MAIN_SIZE.1)
        .min_inner_size(MAIN_MIN.0, MAIN_MIN.1)
        .visible(false)
        .focused(true)
        .build()?;
    // The design's 840 × 600 is the area *below* the native title bar. Here
    // the content view spans the full frame under the title bar, so add
    // the measured title-bar inset rather than assume its height.
    #[cfg(target_os = "macos")]
    if let Ok(ns_window) = window.ns_window() {
        let inset = unsafe { super::macos::title_bar_inset(ns_window) };
        if inset > 0.0 {
            let _ = window.set_size(LogicalSize::new(MAIN_SIZE.0, MAIN_SIZE.1 + inset));
            let _ = window.set_min_size(Some(LogicalSize::new(MAIN_MIN.0, MAIN_MIN.1 + inset)));
        }
    }
    let _ = window.center();
    Ok(window)
}

pub fn open_main(app: &AppHandle) {
    if !super::updates::open_main(app) {
        return;
    }
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    let window = match app.get_window(MAIN) {
        Some(window) => window,
        None => match build_main_window(app) {
            Ok(window) => window,
            Err(error) => {
                super::log_error(format_args!(
                    "BandPeek: could not open main window: {error}"
                ));
                super::updates::close_main(app);
                return;
            }
        },
    };
    if app.get_webview(MAIN).is_none() {
        if let Err(error) = attach_webview(&window, MAIN, "index.html") {
            super::log_error(format_args!(
                "BandPeek: could not create main view: {error}"
            ));
            super::updates::close_main(app);
            return;
        }
        super::validation_line(format_args!("validation-state=main-open"));
    }
    let _ = window.unminimize();
    let _ = window.show();
    let _ = window.set_focus();
}

/// Close button, Cmd+W or the harness: hide the window, destroy its WebView,
/// leave the Dock and keep running from the menu bar.
pub fn close_main(app: &AppHandle) {
    if let Some(window) = app.get_window(MAIN) {
        let _ = window.hide();
    }
    if let Some(webview) = app.get_webview(MAIN) {
        let _ = webview.close();
    }
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
    if app.get_webview(MAIN).is_none() {
        super::updates::close_main(app);
    }
    super::validation_line(format_args!("validation-state=main-closed"));
}

pub fn toggle_popup(app: &AppHandle, anchor: tauri::Rect) {
    if app.get_webview(POPUP).is_some() {
        close_popup(app);
        return;
    }
    {
        let shell = app.state::<Shell>();
        let popup = super::lock(&shell.popup);
        if popup
            .closed_at
            .is_some_and(|t| t.elapsed() < Duration::from_millis(300))
        {
            return;
        }
    }
    let (x, y) = popup_origin(app, anchor);
    let window = match app.get_window(POPUP) {
        Some(window) => {
            let _ = window.set_position(LogicalPosition::new(x, y));
            window
        }
        None => {
            let built = WindowBuilder::new(app, POPUP)
                .title("BandPeek")
                .inner_size(POPUP_WIDTH, POPUP_INITIAL_HEIGHT)
                .position(x, y)
                .decorations(false)
                .resizable(false)
                .minimizable(false)
                .maximizable(false)
                .skip_taskbar(true)
                .always_on_top(true)
                .visible_on_all_workspaces(true)
                .shadow(true)
                .visible(false)
                .build();
            match built {
                Ok(window) => {
                    #[cfg(target_os = "macos")]
                    if let Ok(ns_window) = window.ns_window() {
                        unsafe { super::macos::style_popup(ns_window) };
                    }
                    window
                }
                Err(error) => {
                    super::log_error(format_args!(
                        "BandPeek: could not open menu-bar popup: {error}"
                    ));
                    return;
                }
            }
        }
    };
    *super::lock(&app.state::<Shell>().popup) = PopupState::default();
    if let Err(error) = attach_webview(&window, POPUP, "index.html#tray") {
        super::log_error(format_args!(
            "BandPeek: could not create menu-bar popup view: {error}"
        ));
        return;
    }
    // Never leave an invisible popup behind if the page fails to report.
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(1500));
        let inner = handle.clone();
        let _ = handle.run_on_main_thread(move || {
            let hidden = inner
                .get_window(POPUP)
                .is_some_and(|w| !w.is_visible().unwrap_or(true));
            if hidden && inner.get_webview(POPUP).is_some() {
                show_popup(&inner, POPUP_INITIAL_HEIGHT);
            }
        });
    });
}

/// Called by the popup page once its content is laid out, so it never flashes empty.
pub fn show_popup(app: &AppHandle, height: f64) {
    if app.get_webview(POPUP).is_none() {
        return;
    }
    if let Some(window) = app.get_window(POPUP) {
        let height = height.clamp(120.0, 640.0).ceil();
        let _ = window.set_size(LogicalSize::new(POPUP_WIDTH, height));
        let _ = window.show();
        let _ = window.set_focus();
        #[cfg(target_os = "macos")]
        if let Ok(ns_window) = window.ns_window() {
            unsafe { super::macos::style_popup(ns_window) };
        }
    }
}

pub fn close_popup(app: &AppHandle) {
    if let Some(window) = app.get_window(POPUP) {
        let _ = window.hide();
    }
    if let Some(webview) = app.get_webview(POPUP) {
        let _ = webview.close();
    }
}

pub fn popup_focus_changed(app: &AppHandle, focused: bool) {
    let shell = app.state::<Shell>();
    let mut popup = super::lock(&shell.popup);
    if focused {
        popup.focused = true;
    } else if popup.focused {
        popup.focused = false;
        popup.closed_at = Some(Instant::now());
        drop(popup);
        close_popup(app);
    }
}

/// Top-left of the popup in logical points: centred under the menu-bar item,
/// 6 pt below it, kept on the item's screen.
fn popup_origin(app: &AppHandle, anchor: tauri::Rect) -> (f64, f64) {
    let position = anchor.position.to_physical::<f64>(1.0);
    let size = anchor.size.to_physical::<f64>(1.0);
    let centre_x = position.x + size.width / 2.0;
    let bottom = position.y + size.height;
    let monitor = app
        .monitor_from_point(centre_x, position.y)
        .ok()
        .flatten()
        .or_else(|| app.primary_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        return (centre_x - POPUP_WIDTH / 2.0, bottom + POPUP_GAP);
    };
    let scale = monitor.scale_factor();
    let left = monitor.position().x as f64 / scale;
    let right = left + monitor.size().width as f64 / scale;
    (
        popup_x(centre_x / scale, left, right),
        bottom / scale + POPUP_GAP,
    )
}

/// Horizontal popup position (logical points) for an item centred at
/// `centre_x` on a screen spanning `left..right`: centred, but never closer
/// than the margin to either screen edge. Screens left of the main display
/// have negative coordinates.
fn popup_x(centre_x: f64, left: f64, right: f64) -> f64 {
    (centre_x - POPUP_WIDTH / 2.0)
        .min(right - POPUP_WIDTH - SCREEN_MARGIN)
        .max(left + SCREEN_MARGIN)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn popup_is_centred_under_the_item() {
        assert_eq!(popup_x(1000.0, 0.0, 1512.0), 840.0);
    }

    #[test]
    fn popup_stays_on_screen_near_the_right_edge() {
        // Items right of a notch sit close to the right edge.
        assert_eq!(popup_x(1490.0, 0.0, 1512.0), 1512.0 - 320.0 - 8.0);
    }

    #[test]
    fn popup_stays_on_screen_near_the_left_edge() {
        assert_eq!(popup_x(20.0, 0.0, 1512.0), 8.0);
    }

    #[test]
    fn popup_uses_the_screen_the_item_is_on() {
        // Secondary display to the left of the main one (negative x).
        assert_eq!(popup_x(-100.0, -1920.0, 0.0), -320.0 - 8.0);
        assert_eq!(popup_x(-1000.0, -1920.0, 0.0), -1160.0);
        // Secondary display to the right.
        assert_eq!(popup_x(3400.0, 1512.0, 3432.0), 3432.0 - 328.0);
    }
}
