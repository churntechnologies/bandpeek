//! Menu-bar item: monochrome template mark plus compact live ↓/↑ rates.
use super::{windows, Shell};
use bandpeek_core::{model::LiveRates, settings::format_menu_rate, Monitor};
use std::sync::Mutex;
use tauri::{
    image::Image,
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager,
};

pub const TRAY_ID: &str = "bandpeek";
const TEMPLATE_MARK: &[u8] = include_bytes!("../../icons/PacketTemplate14@2x.png");

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

    let updates = super::lock(&app.state::<Mutex<Monitor>>()).subscribe_live();
    let handle = app.clone();
    std::thread::Builder::new()
        .name("bandpeek-tray-rates".into())
        .spawn(move || {
            while let Ok(rates) = updates.recv() {
                set_title(&handle, &rates);
                for label in [windows::MAIN, windows::POPUP] {
                    if handle
                        .get_window(label)
                        .is_some_and(|w| w.is_visible().unwrap_or(false))
                    {
                        let _ = handle.emit_to(label, "live-rates", &rates);
                    }
                }
            }
        })?;
    Ok(())
}

/// Rewrites the rates using current units. Safe to call from any thread.
pub fn refresh_title(app: &AppHandle) {
    let rates = super::lock(&app.state::<Mutex<Monitor>>())
        .snapshot
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .live_rates();
    set_title(app, &rates);
}

fn set_title(app: &AppHandle, rates: &LiveRates) {
    let mode = app.state::<Shell>().settings().menu_bar_display;
    let (sequence, down_bps, up_bps) = (
        rates.sample_sequence,
        rates.download_bytes_per_second,
        rates.upload_bytes_per_second,
    );
    let line = |arrow: &str, rate: f64| format!("{arrow} {}", format_menu_rate(rate));
    let (down, up) = (line("↓", down_bps), line("↑", up_bps));
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    #[cfg(target_os = "macos")]
    let _ = tray.with_inner_tray_icon(move |inner| {
        if let Some(item) = inner.ns_status_item() {
            super::macos::set_status_rates(&item, mode, &down, &up);
            if bandpeek_core::diagnostics::enabled() {
                bandpeek_core::diagnostics::trace(serde_json::json!({"stage":"status", "sequence":sequence,
                    "delivered_rx_bps":down_bps,"delivered_tx_bps":up_bps,
                    "formatted_rx":down,"formatted_tx":up,"native":super::macos::status_report(&item)}));
            }
        }
    });
    #[cfg(not(target_os = "macos"))]
    let _ = tray.set_tooltip(Some(format!("BandPeek  {down}  {up}")));
}

#[cfg(target_os = "macos")]
pub fn validation_status(app: &AppHandle, rates: Option<(f64, f64)>) {
    let mode = app.state::<Shell>().settings().menu_bar_display;
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.with_inner_tray_icon(move |inner| {
            if let Some(item) = inner.ns_status_item() {
                if let Some((down, up)) = rates {
                    super::macos::set_status_rates(
                        &item,
                        mode,
                        &format!("↓ {}", format_menu_rate(down)),
                        &format!("↑ {}", format_menu_rate(up)),
                    );
                }
                super::validation_line(format_args!(
                    "validation-status={}",
                    super::macos::status_report(&item)
                ));
            }
        });
    }
}
