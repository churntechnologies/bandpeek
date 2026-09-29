//! Window lifecycle. Closing the main window destroys its WebView (Milestone 2
//! measured hidden WebViews keeping WebKit helpers alive); collection continues
//! in the native process and the window is rebuilt on demand.
use super::Shell;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

pub const MAIN: &str = "main";
pub const POPUP: &str = "tray";
/// Approved content size and the handoff's suggested minimum (logical points).
const MAIN_SIZE: (f64, f64) = (840.0, 600.0);
const MAIN_MIN: (f64, f64) = (760.0, 480.0);
const POPUP_WIDTH: f64 = 320.0;
const POPUP_GAP: f64 = 6.0;
const SCREEN_MARGIN: f64 = 8.0;

#[derive(Default)]
pub struct PopupState {
    /// When the popup last closed by losing focus. A click on the menu-bar item
    /// itself first blurs the popup; that click must not immediately reopen it.
    closed_at: Option<Instant>,
    focused: bool,
}

pub fn open_main(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let built = WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
        .title("BandPeek")
        .inner_size(MAIN_SIZE.0, MAIN_SIZE.1)
        .min_inner_size(MAIN_MIN.0, MAIN_MIN.1)
        .visible(false)
        .focused(true)
        .build();
    match built {
        Ok(window) => {
            // The design's 840 × 600 is the area *below* the native title bar. Here
            // the content view spans the full frame under the title bar, so add
            // the measured title-bar inset rather than assume its height.
            #[cfg(target_os = "macos")]
            if let Ok(ns_window) = window.ns_window() {
                let inset = unsafe { super::macos::title_bar_inset(ns_window) };
                if inset > 0.0 {
                    let _ =
                        window.set_size(tauri::LogicalSize::new(MAIN_SIZE.0, MAIN_SIZE.1 + inset));
                    let _ = window.set_min_size(Some(tauri::LogicalSize::new(
                        MAIN_MIN.0,
                        MAIN_MIN.1 + inset,
                    )));
                }
            }
            let _ = window.center();
            let _ = window.show();
            let _ = window.set_focus();
            super::validation_line(format_args!("validation-state=main-open"));
        }
        Err(error) => super::log_error(format_args!(
            "BandPeek: could not open main window: {error}"
        )),
    }
}

/// Main window destroyed: leave the Dock and keep running from the menu bar.
pub fn main_destroyed(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
    super::validation_line(format_args!("validation-state=main-closed"));
    let _ = app;
}

pub fn toggle_popup(app: &AppHandle, anchor: tauri::Rect) {
    if let Some(window) = app.get_webview_window(POPUP) {
        let _ = window.destroy();
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
    let built = WebviewWindowBuilder::new(app, POPUP, WebviewUrl::App("index.html#tray".into()))
        .title("BandPeek")
        .inner_size(POPUP_WIDTH, 336.0)
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
            *super::lock(&app.state::<Shell>().popup) = PopupState::default();
            #[cfg(target_os = "macos")]
            if let Ok(ns_window) = window.ns_window() {
                unsafe { super::macos::style_popup(ns_window) };
            }
            // Never leave an invisible popup behind if the page fails to report.
            let handle = app.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(1500));
                let inner = handle.clone();
                let _ = handle.run_on_main_thread(move || {
                    if let Some(w) = inner.get_webview_window(POPUP) {
                        if !w.is_visible().unwrap_or(true) {
                            show_popup(&inner, 336.0);
                        }
                    }
                });
            });
        }
        Err(error) => super::log_error(format_args!(
            "BandPeek: could not open menu-bar popup: {error}"
        )),
    }
}

/// Called by the popup page once its content is laid out, so it never flashes empty.
pub fn show_popup(app: &AppHandle, height: f64) {
    if let Some(window) = app.get_webview_window(POPUP) {
        let height = height.clamp(120.0, 640.0).ceil();
        let _ = window.set_size(tauri::LogicalSize::new(POPUP_WIDTH, height));
        let _ = window.show();
        let _ = window.set_focus();
        #[cfg(target_os = "macos")]
        if let Ok(ns_window) = window.ns_window() {
            unsafe { super::macos::style_popup(ns_window) };
        }
    }
}

pub fn close_popup(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(POPUP) {
        let _ = window.destroy();
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
    let x = (centre_x / scale - POPUP_WIDTH / 2.0)
        .min(right - POPUP_WIDTH - SCREEN_MARGIN)
        .max(left + SCREEN_MARGIN);
    (x, bottom / scale + POPUP_GAP)
}
