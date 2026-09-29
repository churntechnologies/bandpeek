use rusqlite::{Connection, Result};

pub const CURRENT_SCHEMA_VERSION: u32 = 1;

pub fn initialize_database(conn: &mut Connection) -> Result<()> {
    conn.execute_batch(
        "PRAGMA journal_mode = WAL;
         PRAGMA synchronous = NORMAL;
         PRAGMA foreign_keys = ON;",
    )?;

    let current_version: u32 = conn.query_row("PRAGMA user_version;", [], |row| row.get(0))?;

    if current_version < 1 {
        migrate_v1(conn)?;
    }

    Ok(())
}

fn migrate_v1(conn: &mut Connection) -> Result<()> {
    let tx = conn.transaction()?;

    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS apps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_key TEXT NOT NULL UNIQUE,
            application_name TEXT NOT NULL,
            bundle_id TEXT,
            icon_path TEXT,
            executable_path TEXT,
            first_seen_utc INTEGER NOT NULL,
            last_seen_utc INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_apps_identity ON apps(identity_key);

        CREATE TABLE IF NOT EXISTS traffic_buckets (
            bucket_start_utc INTEGER NOT NULL,
            app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            download_bytes INTEGER NOT NULL,
            upload_bytes INTEGER NOT NULL,
            PRIMARY KEY (bucket_start_utc, app_id)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS idx_buckets_time ON traffic_buckets(bucket_start_utc);

        CREATE TABLE IF NOT EXISTS monitoring_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_utc INTEGER NOT NULL,
            end_utc INTEGER NOT NULL,
            generation INTEGER NOT NULL,
            reason TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gaps_start ON monitoring_gaps(start_utc);

        PRAGMA user_version = 1;",
    )?;

    tx.commit()?;
    Ok(())
}
