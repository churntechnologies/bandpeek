mod shell;

use bandpeek_core::Monitor;
use std::sync::Mutex;
use tauri::Manager;

fn arg_value(flag: &str) -> Option<String> {
    std::env::args().skip_while(|a| a != flag).nth(1)
}

fn main() {
    // Opt-in measurement/validation hooks. Normal launches ignore all of them.
    let validation_mode = arg_value("--validation-mode");
    let validation_log = std::env::args().any(|a| a == "--validation-log");
    let validation_seconds: Option<u64> = arg_value("--validation-seconds")
        .map(|s| s.parse().expect("validation duration in seconds"));
    let sequence_seconds: u64 = arg_value("--validation-sequence-seconds")
        .and_then(|s| s.parse().ok())
        .unwrap_or(90);
    let interval: u64 = arg_value("--interval")
        .and_then(|s| s.parse().ok())
        .unwrap_or(bandpeek_core::model::SAMPLE_INTERVAL_SECONDS);

    let app = tauri::Builder::default()
        .manage(Mutex::new(Monitor::start_with_interval(interval)))
        .manage(shell::Shell::load(validation_mode.is_some()))
        .setup(move |app| {
            let handle = app.handle();
            shell::apply_startup_settings(handle);
            shell::tray::install(handle)?;
            match validation_mode.as_deref() {
                None | Some("visible") | Some("sequence") => shell::windows::open_main(handle),
                Some("hidden") => {
                    shell::windows::open_main(handle);
                    if let Some(window) = app.get_webview_window(shell::windows::MAIN) {
                        window.hide()?;
                    }
                }
                Some("tray") => {
                    #[cfg(target_os = "macos")]
                    handle.set_activation_policy(tauri::ActivationPolicy::Accessory)?;
                }
                Some(_) => {
                    return Err("validation mode must be visible, hidden, tray, or sequence".into())
                }
            }
            if let Some(mode) = &validation_mode {
                shell::validation_line(format_args!(
                    "validation-state={}",
                    if mode == "sequence" { "visible" } else { mode }
                ));
                spawn_validation_controls(handle.clone(), validation_seconds);
                if mode == "sequence" {
                    // Visible → close through the production close path → tray-only.
                    let handle = handle.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_secs(sequence_seconds));
                        let inner = handle.clone();
                        let _ = handle.run_on_main_thread(move || {
                            if let Some(window) = inner.get_webview_window(shell::windows::MAIN) {
                                let _ = window.close();
                            }
                            shell::validation_line(format_args!("validation-state=tray"));
                        });
                    });
                }
            }
            if validation_log {
                let output = shell::lock(&app.state::<Mutex<Monitor>>()).snapshot.clone();
                std::thread::spawn(move || loop {
                    // Serialize under the lock, write after releasing it.
                    let line = serde_json::to_string(&*shell::lock(&output)).unwrap_or_default();
                    shell::validation_line(format_args!("{line}"));
                    std::thread::sleep(std::time::Duration::from_secs(1));
                });
            }
            Ok(())
        })
        .on_window_event(|window, event| match (window.label(), event) {
            (shell::windows::MAIN, tauri::WindowEvent::Destroyed) => {
                shell::windows::main_destroyed(window.app_handle())
            }
            (shell::windows::POPUP, tauri::WindowEvent::Focused(focused)) => {
                shell::windows::popup_focus_changed(window.app_handle(), *focused)
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            shell::live_rates,
            shell::get_history,
            shell::clear_history,
            shell::get_settings,
            shell::set_settings,
            shell::get_ui_state,
            shell::set_ui_state,
            shell::app_icons,
            shell::open_main_window,
            shell::close_tray_popup,
            shell::tray_popup_ready,
            shell::quit_app,
            shell::validation_report,
        ])
        .build(tauri::generate_context!())
        .expect("Could not start BandPeek");

    app.run(|app, event| match event {
        // Closing the last window only returns BandPeek to the menu bar. Only an
        // explicit exit (tray Quit, app menu Quit) carries an exit code.
        tauri::RunEvent::ExitRequested {
            api, code: None, ..
        } => api.prevent_exit(),
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => shell::windows::open_main(app),
        tauri::RunEvent::Exit => {
            shell::lock(&app.state::<Mutex<Monitor>>()).stop();
            shell::validation_line(format_args!("validation-state=exited"));
        }
        _ => {}
    });
}

/// Validation only: timed exit, plus line commands on stdin (`open`, `close`,
/// `quit`) so harnesses drive the same code paths as the tray and window.
fn spawn_validation_controls(handle: tauri::AppHandle, seconds: Option<u64>) {
    let snapshot_dir = std::path::PathBuf::from(
        arg_value("--validation-dir").unwrap_or_else(|| ".validation/milestone4".into()),
    );
    if let Some(seconds) = seconds {
        let handle = handle.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_secs(seconds));
            handle.exit(0);
        });
    }
    std::thread::spawn(move || {
        for line in std::io::stdin().lines().map_while(Result::ok) {
            let inner = handle.clone();
            match line.trim() {
                "open" => {
                    let _ = handle.run_on_main_thread(move || shell::windows::open_main(&inner));
                }
                "close" => {
                    let _ = handle.run_on_main_thread(move || {
                        if let Some(window) = inner.get_webview_window(shell::windows::MAIN) {
                            let _ = window.close();
                        }
                    });
                }
                "quit" => handle.exit(0),
                command => {
                    let (verb, argument) = command.split_once(' ').unwrap_or((command, ""));
                    let (verb, argument) = (verb.to_string(), argument.trim().to_string());
                    let dir = snapshot_dir.clone();
                    let _ = handle.run_on_main_thread(move || match verb.as_str() {
                        #[cfg(target_os = "macos")]
                        "snapshot" => shell::validation::snapshot(&inner, &dir, &argument),
                        #[cfg(target_os = "macos")]
                        "resize" => shell::validation::resize(&inner, &argument),
                        #[cfg(target_os = "macos")]
                        "geometry" => shell::validation::geometry(&inner, &argument),
                        #[cfg(target_os = "macos")]
                        "popup" => shell::validation::open_popup(&inner),
                        "popup-close" => shell::windows::close_popup(&inner),
                        // `eval <label> <js>`: the script reports back via document.title.
                        "eval" => {
                            let (label, script) = argument.split_once(' ').unwrap_or(("main", ""));
                            if let Some(window) = inner.get_webview_window(label) {
                                let _ = window.eval(script);
                            }
                        }
                        "title" => {
                            if let Some(window) = inner.get_webview_window(&argument) {
                                shell::validation_line(format_args!(
                                    "validation-title={}",
                                    window.title().unwrap_or_default()
                                ));
                            }
                        }
                        "appearance" | "units" | "retention" => {
                            let mut settings = inner.state::<shell::Shell>().settings();
                            let value = serde_json::Value::String(argument.clone());
                            match verb.as_str() {
                                "appearance" => {
                                    settings.appearance =
                                        serde_json::from_value(value).unwrap_or_default()
                                }
                                "units" => {
                                    settings.units =
                                        serde_json::from_value(value).unwrap_or_default()
                                }
                                _ => settings.retention_days = argument.parse().unwrap_or(0),
                            }
                            match shell::update_settings(&inner, settings) {
                                Ok(s) => shell::validation_line(format_args!(
                                    "validation-settings={}",
                                    serde_json::to_string(&s).unwrap()
                                )),
                                Err(e) => shell::log_error(format_args!("{e}")),
                            }
                        }
                        _ => shell::log_error(format_args!("unknown validation command: {verb}")),
                    });
                }
            }
        }
    });
}
