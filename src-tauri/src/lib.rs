pub mod aggregate;
pub mod app_icon;
pub mod collectors;
pub mod db;
pub mod diagnostics;
pub mod model;
pub mod presentation;
pub mod settings;
pub mod update_policy;
#[cfg(feature = "release-tools")]
pub mod update_signature;
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
    interval: u64,
    record: bool,
    paused_at: Option<i64>,
    live_subscribers: collectors::LiveSubscribers,
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
    /// Validation seam; the desktop always uses start() and the 2-second default.
    pub fn start_with_interval(interval: u64) -> Self {
        let store =
            db::HistoryStore::open_default().expect("Failed to initialize BandPeek SQLite storage");
        Self::start_with_store(interval, Arc::new(Mutex::new(store)))
    }
    pub fn start_with_store(interval: u64, history: Arc<Mutex<db::HistoryStore>>) -> Self {
        Self::spawn(interval, history, true)
    }
    /// Validation only (README screenshots from a demo database): live rates
    /// run as usual, but observed traffic is not written to history.
    pub fn start_without_recording(interval: u64) -> Self {
        let store =
            db::HistoryStore::open_default().expect("Failed to initialize BandPeek SQLite storage");
        Self::spawn(interval, Arc::new(Mutex::new(store)), false)
    }
    fn spawn(interval: u64, history: Arc<Mutex<db::HistoryStore>>, record: bool) -> Self {
        assert!((1..=60).contains(&interval));
        let snapshot = Arc::new(Mutex::new(model::Snapshot::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let live_subscribers = Arc::new(Mutex::new(Vec::new()));
        let worker = Self::worker(
            interval,
            record,
            &snapshot,
            &stop,
            &history,
            &live_subscribers,
        );
        Self {
            snapshot,
            history,
            stop,
            worker: Some(worker),
            interval,
            record,
            paused_at: None,
            live_subscribers,
        }
    }
    fn worker(
        interval: u64,
        record: bool,
        snapshot: &SharedSnapshot,
        stop: &Arc<AtomicBool>,
        history: &Arc<Mutex<db::HistoryStore>>,
        subscribers: &collectors::LiveSubscribers,
    ) -> std::thread::JoinHandle<()> {
        let output = snapshot.clone();
        let signal = stop.clone();
        let hist_clone = history.clone();
        let live_subscribers = subscribers.clone();
        std::thread::spawn(move || {
            #[cfg(target_os = "macos")]
            collectors::macos::MacosCollector {
                interval,
                history: record.then_some(hist_clone),
                live_subscribers,
            }
            .run(output, signal);
            #[cfg(not(target_os = "macos"))]
            {
                let _ = signal;
                let _ = (hist_clone, record, live_subscribers);
                output.lock().unwrap().status = "Unsupported platform: macOS milestone only".into();
            }
        })
    }
    /// Receives one update per publication, including gaps with unchanged sequence.
    /// The collector never waits for the UI; disconnected subscribers are removed.
    pub fn subscribe_live(&self) -> std::sync::mpsc::Receiver<model::LiveRates> {
        let (sender, receiver) = std::sync::mpsc::channel();
        self.live_subscribers
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .push(sender);
        receiver
    }
    /// Stop collection, reap nettop, then durably flush. Updates must inspect
    /// the result; a failed flush cancels installation.
    pub fn stop_checked(&mut self) -> Result<(), String> {
        if self.worker.is_some() {
            self.paused_at = Some(Self::now_utc());
        }
        self.stop.store(true, Ordering::Relaxed);
        if let Some(worker) = self.worker.take() {
            worker
                .join()
                .map_err(|_| "Collector worker panicked".to_string())?;
        }
        self.history
            .lock()
            .map_err(|_| "History lock poisoned".to_string())?
            .flush()
            .map_err(|e| format!("History flush failed: {e}"))
    }
    fn now_utc() -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64
    }
    pub fn resume(&mut self) {
        if self.worker.is_none() {
            if self.record {
                if let Some(start) = self.paused_at.take() {
                    let generation = self
                        .snapshot
                        .lock()
                        .map(|s| s.collector_generation + 1)
                        .unwrap_or(0);
                    if let Ok(mut history) = self.history.lock() {
                        let _ = history.record_gap(
                            start,
                            Self::now_utc(),
                            generation,
                            "Update preparation paused collector",
                        );
                    }
                }
            }
            self.stop = Arc::new(AtomicBool::new(false));
            self.worker = Some(Self::worker(
                self.interval,
                self.record,
                &self.snapshot,
                &self.stop,
                &self.history,
                &self.live_subscribers,
            ));
        }
    }
    pub fn stop(&mut self) {
        let _ = self.stop_checked();
    }
}
impl Drop for Monitor {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod monitor_update_tests {
    use super::*;
    use crate::model::{AppDelta, HistoryRange};
    #[test]
    fn update_shutdown_flushes_pending_sqlite_after_joining_worker() {
        let path =
            std::env::temp_dir().join(format!("bandpeek-update-flush-{}.db", std::process::id()));
        let mut store = db::HistoryStore::open(&path).unwrap();
        let now = 1790616469;
        let delta = |bytes| AppDelta {
            identity_key: "exec:/usr/bin/curl".into(),
            application_name: "curl".into(),
            bundle_id: None,
            icon_path: None,
            executable_path: None,
            download_bytes: bytes,
            upload_bytes: 0,
        };
        store.record_deltas(now, &[delta(1)]).unwrap(); // first auto flush
        store.record_deltas(now + 1, &[delta(777)]).unwrap(); // buffered within same minute
        let history = Arc::new(Mutex::new(store));
        let signal = Arc::new(AtomicBool::new(false));
        let worker_signal = signal.clone();
        let worker = std::thread::spawn(move || {
            while !worker_signal.load(Ordering::Relaxed) {
                std::thread::yield_now();
            }
        });
        let mut monitor = Monitor {
            snapshot: Arc::new(Mutex::new(model::Snapshot::default())),
            history,
            stop: signal,
            worker: Some(worker),
            interval: 5,
            live_subscribers: Arc::new(Mutex::new(Vec::new())),
            record: true,
            paused_at: None,
        };
        monitor.stop_checked().unwrap();
        assert!(monitor.worker.is_none());
        // Independent connection sees the previously buffered observations.
        let mut reopened = db::HistoryStore::open(&path).unwrap();
        assert_eq!(
            reopened
                .get_history(HistoryRange::Today, now + 2)
                .unwrap()
                .summary
                .download_bytes,
            778
        );
        drop(reopened);
        drop(monitor);
        let _ = std::fs::remove_file(path);
    }
}
