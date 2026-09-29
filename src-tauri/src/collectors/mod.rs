use crate::model::Snapshot;
use std::sync::{atomic::AtomicBool, Arc, Mutex};

pub type SharedSnapshot = Arc<Mutex<Snapshot>>;
pub type SharedHistory = Arc<Mutex<crate::db::HistoryStore>>;

/// A collector owns its worker/subprocess until run returns; Stop must be bounded.
/// Future platform collectors implement this boundary, not a fabricated data source.
pub trait Collector: Send + 'static {
    fn run(self, output: SharedSnapshot, stop: Arc<AtomicBool>);
}
#[cfg(target_os = "macos")]
pub mod macos;
