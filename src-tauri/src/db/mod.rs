pub mod schema;
pub mod time;

#[cfg(test)]
pub mod tests;

use crate::model::{AppDelta, AppHistoryRow, HistoryRange, HistorySummary, HistoryView};
use rusqlite::{params, Connection, Result};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub const DEFAULT_RETENTION_DAYS: u32 = 90;
pub const FLUSH_INTERVAL_SECONDS: i64 = 60;
/// A long-running instance re-applies retention at most this often (on flush).
pub const PRUNE_INTERVAL_SECONDS: i64 = 3600;

#[derive(Clone, Debug)]
struct PendingDelta {
    application_name: String,
    bundle_id: Option<String>,
    icon_path: Option<String>,
    executable_path: Option<String>,
    download_bytes: u64,
    upload_bytes: u64,
    last_seen_utc: i64,
}

pub struct HistoryStore {
    conn: Connection,
    app_id_cache: HashMap<String, i64>,
    current_bucket_start_utc: Option<i64>,
    pending_deltas: HashMap<String, PendingDelta>,
    last_flush_ts: i64,
    last_prune_utc: i64,
    pub retention_days: u32,
}

impl HistoryStore {
    pub fn default_db_path() -> PathBuf {
        if let Ok(override_path) = std::env::var("BANDPEEK_DB_PATH") {
            let trimmed = override_path.trim();
            if !trimmed.is_empty() {
                return PathBuf::from(trimmed);
            }
        }
        if let Some(home) = std::env::var_os("HOME") {
            PathBuf::from(home).join("Library/Application Support/BandPeek/bandpeek.db")
        } else {
            PathBuf::from("bandpeek.db")
        }
    }

    pub fn open_default() -> Result<Self> {
        Self::open(Self::default_db_path())
    }

    pub fn open_memory() -> Result<Self> {
        let mut conn = Connection::open_in_memory()?;
        schema::initialize_database(&mut conn)?;
        Ok(Self {
            conn,
            app_id_cache: HashMap::new(),
            current_bucket_start_utc: None,
            pending_deltas: HashMap::new(),
            last_flush_ts: 0,
            last_prune_utc: 0,
            retention_days: DEFAULT_RETENTION_DAYS,
        })
    }

    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self> {
        let path_ref = path.as_ref();
        if let Some(parent) = path_ref.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        let mut conn = match Connection::open(path_ref) {
            Ok(conn) => conn,
            Err(e) => {
                Self::quarantine_and_retry(path_ref, &e)?;
                Connection::open(path_ref)?
            }
        };

        if let Err(e) = schema::initialize_database(&mut conn) {
            drop(conn);
            Self::quarantine_and_retry(path_ref, &e)?;
            let mut fresh_conn = Connection::open(path_ref)?;
            schema::initialize_database(&mut fresh_conn)?;
            conn = fresh_conn;
        }

        let mut store = Self {
            conn,
            app_id_cache: HashMap::new(),
            current_bucket_start_utc: None,
            pending_deltas: HashMap::new(),
            last_flush_ts: 0,
            last_prune_utc: 0,
            retention_days: DEFAULT_RETENTION_DAYS,
        };

        let now_sec = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64;
        let _ = store.apply_retention(now_sec);

        Ok(store)
    }

    fn quarantine_and_retry(path: &Path, error: &rusqlite::Error) -> Result<()> {
        eprintln!(
            "BandPeek: SQLite database at {:?} is corrupted or unreadable ({}). Moving aside to create a clean database.",
            path, error
        );
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let backup_path = path.with_extension(format!("corrupt.{ts}.db"));
        let _ = std::fs::rename(path, backup_path);
        // Also remove possible WAL/SHM companion files
        let _ = std::fs::remove_file(path.with_extension("db-wal"));
        let _ = std::fs::remove_file(path.with_extension("db-shm"));
        Ok(())
    }

    pub fn record_deltas(&mut self, ts_utc: i64, deltas: &[AppDelta]) -> Result<()> {
        let bucket = time::minute_bucket(ts_utc);

        if let Some(curr) = self.current_bucket_start_utc {
            if curr != bucket {
                self.flush()?;
                self.current_bucket_start_utc = Some(bucket);
            }
        } else {
            self.current_bucket_start_utc = Some(bucket);
        }

        for delta in deltas {
            if delta.download_bytes == 0 && delta.upload_bytes == 0 {
                continue;
            }

            let entry = self
                .pending_deltas
                .entry(delta.identity_key.clone())
                .or_insert_with(|| PendingDelta {
                    application_name: delta.application_name.clone(),
                    bundle_id: delta.bundle_id.clone(),
                    icon_path: delta.icon_path.clone(),
                    executable_path: delta.executable_path.clone(),
                    download_bytes: 0,
                    upload_bytes: 0,
                    last_seen_utc: ts_utc,
                });

            entry.download_bytes = entry.download_bytes.saturating_add(delta.download_bytes);
            entry.upload_bytes = entry.upload_bytes.saturating_add(delta.upload_bytes);
            entry.last_seen_utc = entry.last_seen_utc.max(ts_utc);

            if !delta.application_name.is_empty() {
                entry.application_name = delta.application_name.clone();
            }
            if delta.bundle_id.is_some() {
                entry.bundle_id = delta.bundle_id.clone();
            }
            if delta.icon_path.is_some() {
                entry.icon_path = delta.icon_path.clone();
            }
            if delta.executable_path.is_some() {
                entry.executable_path = delta.executable_path.clone();
            }
        }

        // Periodically flush if elapsed since last flush exceeds interval
        if ts_utc.saturating_sub(self.last_flush_ts) >= FLUSH_INTERVAL_SECONDS {
            self.flush()?;
        }

        Ok(())
    }

    pub fn record_gap(
        &mut self,
        start_utc: i64,
        end_utc: i64,
        generation: u64,
        reason: &str,
    ) -> Result<()> {
        self.conn.execute(
            "INSERT INTO monitoring_gaps (start_utc, end_utc, generation, reason)
             VALUES (?1, ?2, ?3, ?4);",
            params![start_utc, end_utc, generation as i64, reason],
        )?;
        Ok(())
    }

    pub fn flush(&mut self) -> Result<()> {
        if self.pending_deltas.is_empty() {
            return Ok(());
        }

        let bucket = match self.current_bucket_start_utc {
            Some(b) => b,
            None => return Ok(()),
        };

        // A failed update-preparation flush must leave every observation
        // available for retry; publish the cache only after SQLite commits.
        let mut committed_cache = self.app_id_cache.clone();
        let tx = self.conn.transaction()?;

        for (identity_key, pending) in &self.pending_deltas {
            let app_id = match committed_cache.get(identity_key) {
                Some(&id) => {
                    tx.execute(
                        "UPDATE apps SET last_seen_utc = ?1 WHERE id = ?2;",
                        params![pending.last_seen_utc, id],
                    )?;
                    id
                }
                None => {
                    tx.execute(
                        "INSERT INTO apps (
                            identity_key, application_name, bundle_id, icon_path, executable_path, first_seen_utc, last_seen_utc
                        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)
                        ON CONFLICT(identity_key) DO UPDATE SET
                            application_name = CASE WHEN excluded.application_name != '' THEN excluded.application_name ELSE apps.application_name END,
                            bundle_id = COALESCE(excluded.bundle_id, apps.bundle_id),
                            icon_path = COALESCE(excluded.icon_path, apps.icon_path),
                            executable_path = COALESCE(excluded.executable_path, apps.executable_path),
                            last_seen_utc = excluded.last_seen_utc;",
                        params![
                            identity_key,
                            pending.application_name,
                            pending.bundle_id,
                            pending.icon_path,
                            pending.executable_path,
                            pending.last_seen_utc,
                        ],
                    )?;

                    let id: i64 = tx.query_row(
                        "SELECT id FROM apps WHERE identity_key = ?1;",
                        params![identity_key],
                        |row| row.get(0),
                    )?;
                    committed_cache.insert(identity_key.clone(), id);
                    id
                }
            };

            let down_i64 = i64::try_from(pending.download_bytes).unwrap_or(i64::MAX);
            let up_i64 = i64::try_from(pending.upload_bytes).unwrap_or(i64::MAX);

            tx.execute(
                "INSERT INTO traffic_buckets (bucket_start_utc, app_id, download_bytes, upload_bytes)
                 VALUES (?1, ?2, ?3, ?4)
                 ON CONFLICT(bucket_start_utc, app_id) DO UPDATE SET
                     download_bytes = traffic_buckets.download_bytes + excluded.download_bytes,
                     upload_bytes = traffic_buckets.upload_bytes + excluded.upload_bytes;",
                params![bucket, app_id, down_i64, up_i64],
            )?;
        }

        tx.commit()?;
        self.pending_deltas.clear();
        self.app_id_cache = committed_cache;
        self.last_flush_ts = bucket;
        if bucket.saturating_sub(self.last_prune_utc) >= PRUNE_INTERVAL_SECONDS {
            self.apply_retention(bucket)?;
        }
        Ok(())
    }

    /// Changes the retention window and prunes immediately.
    pub fn set_retention_days(&mut self, days: u32, now_utc: i64) -> Result<usize> {
        self.retention_days = days.max(1);
        self.apply_retention(now_utc)
    }

    fn apply_retention(&mut self, now_utc: i64) -> Result<usize> {
        self.last_prune_utc = now_utc;
        self.prune_older_than(i64::from(self.retention_days) * 86400, now_utc)
    }

    pub fn get_history(&mut self, range: HistoryRange, now_utc: i64) -> Result<HistoryView> {
        self.flush()?;

        let (start_utc, end_utc) = time::range_bounds(range, now_utc);

        let mut stmt = self.conn.prepare(
            "SELECT
                a.identity_key,
                a.application_name,
                a.bundle_id,
                a.icon_path,
                a.executable_path,
                COALESCE(SUM(tb.download_bytes), 0) AS down_bytes,
                COALESCE(SUM(tb.upload_bytes), 0) AS up_bytes,
                COALESCE(SUM(tb.download_bytes + tb.upload_bytes), 0) AS total_bytes
            FROM traffic_buckets tb
            JOIN apps a ON tb.app_id = a.id
            WHERE tb.bucket_start_utc >= ?1 AND tb.bucket_start_utc < ?2
            GROUP BY tb.app_id
            ORDER BY total_bytes DESC, a.id ASC;",
        )?;

        let mut rows = Vec::new();
        let mut total_down = 0u64;
        let mut total_up = 0u64;
        let mut total_bytes = 0u64;

        let query_rows = stmt.query_map(params![start_utc, end_utc], |row| {
            let identity_key: String = row.get(0)?;
            let application_name: String = row.get(1)?;
            let bundle_id: Option<String> = row.get(2)?;
            let icon_path: Option<String> = row.get(3)?;
            let executable_path: Option<String> = row.get(4)?;
            let down = (row.get::<_, i64>(5)?).max(0) as u64;
            let up = (row.get::<_, i64>(6)?).max(0) as u64;
            let tot = (row.get::<_, i64>(7)?).max(0) as u64;
            Ok((
                identity_key,
                application_name,
                bundle_id,
                icon_path,
                executable_path,
                down,
                up,
                tot,
            ))
        })?;

        for item in query_rows {
            let (
                identity_key,
                application_name,
                bundle_id,
                icon_path,
                executable_path,
                down,
                up,
                tot,
            ) = item?;
            total_down = total_down.saturating_add(down);
            total_up = total_up.saturating_add(up);
            total_bytes = total_bytes.saturating_add(tot);

            let presentation = crate::presentation::present(
                &identity_key,
                &application_name,
                bundle_id.as_deref(),
                executable_path.as_deref(),
            );
            rows.push(AppHistoryRow {
                identity_key,
                application_name,
                display_name: presentation.display_name,
                kind: presentation.kind,
                bundle_id,
                icon_path,
                executable_path,
                download_bytes: down,
                upload_bytes: up,
                total_bytes: tot,
                share_percent: 0.0,
            });
        }

        if total_bytes > 0 {
            for row in &mut rows {
                row.share_percent = (row.total_bytes as f64 / total_bytes as f64) * 100.0;
            }
        }

        Ok(HistoryView {
            range,
            range_start_utc: start_utc,
            range_end_utc: end_utc,
            summary: HistorySummary {
                download_bytes: total_down,
                upload_bytes: total_up,
                total_bytes,
            },
            rows,
        })
    }

    pub fn prune_older_than(&mut self, retention_seconds: i64, now_utc: i64) -> Result<usize> {
        let cutoff_utc = now_utc.saturating_sub(retention_seconds);
        let tx = self.conn.transaction()?;

        let deleted_buckets = tx.execute(
            "DELETE FROM traffic_buckets WHERE bucket_start_utc < ?1;",
            params![cutoff_utc],
        )?;

        tx.execute(
            "DELETE FROM monitoring_gaps WHERE end_utc < ?1;",
            params![cutoff_utc],
        )?;

        tx.execute(
            "DELETE FROM apps WHERE last_seen_utc < ?1 AND NOT EXISTS (
                SELECT 1 FROM traffic_buckets WHERE app_id = apps.id
            );",
            params![cutoff_utc],
        )?;

        tx.commit()?;
        self.app_id_cache.clear();
        Ok(deleted_buckets)
    }

    pub fn clear(&mut self) -> Result<()> {
        self.pending_deltas.clear();
        self.app_id_cache.clear();
        self.current_bucket_start_utc = None;

        let tx = self.conn.transaction()?;
        tx.execute_batch(
            "DELETE FROM traffic_buckets;
             DELETE FROM monitoring_gaps;
             DELETE FROM apps;",
        )?;
        tx.commit()?;
        Ok(())
    }
}

impl Drop for HistoryStore {
    fn drop(&mut self) {
        let _ = self.flush();
    }
}
