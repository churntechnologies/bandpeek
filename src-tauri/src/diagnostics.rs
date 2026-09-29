//! Opt-in validation evidence. Normal launches do no serialization or file I/O.
use std::{
    fs::File,
    io::Write,
    sync::{Mutex, OnceLock},
};
static TRACE: OnceLock<Option<Mutex<File>>> = OnceLock::new();
pub fn enabled() -> bool {
    TRACE
        .get_or_init(|| {
            std::env::var_os("BANDPEEK_LIVE_TRACE_PATH")
                .and_then(|p| {
                    std::fs::OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(p)
                        .ok()
                })
                .map(Mutex::new)
        })
        .is_some()
}
pub fn trace(mut value: serde_json::Value) {
    if !enabled() {
        return;
    }
    value["at_unix_ms"] = serde_json::json!(std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64);
    if let Some(file) = TRACE.get().and_then(Option::as_ref) {
        let mut file = file.lock().unwrap_or_else(|p| p.into_inner());
        let _ = writeln!(file, "{value}");
    }
}
