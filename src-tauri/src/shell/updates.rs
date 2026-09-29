//! Official Tauri 2 updater, coordinated independently of WebViews.
//! No updater commands are exposed to JavaScript. No telemetry is sent.
use super::lock;
use bandpeek_core::{
    app_icon::outer_bundle,
    settings::Settings,
    update_policy::{self, InstallHost, Phase, Policy},
    Monitor,
};
use serde::Deserialize;
use std::{
    path::{Path, PathBuf},
    sync::{mpsc, Mutex},
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

const STARTUP_DELAY: Duration = Duration::from_secs(30);
const CHECK_INTERVAL: Duration = Duration::from_secs(6 * 60 * 60);
const RELEASES: &str =
    "https://api.github.com/repos/churntechnologies/bandpeek/releases?per_page=100";
const ASSET_PREFIX: &str = "https://github.com/churntechnologies/bandpeek/releases/download/";

#[derive(Default)]
pub struct Updates {
    policy: Mutex<Policy>,
    wake: Mutex<Option<mpsc::Sender<Wake>>>,
}

#[derive(Clone, Copy)]
enum Wake {
    WindowClosed,
    CheckNow,
}

// An owner-supplied HTTPS fixture can exercise the complete signed install
// before a private repository becomes public. Never used in normal launches;
// the embedded public key and every official verification check still apply.
fn fixture_endpoint(app: &AppHandle) -> Result<Option<String>, String> {
    if !app.state::<super::Shell>().validation {
        return Ok(None);
    }
    let Some(url) = crate::arg_value("--validation-update-endpoint") else {
        return Ok(None);
    };
    let parsed = reqwest::Url::parse(&url).map_err(|e| e.to_string())?;
    if parsed.scheme() != "https" {
        return Err("Update fixtures must use HTTPS with valid certificates".into());
    }
    Ok(Some(url))
}

pub fn validation_check_now(app: &AppHandle) {
    if !app.state::<super::Shell>().validation {
        return;
    }
    if let Some(sender) = &*lock(&app.state::<Updates>().wake) {
        let _ = sender.send(Wake::CheckNow);
    }
}
pub fn validation_state(app: &AppHandle) {
    if app.state::<super::Shell>().validation {
        super::validation_line(format_args!(
            "validation-update-phase={:?}",
            lock(&app.state::<Updates>().policy).phase
        ));
    }
}

fn marker() -> PathBuf {
    Settings::default_path().with_file_name("update-relaunch")
}
pub fn consume_relaunch_marker() -> bool {
    let path = marker();
    path.exists() && std::fs::remove_file(path).is_ok()
}

pub fn open_main(app: &AppHandle) -> bool {
    lock(&app.state::<Updates>().policy).open_main()
}
pub fn close_main(app: &AppHandle) {
    let state = app.state::<Updates>();
    lock(&state.policy).close_main();
    if let Some(sender) = &*lock(&state.wake) {
        let _ = sender.send(Wake::WindowClosed);
    };
}

// Diagnostics are local, bounded, contain no observations or settings. Errors
// can include local install paths; never attach this log to public issues raw.
fn log(message: &str) {
    use std::io::Write;
    super::log_error(format_args!("BandPeek updater: {message}"));
    let path = Settings::default_path().with_file_name("updates.log");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if std::fs::metadata(&path).is_ok_and(|m| m.len() > 1_048_576) {
        let _ = std::fs::rename(&path, path.with_extension("log.previous"));
    }
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = writeln!(file, "{} {message}", super::now_utc());
    }
}

pub fn start(app: &AppHandle) {
    let key = app
        .config()
        .plugins
        .0
        .get("updater")
        .and_then(|v| v.get("pubkey"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if key.trim().is_empty() {
        log("inactive: owner must embed the updater public key before release");
        return;
    }
    if !cfg!(all(target_os = "macos", target_arch = "aarch64"))
        || std::env::current_exe()
            .ok()
            .and_then(|p| outer_bundle(&p))
            .is_none()
    {
        log("inactive: only installed macOS Apple Silicon bundles support updates");
        return;
    }
    let (tx, rx) = mpsc::channel();
    *lock(&app.state::<Updates>().wake) = Some(tx);
    let handle = app.clone();
    std::thread::Builder::new().name("bandpeek-updater".into()).spawn(move || {
        let mut deadline = Instant::now() + STARTUP_DELAY;
        let mut pending: Option<(Update, Vec<u8>)> = None;
        loop {
            if matches!(rx.recv_timeout(deadline.saturating_duration_since(Instant::now())), Ok(Wake::CheckNow)) {
                deadline = Instant::now();
            }
            if Instant::now() >= deadline {
                deadline = Instant::now() + CHECK_INTERVAL;
                if pending.is_none() && lock(&handle.state::<Updates>().policy).begin_check() {
                    log("checking GitHub Releases");
                    match tauri::async_runtime::block_on(check_and_download(&handle)) {
                        Ok(Some(update)) => {
                            pending = Some(update);
                            lock(&handle.state::<Updates>().policy).phase = Phase::Ready;
                            log("download verified; waiting until the main window is closed");
                        }
                        Ok(None) => {
                            lock(&handle.state::<Updates>().policy).phase = Phase::Idle;
                            log("no newer release");
                        }
                        Err(error) => {
                            lock(&handle.state::<Updates>().policy).phase = Phase::Failed;
                            log(&format!("check/download failed; retry at next scheduled check: {error}"));
                        }
                    }
                }
            }
            // Reservation runs on the UI thread, so open/close cannot race it.
            if pending.is_some() && reserve_install(&handle) {
                let (update, bytes) = pending.take().expect("pending verified update");
                log("install reserved; stopping collector and flushing SQLite");
                let mut host = NativeHost { app: &handle, update, bytes };
                if let Err(error) = update_policy::install_and_relaunch(&mut host) {
                    lock(&handle.state::<Updates>().policy).phase = Phase::Failed;
                    log(&format!("install failed; current version resumed; retry at next scheduled check: {error}"));
                } else {
                    return; // Tauri's exit/restart callback now owns shutdown.
                }
            }
        }
    }).expect("updater worker");
}

fn reserve_install(app: &AppHandle) -> bool {
    let (tx, rx) = mpsc::channel();
    let inner = app.clone();
    if app
        .run_on_main_thread(move || {
            let state = inner.state::<Updates>();
            let allowed = inner.get_webview(super::windows::MAIN).is_none()
                && lock(&state.policy).begin_install();
            let _ = tx.send(allowed);
        })
        .is_err()
    {
        return false;
    }
    rx.recv().unwrap_or(false)
}

#[derive(Deserialize)]
struct Asset {
    name: String,
    browser_download_url: String,
}
#[derive(Deserialize)]
struct Release {
    tag_name: String,
    draft: bool,
    assets: Vec<Asset>,
}
fn manifest_url(releases: Vec<Release>) -> Option<String> {
    // GitHub /releases/latest excludes prereleases. Discover published beta
    // tags through /releases, then let Tauri fetch/parse latest.json and enforce
    // its normal newer-version comparison and cryptographic verification.
    releases
        .into_iter()
        .filter(|r| !r.draft)
        .filter_map(|r| {
            let version = semver::Version::parse(r.tag_name.strip_prefix('v')?).ok()?;
            let asset = r.assets.into_iter().find(|a| {
                a.name == "latest.json" && a.browser_download_url.starts_with(ASSET_PREFIX)
            })?;
            Some((version, asset.browser_download_url))
        })
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, url)| url)
}
async fn check_and_download(app: &AppHandle) -> Result<Option<(Update, Vec<u8>)>, String> {
    if rustls::crypto::CryptoProvider::get_default().is_none() {
        let _ = rustls::crypto::ring::default_provider().install_default();
    }
    let url = if let Some(url) = fixture_endpoint(app)? {
        url
    } else {
        let client = reqwest::Client::builder()
            .user_agent("BandPeek updater")
            .timeout(Duration::from_secs(60))
            .build()
            .map_err(|e| e.to_string())?;
        let releases: Vec<Release> = client
            .get(RELEASES)
            .send()
            .await
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        let Some(url) = manifest_url(releases) else {
            return Ok(None);
        };
        url
    };
    let updater = app
        .updater_builder()
        .endpoints(vec![url
            .parse()
            .map_err(|e| format!("Invalid manifest URL: {e}"))?])
        .map_err(|e| e.to_string())?
        .timeout(Duration::from_secs(10 * 60))
        .build()
        .map_err(|e| e.to_string())?;
    let Some(update) = updater.check().await.map_err(|e| e.to_string())? else {
        return Ok(None);
    };
    lock(&app.state::<Updates>().policy).phase = Phase::Downloading;
    log(&format!("downloading {}", update.version));
    // Official download() verifies the signature before returning any bytes.
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    Ok(Some((update, bytes)))
}

struct NativeHost<'a> {
    app: &'a AppHandle,
    update: Update,
    bytes: Vec<u8>,
}
impl InstallHost for NativeHost<'_> {
    fn quiesce_and_flush(&mut self) -> Result<(), String> {
        lock(&self.app.state::<Mutex<Monitor>>()).stop_checked()
    }
    fn install_verified(&mut self) -> Result<(), String> {
        // Write restart intent before replacement; failure cancels installation.
        std::fs::write(marker(), b"tray").map_err(|e| e.to_string())?;
        install_with_backup(&self.update, &self.bytes)
    }
    fn resume_current(&mut self) {
        let _ = std::fs::remove_file(marker());
        lock(&self.app.state::<Mutex<Monitor>>()).resume();
    }
    fn relaunch(&mut self) -> Result<(), String> {
        log("installed; SQLite flushed and collector reaped; requesting tray-only relaunch");
        self.app.request_restart(); // fires the normal Exit callback
        Ok(())
    }
}

/// Tauri's macOS installer moves the current bundle out before the last rename.
/// Keep an independent same-volume copy so a rename/extraction failure cannot
/// delete the working installation when its temporary directory is dropped.
fn install_with_backup(update: &Update, bytes: &[u8]) -> Result<(), String> {
    let path = std::env::current_exe()
        .ok()
        .and_then(|p| outer_bundle(&p))
        .ok_or("Updater requires an installed application bundle")?;
    let parent = path.parent().ok_or("App bundle has no parent")?;
    check_writable_owned_bundle(&path, parent)?;
    let backup = parent.join(format!(
        ".BandPeek-update-backup-{}.app",
        std::process::id()
    ));
    if backup.exists() {
        return Err("An update backup already exists; installation cancelled".into());
    }
    let result = std::process::Command::new("/usr/bin/ditto")
        .arg(&path)
        .arg(&backup)
        .status();
    if !result.is_ok_and(|s| s.success()) {
        let _ = std::fs::remove_dir_all(&backup);
        return Err("Could not back up current application; installation cancelled".into());
    }
    match update.install(bytes) {
        Ok(()) => {
            if let Err(e) = std::fs::remove_dir_all(backup) {
                log(&format!("backup cleanup failed: {e}"));
            }
            Ok(())
        }
        Err(error) => {
            restore_backup(&path, &backup).map_err(|e| {
                format!("CRITICAL: install failed ({error}); rollback failed ({e})")
            })?;
            Err(error.to_string())
        }
    }
}
fn check_writable_owned_bundle(path: &Path, parent: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::{ffi::OsStrExt, fs::MetadataExt};
        let owner = std::fs::metadata(path).map_err(|e| e.to_string())?.uid();
        let dir =
            std::ffi::CString::new(parent.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
        if owner != unsafe { libc::geteuid() }
            || unsafe { libc::access(dir.as_ptr(), libc::W_OK) } != 0
        {
            return Err(
                "Application is not user-owned/writable; silent update deferred (no elevation)"
                    .into(),
            );
        }
    }
    Ok(())
}
fn restore_backup(path: &Path, backup: &Path) -> std::io::Result<()> {
    let failed = backup.with_extension("failed");
    if path.exists() {
        std::fs::rename(path, &failed)?;
    }
    std::fs::rename(backup, path)?;
    if failed.exists() {
        let _ = std::fs::remove_dir_all(failed);
    }
    Ok(())
}

/// Exercise exactly the production quiesce/flush/restart path without replacing
/// any app files. Inert unless launched with --validation-mode. This is not an
/// artifact verification or installation test and does not bypass verification.
pub fn validation_relaunch(app: &AppHandle) {
    if !app.state::<super::Shell>().validation || app.get_webview(super::windows::MAIN).is_some() {
        return;
    }
    let result = lock(&app.state::<Mutex<Monitor>>()).stop_checked();
    match result {
        Ok(()) => {
            if std::fs::write(marker(), b"tray").is_ok() {
                super::validation_line(format_args!(
                    "validation-update=collector-stopped-database-flushed"
                ));
                app.request_restart();
            } else {
                lock(&app.state::<Mutex<Monitor>>()).resume();
            }
        }
        Err(e) => {
            log(&e);
            lock(&app.state::<Mutex<Monitor>>()).resume();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn beta_discovery_selects_highest_published_semver_and_ignores_drafts() {
        let release = |tag: &str, draft, url: &str| Release {
            tag_name: tag.into(),
            draft,
            assets: vec![Asset {
                name: "latest.json".into(),
                browser_download_url: url.into(),
            }],
        };
        let url = format!("{ASSET_PREFIX}v0.1.0-beta.2/latest.json");
        assert_eq!(
            manifest_url(vec![
                release(
                    "v0.1.0-beta.1",
                    false,
                    &format!("{ASSET_PREFIX}v0.1.0-beta.1/latest.json")
                ),
                release("v0.1.0-beta.2", false, &url),
                release("v9.0.0", true, &url),
                release("v8.0.0", false, "https://example.com/latest.json")
            ]),
            Some(url)
        );
    }
    #[test]
    fn rollback_restores_bundle_after_a_partial_install() {
        let dir = std::env::temp_dir().join(format!("bandpeek-rollback-{}", std::process::id()));
        let path = dir.join("BandPeek.app");
        let backup = dir.join("backup.app");
        std::fs::create_dir_all(&path).unwrap();
        std::fs::create_dir_all(&backup).unwrap();
        std::fs::write(path.join("binary"), b"partial update").unwrap();
        std::fs::write(backup.join("binary"), b"working version").unwrap();
        restore_backup(&path, &backup).unwrap();
        assert_eq!(
            std::fs::read(path.join("binary")).unwrap(),
            b"working version"
        );
        assert!(!backup.exists());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
