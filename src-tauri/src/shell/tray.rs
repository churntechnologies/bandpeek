//! Menu-bar item: monochrome template mark plus compact live ↓/↑ rates.
use super::{windows, Shell};
use bandpeek_core::{collectors::SharedSnapshot, settings::format_bytes, Monitor};
use std::{sync::Mutex, time::Duration};
use tauri::{
    image::Image,
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager,
};

pub const TRAY_ID: &str = "bandpeek";
const TEMPLATE_MARK: &[u8] = include_bytes!("../../icons/tray-template.png");

pub fn install(app: &AppHandle) -> tauri::Result<()> {
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(Image::from_bytes(TEMPLATE_MARK)?)
        .icon_as_template(true)
        .tooltip("BandPeek")
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                rect,
                button: MouseButton::Left | MouseButton::Right,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                windows::toggle_popup(tray.app_handle(), rect);
            }
        })
        .build(app)?;
    refresh_title(app);

    // The snapshot changes once per collector frame (5 s). Checking it each
    // second costs one mutex read; the title is only rewritten on change.
    let snapshot: SharedSnapshot = super::lock(&app.state::<Mutex<Monitor>>()).snapshot.clone();
    let handle = app.clone();
    std::thread::Builder::new()
        .name("bandpeek-tray-rates".into())
        .spawn(move || {
            let mut last = u64::MAX;
            loop {
                std::thread::sleep(Duration::from_secs(1));
                let sequence = snapshot.lock().map(|s| s.sample_sequence).unwrap_or(0);
                if sequence != last {
                    last = sequence;
                    refresh_title(&handle);
                }
            }
        })?;
    Ok(())
}

/// Rewrites the rates using current units. Safe to call from any thread.
pub fn refresh_title(app: &AppHandle) {
    let units = app.state::<Shell>().settings().units;
    let (down, up) = {
        let monitor = app.state::<Mutex<Monitor>>();
        let monitor = super::lock(&monitor);
        let snapshot = super::lock(&monitor.snapshot);
        (
            snapshot.download_bytes_per_second,
            snapshot.upload_bytes_per_second,
        )
    };
    let line = |arrow: &str, rate: f64| {
        let (n, u) = format_bytes(rate, units);
        format!("{arrow} {n} {u}/s")
    };
    let (down, up) = (line("↓", down), line("↑", up));
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    #[cfg(target_os = "macos")]
    let _ = tray.with_inner_tray_icon(move |inner| {
        if let Some(item) = inner.ns_status_item() {
            super::macos::set_status_rates(&item, &down, &up);
        }
    });
    #[cfg(not(target_os = "macos"))]
    let _ = tray.set_tooltip(Some(format!("BandPeek  {down}  {up}")));
}
