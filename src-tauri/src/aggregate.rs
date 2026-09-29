use crate::model::*;
use std::collections::{HashMap, HashSet};

pub struct Aggregator {
    rows: HashMap<ProcessId, ProcessRow>,
    baselines: HashMap<ProcessId, OsCounters>,
    last_ms: Option<u64>,
    epoch_us: u64,
    pub snapshot: Snapshot,
    pub pending_deltas: Vec<AppDelta>,
}
impl Aggregator {
    pub fn new(epoch_us: u64) -> Self {
        Self {
            rows: HashMap::new(),
            baselines: HashMap::new(),
            last_ms: None,
            epoch_us,
            snapshot: Snapshot::default(),
            pending_deltas: Vec::new(),
        }
    }
    /// Resume an update-paused worker without losing session totals or rows.
    /// OS counter baselines are deliberately rebuilt for its new nettop child.
    pub fn resume_from(snapshot: Snapshot, epoch_us: u64) -> Self {
        let mut aggregate = Self::new(epoch_us);
        aggregate.snapshot = snapshot;
        aggregate.rows = std::mem::take(&mut aggregate.snapshot.rows)
            .into_iter()
            .map(|row| (row.id.clone(), row))
            .collect();
        aggregate.restart(epoch_us);
        aggregate
    }
    // A lost collector generation is an explicit measurement gap. Keep session totals,
    // but never subtract counters across two nettop lifetimes.
    pub fn restart(&mut self, epoch_us: u64) {
        self.baselines.clear();
        self.last_ms = None;
        self.epoch_us = epoch_us;
        self.snapshot.download_bytes_per_second = 0.0;
        self.snapshot.upload_bytes_per_second = 0.0;
        self.pending_deltas.clear();
        for row in self.rows.values_mut() {
            row.active = false;
        }
    }
    pub fn apply(&mut self, at_ms: u64, observations: Vec<Observation>) -> bool {
        let mut seen = HashSet::new();
        if self.last_ms.is_some_and(|last| at_ms <= last)
            || observations.iter().any(|o| !seen.insert(o.id.clone()))
        {
            self.snapshot.rejected_samples += 1;
            return false;
        }
        let mut delta = SessionBytes::default();
        for row in self.rows.values_mut() {
            row.active = false;
        }
        for observation in observations {
            let id = observation.id;
            let now = observation.counters;
            let previous = self.baselines.insert(id.clone(), now);
            let increment = if let Some(old) = previous {
                // A decrease is not evidence of new bytes. Rebaseline that direction.
                if now.download < old.download || now.upload < old.upload {
                    self.snapshot.counter_resets += 1;
                }
                SessionBytes {
                    download: now.download.saturating_sub(old.download),
                    upload: now.upload.saturating_sub(old.upload),
                }
            } else if self.last_ms.is_some() && id.start_us >= self.epoch_us {
                // A process born inside this collector generation has no pre-session bytes.
                SessionBytes {
                    download: now.download,
                    upload: now.upload,
                }
            } else {
                SessionBytes::default()
            };
            let row = self.rows.entry(id.clone()).or_insert_with(|| ProcessRow {
                id,
                process_name: observation.process_name.clone(),
                app: observation.app.clone(),
                bytes: SessionBytes::default(),
                total_bytes: 0,
                active: true,
                os_counters: now,
                last_seen_ms: at_ms,
            });
            row.process_name = observation.process_name;
            row.app = observation.app;
            row.bytes.download = row.bytes.download.saturating_add(increment.download);
            row.bytes.upload = row.bytes.upload.saturating_add(increment.upload);
            row.total_bytes = row.bytes.download.saturating_add(row.bytes.upload);
            row.active = true;
            row.os_counters = now;
            row.last_seen_ms = at_ms;
            delta.download = delta.download.saturating_add(increment.download);
            delta.upload = delta.upload.saturating_add(increment.upload);
            if increment.download > 0 || increment.upload > 0 {
                self.pending_deltas.push(AppDelta {
                    identity_key: row.app.stable_key(&row.process_name),
                    application_name: row.app.display_name(&row.process_name),
                    bundle_id: row.app.bundle_id.clone(),
                    icon_path: row.app.icon_path.clone(),
                    executable_path: row.app.executable_path.clone(),
                    download_bytes: increment.download,
                    upload_bytes: increment.upload,
                });
            }
        }
        // Keep baselines for absent identities. A temporarily absent process must not
        // have its entire lifetime counter counted again when it reappears.
        let elapsed = self.last_ms.map(|v| (at_ms - v) as f64 / 1000.0);
        self.snapshot.download_bytes_per_second =
            elapsed.map_or(0.0, |s| delta.download as f64 / s);
        self.snapshot.upload_bytes_per_second = elapsed.map_or(0.0, |s| delta.upload as f64 / s);
        self.snapshot.session_bytes.download = self
            .snapshot
            .session_bytes
            .download
            .saturating_add(delta.download);
        self.snapshot.session_bytes.upload = self
            .snapshot
            .session_bytes
            .upload
            .saturating_add(delta.upload);
        self.last_ms = Some(at_ms);
        self.snapshot.sample_elapsed_ms = at_ms;
        self.snapshot.sample_sequence += 1;
        // Bound retired rows; advance the birth cutoff on eviction to avoid recounting
        // a very old identity that returns after its baseline has been evicted.
        while self.rows.len() > 2048 {
            let victim = self
                .rows
                .iter()
                .filter(|(_, r)| !r.active)
                .min_by_key(|(_, r)| r.last_seen_ms)
                .map(|(id, _)| id.clone());
            let Some(id) = victim else { break };
            let row = self.rows.remove(&id).unwrap();
            self.baselines.remove(&id);
            self.epoch_us = self.epoch_us.max(id.start_us.saturating_add(1));
            self.snapshot.archived_bytes.download += row.bytes.download;
            self.snapshot.archived_bytes.upload += row.bytes.upload;
        }
        true
    }
    pub fn view(&self) -> Snapshot {
        let mut view = self.snapshot.clone();
        view.rows = self.rows.values().cloned().collect();
        view.rows.sort_by(|a, b| {
            b.total_bytes
                .cmp(&a.total_bytes)
                .then(a.id.pid.cmp(&b.id.pid))
        });
        view
    }
    pub fn drain_deltas(&mut self) -> Vec<AppDelta> {
        std::mem::take(&mut self.pending_deltas)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn row(pid: i32, start_us: u64, down: u64, up: u64) -> Observation {
        Observation {
            id: ProcessId { pid, start_us },
            process_name: "test".into(),
            app: AppIdentity::default(),
            counters: OsCounters {
                download: down,
                upload: up,
            },
        }
    }
    #[test]
    fn update_resume_retains_totals_and_rebaselines_new_child() {
        let mut before = Aggregator::new(100);
        before.apply(0, vec![row(1, 1, 100, 20)]);
        before.apply(5000, vec![row(1, 1, 170, 30)]);
        before.snapshot.collector_generation = 3;
        let snapshot = before.view();
        let mut after = Aggregator::resume_from(snapshot.clone(), 200);
        after.apply(0, vec![row(1, 1, 900, 100)]); // new child's baseline is not traffic
        assert_eq!(after.view().session_bytes, snapshot.session_bytes);
        assert_eq!(
            after.view().rows[0].total_bytes,
            snapshot.rows[0].total_bytes
        );
        assert_eq!(after.snapshot.collector_generation, 3);
        assert!(after.snapshot.sample_sequence > snapshot.sample_sequence);
        after.apply(5000, vec![row(1, 1, 910, 105)]);
        assert_eq!(
            after.snapshot.session_bytes.download,
            snapshot.session_bytes.download + 10
        );
    }
    #[test]
    fn baseline_delta_and_rates() {
        let mut a = Aggregator::new(100);
        a.apply(0, vec![row(1, 1, 900, 400)]);
        a.apply(3000, vec![row(1, 1, 1200, 460)]);
        assert_eq!(
            a.snapshot.session_bytes,
            SessionBytes {
                download: 300,
                upload: 60
            }
        );
        assert_eq!(a.snapshot.download_bytes_per_second, 100.0);
    }
    #[test]
    fn birth_exit_reappearance_and_pid_reuse() {
        let mut a = Aggregator::new(100);
        a.apply(0, vec![]);
        a.apply(3000, vec![row(1, 101, 30, 10)]);
        a.apply(6000, vec![]);
        assert!(!a.view().rows[0].active);
        a.apply(9000, vec![row(1, 101, 40, 10)]);
        assert_eq!(a.snapshot.session_bytes.download, 40);
        a.apply(12000, vec![row(1, 102, 7, 3)]);
        assert_eq!(a.snapshot.session_bytes.download, 47);
        assert_eq!(a.view().rows.len(), 2);
    }
    #[test]
    fn resets_are_directional_and_never_negative() {
        let mut a = Aggregator::new(100);
        a.apply(0, vec![row(1, 1, 900, 400)]);
        a.apply(3000, vec![row(1, 1, 20, 450)]);
        a.apply(6000, vec![row(1, 1, 25, 455)]);
        assert_eq!(
            a.snapshot.session_bytes,
            SessionBytes {
                download: 5,
                upload: 55
            }
        );
        assert_eq!(a.snapshot.counter_resets, 1);
    }
    #[test]
    fn restart_retains_totals_but_rebaselines() {
        let mut a = Aggregator::new(100);
        a.apply(0, vec![row(1, 1, 10, 0)]);
        a.apply(3000, vec![row(1, 1, 20, 0)]);
        a.restart(200);
        a.apply(6000, vec![row(1, 1, 900, 0)]);
        a.apply(9000, vec![row(1, 1, 910, 0)]);
        assert_eq!(a.snapshot.session_bytes.download, 20);
    }
    #[test]
    fn long_pause_rates_use_actual_spacing_and_restart_discards_jump() {
        let mut a = Aggregator::new(100);
        a.apply(0, vec![row(1, 1, 100, 200)]);
        a.apply(5_000, vec![row(1, 1, 200, 300)]);
        a.apply(65_000, vec![row(1, 1, 800, 900)]);
        assert_eq!(a.snapshot.download_bytes_per_second, 10.0);
        let before = a.snapshot.session_bytes;
        a.restart(1_000);
        a.apply(70_000, vec![row(1, 1, 9_000_000, 1)]);
        assert_eq!(a.snapshot.session_bytes, before);
        assert_eq!(a.snapshot.download_bytes_per_second, 0.0);
        a.apply(75_000, vec![row(1, 1, 9_000_100, 51)]);
        assert_eq!(a.snapshot.session_bytes.download, before.download + 100);
        assert_eq!(a.snapshot.upload_bytes_per_second, 10.0);
    }
    #[test]
    fn duplicate_and_out_of_order_frames_are_atomic_rejections() {
        let mut a = Aggregator::new(100);
        a.apply(0, vec![row(1, 1, 10, 0)]);
        assert!(!a.apply(3000, vec![row(1, 1, 20, 0), row(1, 1, 20, 0)]));
        assert!(!a.apply(0, vec![]));
        a.apply(6000, vec![row(1, 1, 30, 0)]);
        assert_eq!(a.snapshot.session_bytes.download, 20);
    }
}
