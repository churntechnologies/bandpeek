pub mod aggregate;
pub mod app_icon;
pub mod collectors;
pub mod db;
pub mod model;
pub mod settings;
use collectors::{Collector, SharedSnapshot};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};

pub struct Monitor {
    pub snapshot: SharedSnapshot,
    pub history: Arc<Mutex<db::HistoryStore>>,
    stop: Arc<AtomicBool>,
    worker: Option<std::thread::JoinHandle<()>>,
}
impl Default for Monitor {
    fn default() -> Self {
        Self::start()
    }
}
impl Monitor {
    pub fn start() -> Self {
        Self::start_with_interval(model::SAMPLE_INTERVAL_SECONDS)
    }
    /// Validation seam; the desktop always uses start() and the 5-second default.
    pub fn start_with_interval(interval: u64) -> Self {
        let store =
            db::HistoryStore::open_default().expect("Failed to initialize BandPeek SQLite storage");
        Self::start_with_store(interval, Arc::new(Mutex::new(store)))
    }
    pub fn start_with_store(interval: u64, history: Arc<Mutex<db::HistoryStore>>) -> Self {
        assert!((1..=60).contains(&interval));
        let snapshot = Arc::new(Mutex::new(model::Snapshot::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let output = snapshot.clone();
        let signal = stop.clone();
        let hist_clone = history.clone();
        let worker = std::thread::spawn(move || {
            #[cfg(target_os = "macos")]
            collectors::macos::MacosCollector {
                interval,
                history: Some(hist_clone),
            }
            .run(output, signal);
            #[cfg(not(target_os = "macos"))]
            {
                let _ = signal;
                let _ = hist_clone;
                output.lock().unwrap().status = "Unsupported platform: macOS milestone only".into();
            }
        });
        Self {
            snapshot,
            history,
            stop,
            worker: Some(worker),
        }
    }
    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        if let Ok(mut store) = self.history.lock() {
            let _ = store.flush();
        }
    }
}
impl Drop for Monitor {
    fn drop(&mut self) {
        self.stop();
    }
}
