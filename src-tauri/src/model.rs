use serde::{Deserialize, Serialize};
pub const SAMPLE_INTERVAL_SECONDS: u64 = 2;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum HistoryRange {
    Today,
    Yesterday,
    // snake_case alone would produce "last7_days"; the UI sends "last_7_days".
    #[serde(rename = "last_7_days")]
    Last7Days,
    #[serde(rename = "last_30_days")]
    Last30Days,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct AppHistoryRow {
    pub identity_key: String,
    /// Name as stored with the identity (unchanged history data).
    pub application_name: String,
    /// Human-facing label derived at read time (`presentation::present`).
    pub display_name: String,
    pub kind: crate::presentation::AppKind,
    pub bundle_id: Option<String>,
    pub icon_path: Option<String>,
    pub executable_path: Option<String>,
    pub download_bytes: u64,
    pub upload_bytes: u64,
    pub total_bytes: u64,
    pub share_percent: f64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct HistorySummary {
    pub download_bytes: u64,
    pub upload_bytes: u64,
    pub total_bytes: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct HistoryView {
    pub range: HistoryRange,
    pub range_start_utc: i64,
    pub range_end_utc: i64,
    pub summary: HistorySummary,
    pub rows: Vec<AppHistoryRow>,
}

/// Collector condition as presented in the status bar.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TrackingState {
    Starting,
    Tracking,
    Gap,
    Stopped,
}

/// Small per-refresh payload for header/tray speeds. The full process table in
/// `Snapshot` is deliberately not serialized to the UI every refresh.
#[derive(Clone, Debug, Serialize)]
pub struct LiveRates {
    pub download_bytes_per_second: f64,
    pub upload_bytes_per_second: f64,
    pub state: TrackingState,
    pub sample_sequence: u64,
    pub collector_generation: u64,
}

impl Snapshot {
    pub fn tracking_state(&self) -> TrackingState {
        let status = self.status.as_str();
        if status.starts_with("Tracking") {
            TrackingState::Tracking
        } else if status.starts_with("Stopped") {
            TrackingState::Stopped
        } else if status.starts_with("Collector gap") || status.starts_with("Malformed") {
            TrackingState::Gap
        } else {
            TrackingState::Starting
        }
    }

    pub fn live_rates(&self) -> LiveRates {
        LiveRates {
            download_bytes_per_second: self.download_bytes_per_second,
            upload_bytes_per_second: self.upload_bytes_per_second,
            state: self.tracking_state(),
            sample_sequence: self.sample_sequence,
            collector_generation: self.collector_generation,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct AppDelta {
    pub identity_key: String,
    pub application_name: String,
    pub bundle_id: Option<String>,
    pub icon_path: Option<String>,
    pub executable_path: Option<String>,
    pub download_bytes: u64,
    pub upload_bytes: u64,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq, Serialize)]
pub struct ProcessId {
    pub pid: i32,
    pub start_us: u64,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct AppIdentity {
    pub application_name: Option<String>,
    pub bundle_id: Option<String>,
    pub icon_path: Option<String>,
    pub executable_path: Option<String>,
}

impl AppIdentity {
    pub fn stable_key(&self, fallback_process_name: &str) -> String {
        if let Some(ref bundle_id) = self.bundle_id {
            let trimmed = bundle_id.trim();
            if !trimmed.is_empty() {
                return format!("bundle:{trimmed}");
            }
        }
        if let Some(ref exec) = self.executable_path {
            let trimmed = exec.trim();
            if !trimmed.is_empty() {
                return format!("exec:{trimmed}");
            }
        }
        format!("proc:{}", fallback_process_name.trim())
    }

    /// Bundle name, else the executable's file name (nettop truncates process
    /// names to 15 characters, e.g. "com.apple.WebKi"), else the process name.
    pub fn display_name(&self, fallback_process_name: &str) -> String {
        self.application_name
            .clone()
            .or_else(|| {
                self.executable_path
                    .as_deref()
                    .and_then(|p| std::path::Path::new(p).file_name())
                    .map(|n| n.to_string_lossy().into_owned())
                    .filter(|n| !n.trim().is_empty())
            })
            .unwrap_or_else(|| fallback_process_name.to_string())
    }
}

// OS counters and session deltas deliberately have different types.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub struct OsCounters {
    pub download: u64,
    pub upload: u64,
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
pub struct SessionBytes {
    pub download: u64,
    pub upload: u64,
}

#[derive(Clone, Copy, Debug, Serialize)]
pub enum NetworkScope {
    All,
    Wifi,
    Ethernet,
}

#[derive(Clone, Debug, Serialize)]
pub struct Observation {
    pub id: ProcessId,
    pub process_name: String,
    pub app: AppIdentity,
    pub counters: OsCounters,
}

#[derive(Clone, Debug, Serialize)]
pub struct ProcessRow {
    pub id: ProcessId,
    pub process_name: String,
    pub app: AppIdentity,
    pub bytes: SessionBytes,
    pub total_bytes: u64,
    pub active: bool,
    pub os_counters: OsCounters,
    pub last_seen_ms: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct Snapshot {
    pub status: String,
    pub sampling_interval_seconds: u64,
    pub scope: NetworkScope,
    pub collector_pid: Option<u32>,
    pub collector_generation: u64,
    pub sample_sequence: u64,
    pub sample_elapsed_ms: u64,
    pub download_bytes_per_second: f64,
    pub upload_bytes_per_second: f64,
    pub session_bytes: SessionBytes,
    pub archived_bytes: SessionBytes,
    pub rejected_samples: u64,
    pub unresolved_rows: u64,
    pub counter_resets: u64,
    pub rows: Vec<ProcessRow>,
}
impl Default for Snapshot {
    fn default() -> Self {
        Self {
            status: "Starting collector; waiting for baseline".into(),
            sampling_interval_seconds: SAMPLE_INTERVAL_SECONDS,
            scope: NetworkScope::All,
            collector_pid: None,
            collector_generation: 0,
            sample_sequence: 0,
            sample_elapsed_ms: 0,
            download_bytes_per_second: 0.0,
            upload_bytes_per_second: 0.0,
            session_bytes: SessionBytes::default(),
            archived_bytes: SessionBytes::default(),
            rejected_samples: 0,
            unresolved_rows: 0,
            counter_resets: 0,
            rows: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn history_range_matches_ui_names() {
        for (range, name) in [
            (HistoryRange::Today, "\"today\""),
            (HistoryRange::Yesterday, "\"yesterday\""),
            (HistoryRange::Last7Days, "\"last_7_days\""),
            (HistoryRange::Last30Days, "\"last_30_days\""),
        ] {
            assert_eq!(serde_json::to_string(&range).unwrap(), name);
            assert_eq!(serde_json::from_str::<HistoryRange>(name).unwrap(), range);
        }
    }

    #[test]
    fn display_name_prefers_untruncated_executable_name() {
        let app = AppIdentity {
            executable_path: Some(
                "/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.Networking.xpc/Contents/MacOS/com.apple.WebKit.Networking".into(),
            ),
            ..AppIdentity::default()
        };
        assert_eq!(
            app.display_name("com.apple.WebKi"),
            "com.apple.WebKit.Networking"
        );
        let bundle = AppIdentity {
            application_name: Some("Google Chrome".into()),
            ..app.clone()
        };
        assert_eq!(bundle.display_name("Google Chrome H"), "Google Chrome");
        assert_eq!(AppIdentity::default().display_name("curl"), "curl");
    }

    #[test]
    fn tracking_state_from_collector_status() {
        let mut snapshot = Snapshot::default();
        assert_eq!(snapshot.tracking_state(), TrackingState::Starting);
        snapshot.status = "Tracking · TCP/UDP · all interfaces · ~5s display delay".into();
        assert_eq!(snapshot.tracking_state(), TrackingState::Tracking);
        snapshot.status = "Collector gap: nettop stream closed; retry in 1s".into();
        assert_eq!(snapshot.tracking_state(), TrackingState::Gap);
        snapshot.status = "Stopped".into();
        assert_eq!(snapshot.live_rates().state, TrackingState::Stopped);
    }
}
