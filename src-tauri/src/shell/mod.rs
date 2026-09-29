//! Desktop shell: commands, settings, window lifecycle and the menu-bar item.
//! Collection lives in `bandpeek_core::Monitor` and never depends on a window.
#[cfg(target_os = "macos")]
pub mod macos;
pub mod tray;
#[cfg(target_os = "macos")]
pub mod validation;
pub mod windows;

use bandpeek_core::{
    model::{HistoryRange, HistoryView, LiveRates},
    settings::{Appearance, Settings},
    Monitor,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    io::Write,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex, MutexGuard, PoisonError,
    },
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager, State};

static VALIDATION: AtomicBool = AtomicBool::new(false);

/// Harness output (`--validation-mode` only). Best effort: a closed stdout must
/// never panic the app (`println!` does).
pub fn validation_line(line: std::fmt::Arguments) {
    if VALIDATION.load(Ordering::Relaxed) {
        let mut out = std::io::stdout().lock();
        let _ = out
            .write_fmt(line)
            .and_then(|_| out.write_all(b"\n"))
            .and_then(|_| out.flush());
    }
}

/// Best-effort diagnostics on stderr (`eprintln!` panics if stderr is closed).
pub fn log_error(line: std::fmt::Arguments) {
    let mut err = std::io::stderr().lock();
    let _ = err.write_fmt(line).and_then(|_| err.write_all(b"\n"));
}

/// A panic elsewhere must not turn every later command into an abort.
pub fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Interface state that must survive destroying and recreating the main WebView.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct UiState {
    pub range: HistoryRange,
    pub sort: String,
    pub query: String,
}
impl Default for UiState {
    fn default() -> Self {
        Self {
            range: HistoryRange::Today,
            sort: "total".into(),
            query: String::new(),
        }
    }
}

pub struct Shell {
    pub settings: Mutex<Settings>,
    settings_path: PathBuf,
    ui: Mutex<UiState>,
    /// identity_key → PNG data URL (None: no bundle icon, UI shows its fallback).
    icons: Mutex<HashMap<String, Option<String>>>,
    pub popup: Mutex<windows::PopupState>,
    /// Set only by `--validation-mode`; enables `validation_report`.
    pub validation: bool,
}
impl Shell {
    pub fn load(validation: bool) -> Self {
        VALIDATION.store(validation, Ordering::Relaxed);
        let settings_path = Settings::default_path();
        Self {
            settings: Mutex::new(Settings::load(&settings_path)),
            settings_path,
            ui: Mutex::new(UiState::default()),
            icons: Mutex::new(HashMap::new()),
            popup: Mutex::new(windows::PopupState::default()),
            validation,
        }
    }
    pub fn settings(&self) -> Settings {
        lock(&self.settings).clone()
    }
}

fn now_utc() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

pub fn apply_appearance(app: &AppHandle, appearance: Appearance) {
    app.set_theme(match appearance {
        Appearance::System => None,
        Appearance::Light => Some(tauri::Theme::Light),
        Appearance::Dark => Some(tauri::Theme::Dark),
    });
}

/// Applies persisted settings at startup (appearance + retention).
pub fn apply_startup_settings(app: &AppHandle) {
    let settings = app.state::<Shell>().settings();
    apply_appearance(app, settings.appearance);
    let history = lock(&app.state::<Mutex<Monitor>>()).history.clone();
    let mut store = lock(&history);
    if store.retention_days != settings.retention_days {
        let _ = store.set_retention_days(settings.retention_days, now_utc());
    }
}

#[tauri::command]
pub fn live_rates(monitor: State<'_, Mutex<Monitor>>) -> LiveRates {
    let monitor = lock(&monitor);
    let snapshot = lock(&monitor.snapshot);
    snapshot.live_rates()
}

#[tauri::command]
pub fn get_history(
    range: HistoryRange,
    monitor: State<'_, Mutex<Monitor>>,
) -> Result<HistoryView, String> {
    let monitor = lock(&monitor);
    let mut store = lock(&monitor.history);
    store
        .get_history(range, now_utc())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn clear_history(app: AppHandle, monitor: State<'_, Mutex<Monitor>>) -> Result<(), String> {
    {
        let monitor = lock(&monitor);
        let mut store = lock(&monitor.history);
        store.clear().map_err(|e| e.to_string())?;
    }
    let _ = app.emit("history-changed", ());
    Ok(())
}

#[tauri::command]
pub fn get_settings(shell: State<'_, Shell>) -> Settings {
    shell.settings()
}

#[tauri::command]
pub fn set_settings(settings: Settings, app: AppHandle) -> Result<Settings, String> {
    update_settings(&app, settings)
}

pub fn update_settings(app: &AppHandle, settings: Settings) -> Result<Settings, String> {
    let shell = app.state::<Shell>();
    let monitor = app.state::<Mutex<Monitor>>();
    let settings = settings.normalized();
    let previous = std::mem::replace(&mut *lock(&shell.settings), settings.clone());
    settings
        .save(&shell.settings_path)
        .map_err(|e| format!("Could not save settings: {e}"))?;
    if previous.appearance != settings.appearance {
        apply_appearance(app, settings.appearance);
    }
    if previous.retention_days != settings.retention_days {
        let monitor = lock(&monitor);
        let mut store = lock(&monitor.history);
        store
            .set_retention_days(settings.retention_days, now_utc())
            .map_err(|e| e.to_string())?;
    }
    if previous.units != settings.units {
        tray::refresh_title(app);
    }
    let _ = app.emit("settings-changed", &settings);
    Ok(settings)
}

#[tauri::command]
pub fn get_ui_state(shell: State<'_, Shell>) -> UiState {
    lock(&shell.ui).clone()
}

#[tauri::command]
pub fn set_ui_state(state: UiState, shell: State<'_, Shell>) {
    *lock(&shell.ui) = state;
}

#[derive(Debug, Deserialize)]
pub struct IconRequest {
    identity_key: String,
    bundle_id: Option<String>,
    icon_path: Option<String>,
    executable_path: Option<String>,
}

const ICON_PIXELS: isize = 36; // 18 pt at 2x; the 16 pt tray rows scale it down.
const ICON_CACHE_LIMIT: usize = 512;

/// Synchronous so Tauri runs it on the main thread, as AppKit drawing requires.
#[tauri::command]
pub fn app_icons(
    requests: Vec<IconRequest>,
    shell: State<'_, Shell>,
) -> HashMap<String, Option<String>> {
    let mut cache = lock(&shell.icons);
    let mut result = HashMap::new();
    for request in requests {
        if let Some(hit) = cache.get(&request.identity_key) {
            result.insert(request.identity_key, hit.clone());
            continue;
        }
        let icon = resolve_icon(&request);
        if cache.len() >= ICON_CACHE_LIMIT {
            cache.clear();
        }
        cache.insert(request.identity_key.clone(), icon.clone());
        result.insert(request.identity_key, icon);
    }
    result
}

fn resolve_icon(request: &IconRequest) -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        use base64::Engine;
        let bundle = bandpeek_core::app_icon::bundle_for(
            request.executable_path.as_deref(),
            request.icon_path.as_deref(),
        )
        .filter(|p| p.is_dir())
        .or_else(|| {
            // Only bundle identities may be looked up by ID; exec:/proc: rows keep the fallback.
            request
                .identity_key
                .starts_with("bundle:")
                .then_some(request.bundle_id.as_deref())
                .flatten()
                .and_then(macos::application_for_bundle_id)
        })?;
        let png = macos::bundle_icon_png(&bundle, ICON_PIXELS)?;
        Some(format!(
            "data:image/png;base64,{}",
            base64::engine::general_purpose::STANDARD.encode(png)
        ))
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = request;
        None
    }
}

#[tauri::command]
pub fn open_main_window(app: AppHandle) {
    windows::close_popup(&app);
    windows::open_main(&app);
}

#[tauri::command]
pub fn close_tray_popup(app: AppHandle) {
    windows::close_popup(&app);
}

#[tauri::command]
pub fn tray_popup_ready(height: f64, app: AppHandle) {
    windows::show_popup(&app, height);
}

#[tauri::command]
pub fn quit_app(app: AppHandle) {
    app.exit(0);
}

/// Validation harness channel: pages report what they rendered. Inert unless
/// the app was started with `--validation-mode`.
#[tauri::command]
pub fn validation_report(text: String, shell: State<'_, Shell>) {
    if shell.validation {
        validation_line(format_args!(
            "validation-report={}",
            text.replace('\n', " ")
        ));
    }
}
