use super::*;
use crate::model::AppDelta;

fn sample_delta(key: &str, name: &str, down: u64, up: u64) -> AppDelta {
    AppDelta {
        identity_key: key.to_string(),
        application_name: name.to_string(),
        bundle_id: if key.starts_with("bundle:") {
            Some(key.strip_prefix("bundle:").unwrap().to_string())
        } else {
            None
        },
        icon_path: None,
        executable_path: if key.starts_with("exec:") {
            Some(key.strip_prefix("exec:").unwrap().to_string())
        } else {
            None
        },
        download_bytes: down,
        upload_bytes: up,
    }
}

#[test]
fn test_persist_and_retrieve_today() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469; // Reference time during a day

    let deltas = vec![
        sample_delta("bundle:com.google.Chrome", "Google Chrome", 5000, 1000),
        sample_delta("bundle:com.apple.Safari", "Safari", 2000, 500),
    ];

    store.record_deltas(now, &deltas).unwrap();
    let view = store.get_history(HistoryRange::Today, now).unwrap();

    assert_eq!(view.summary.download_bytes, 7000);
    assert_eq!(view.summary.upload_bytes, 1500);
    assert_eq!(view.summary.total_bytes, 8500);
    assert_eq!(view.rows.len(), 2);

    // Sorted by total usage desc
    assert_eq!(view.rows[0].identity_key, "bundle:com.google.Chrome");
    assert_eq!(view.rows[0].download_bytes, 5000);
    assert_eq!(view.rows[0].upload_bytes, 1000);
    assert_eq!(view.rows[0].total_bytes, 6000);
    let chrome_share = (6000.0 / 8500.0) * 100.0;
    assert!((view.rows[0].share_percent - chrome_share).abs() < 0.01);

    assert_eq!(view.rows[1].identity_key, "bundle:com.apple.Safari");
    assert_eq!(view.rows[1].download_bytes, 2000);
    assert_eq!(view.rows[1].upload_bytes, 500);
    assert_eq!(view.rows[1].total_bytes, 2500);
}

#[test]
fn test_quit_relaunch_retains_totals_and_no_double_counting() {
    let temp_dir = std::env::temp_dir().join(format!("bandpeek_test_{}", std::process::id()));
    let _ = std::fs::create_dir_all(&temp_dir);
    let db_path = temp_dir.join("test_relaunch.db");

    let now = 1790616469;

    // Session 1: run and record deltas
    {
        let mut store = HistoryStore::open(&db_path).unwrap();
        store
            .record_deltas(
                now,
                &[sample_delta(
                    "bundle:com.google.Chrome",
                    "Google Chrome",
                    10_000,
                    2_000,
                )],
            )
            .unwrap();
        store.flush().unwrap();
    }

    // Session 2: relaunch (new connection to existing database file)
    {
        let mut store = HistoryStore::open(&db_path).unwrap();
        let view = store.get_history(HistoryRange::Today, now).unwrap();
        assert_eq!(view.summary.total_bytes, 12_000);
        assert_eq!(view.rows.len(), 1);

        // Record a new incremental delta in session 2
        store
            .record_deltas(
                now + 60,
                &[sample_delta(
                    "bundle:com.google.Chrome",
                    "Google Chrome",
                    3_000,
                    1_000,
                )],
            )
            .unwrap();
        store.flush().unwrap();

        let updated = store.get_history(HistoryRange::Today, now + 60).unwrap();
        assert_eq!(updated.summary.download_bytes, 13_000);
        assert_eq!(updated.summary.upload_bytes, 3_000);
        assert_eq!(updated.summary.total_bytes, 16_000);
    }

    let _ = std::fs::remove_dir_all(temp_dir);
}

#[test]
fn test_yesterday_does_not_include_today() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469; // 2026-09-29 01:27:49 local
    let today_mid = time::local_midnight_utc(now);
    let yesterday_mid = time::days_ago_midnight_utc(now, 1);
    assert!(yesterday_mid < today_mid);

    // Yesterday timestamp (middle of yesterday)
    let yesterday_ts = yesterday_mid + 3600 * 12;
    // Today timestamp
    let today_ts = now;

    // Record yesterday traffic
    store
        .record_deltas(
            yesterday_ts,
            &[sample_delta(
                "bundle:com.apple.Music",
                "Music",
                40_000,
                1_000,
            )],
        )
        .unwrap();

    // Record today traffic
    store
        .record_deltas(
            today_ts,
            &[sample_delta(
                "bundle:com.google.Chrome",
                "Google Chrome",
                50_000,
                5_000,
            )],
        )
        .unwrap();

    // Query Yesterday: should only have Music, not Chrome!
    let yesterday_view = store.get_history(HistoryRange::Yesterday, now).unwrap();
    assert_eq!(yesterday_view.summary.download_bytes, 40_000);
    assert_eq!(yesterday_view.summary.upload_bytes, 1_000);
    assert_eq!(yesterday_view.rows.len(), 1);
    assert_eq!(
        yesterday_view.rows[0].identity_key,
        "bundle:com.apple.Music"
    );

    // Query Today: should only have Chrome, not Music!
    let today_view = store.get_history(HistoryRange::Today, now).unwrap();
    assert_eq!(today_view.summary.download_bytes, 50_000);
    assert_eq!(today_view.summary.upload_bytes, 5_000);
    assert_eq!(today_view.rows.len(), 1);
    assert_eq!(today_view.rows[0].identity_key, "bundle:com.google.Chrome");

    // Query Last 7 Days: includes both yesterday and today!
    let seven_view = store.get_history(HistoryRange::Last7Days, now).unwrap();
    assert_eq!(seven_view.summary.download_bytes, 90_000);
    assert_eq!(seven_view.summary.upload_bytes, 6_000);
    assert_eq!(seven_view.rows.len(), 2);
}

#[test]
fn test_midnight_crossing_partitioning() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469;
    let today_mid = time::local_midnight_utc(now);

    // One sample 10 seconds before midnight
    let before_mid = today_mid - 10;
    // One sample 10 seconds after midnight
    let after_mid = today_mid + 10;

    store
        .record_deltas(
            before_mid,
            &[sample_delta("bundle:com.slack.Slack", "Slack", 100, 50)],
        )
        .unwrap();

    store
        .record_deltas(
            after_mid,
            &[sample_delta("bundle:com.slack.Slack", "Slack", 200, 150)],
        )
        .unwrap();

    // Checked from perspective of "after_mid" (Today):
    let today_view = store.get_history(HistoryRange::Today, after_mid).unwrap();
    assert_eq!(today_view.summary.download_bytes, 200);
    assert_eq!(today_view.summary.upload_bytes, 150);

    let yesterday_view = store
        .get_history(HistoryRange::Yesterday, after_mid)
        .unwrap();
    assert_eq!(yesterday_view.summary.download_bytes, 100);
    assert_eq!(yesterday_view.summary.upload_bytes, 50);
}

#[test]
fn test_retention_pruning() {
    let mut store = HistoryStore::open_memory().unwrap();
    // This test exercises the explicit prune; keep the hourly flush prune out of the way.
    store.last_prune_utc = i64::MAX;
    let now = 1790616469;
    let ninety_days_ago = now - (90 * 86400);
    let old_ts = ninety_days_ago - 3600; // 90 days and 1 hour ago
    let fresh_ts = now - 3600; // 1 hour ago

    store
        .record_deltas(
            old_ts,
            &[sample_delta("bundle:com.old.app", "Old App", 500, 500)],
        )
        .unwrap();
    store
        .record_deltas(
            fresh_ts,
            &[sample_delta("bundle:com.new.app", "New App", 1000, 1000)],
        )
        .unwrap();

    store.flush().unwrap();

    // Prune data older than 90 days
    let pruned = store.prune_older_than(90 * 86400, now).unwrap();
    assert_eq!(pruned, 1);

    // Old App is pruned; New App remains
    let view = store.get_history(HistoryRange::Last30Days, now).unwrap();
    assert_eq!(view.summary.download_bytes, 1000);
    assert_eq!(view.rows.len(), 1);
    assert_eq!(view.rows[0].identity_key, "bundle:com.new.app");
}

#[test]
fn test_clear_history() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469;

    store
        .record_deltas(
            now,
            &[sample_delta(
                "bundle:com.google.Chrome",
                "Google Chrome",
                50_000,
                10_000,
            )],
        )
        .unwrap();
    store.flush().unwrap();

    let view_before = store.get_history(HistoryRange::Today, now).unwrap();
    assert_eq!(view_before.summary.total_bytes, 60_000);

    store.clear().unwrap();

    let view_after = store.get_history(HistoryRange::Today, now).unwrap();
    assert_eq!(view_after.summary.total_bytes, 0);
    assert_eq!(view_after.rows.len(), 0);
}

#[test]
fn test_large_byte_values_do_not_overflow() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469;

    // 100 Gigabytes download and 50 Gigabytes upload
    let large_down = 100 * 1024 * 1024 * 1024u64; // 107,374,182,400 bytes
    let large_up = 50 * 1024 * 1024 * 1024u64;

    store
        .record_deltas(
            now,
            &[sample_delta(
                "bundle:com.large.transfer",
                "Large Transfer",
                large_down,
                large_up,
            )],
        )
        .unwrap();

    let view = store.get_history(HistoryRange::Today, now).unwrap();
    assert_eq!(view.summary.download_bytes, large_down);
    assert_eq!(view.summary.upload_bytes, large_up);
    assert_eq!(view.summary.total_bytes, large_down + large_up);
    assert_eq!(view.rows[0].download_bytes, large_down);
    assert_eq!(view.rows[0].upload_bytes, large_up);
    assert_eq!(view.rows[0].total_bytes, large_down + large_up);
}

#[test]
fn test_gap_recording() {
    let mut store = HistoryStore::open_memory().unwrap();
    let start = 1790616000;
    let end = 1790616030;

    store.record_gap(start, end, 1, "nettop stalled").unwrap();

    let gap_count: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM monitoring_gaps;", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(gap_count, 1);
}

#[test]
fn test_multi_process_aggregation_into_same_app_bundle() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469;

    let chrome_helper_1 = sample_delta("bundle:com.google.Chrome", "Google Chrome", 10_000, 2_000);
    let chrome_helper_2 = sample_delta("bundle:com.google.Chrome", "Google Chrome", 15_000, 3_000);
    let curl = sample_delta("exec:/usr/bin/curl", "curl", 5_000, 1_000);

    store
        .record_deltas(now, &[chrome_helper_1, chrome_helper_2, curl])
        .unwrap();

    let view = store.get_history(HistoryRange::Today, now).unwrap();
    assert_eq!(view.rows.len(), 2);

    let chrome = &view.rows[0];
    assert_eq!(chrome.identity_key, "bundle:com.google.Chrome");
    assert_eq!(chrome.download_bytes, 25_000);
    assert_eq!(chrome.upload_bytes, 5_000);
    assert_eq!(chrome.total_bytes, 30_000);

    let curl_row = &view.rows[1];
    assert_eq!(curl_row.identity_key, "exec:/usr/bin/curl");
    assert_eq!(curl_row.download_bytes, 5_000);
    assert_eq!(curl_row.upload_bytes, 1_000);
    assert_eq!(curl_row.total_bytes, 6_000);

    assert_eq!(view.summary.total_bytes, 36_000);
}

#[test]
fn test_collector_gap_and_rebaseline_generates_no_bogus_traffic() {
    use crate::aggregate::Aggregator;
    use crate::model::{AppIdentity, Observation, OsCounters, ProcessId};

    let mut store = HistoryStore::open_memory().unwrap();
    let mut agg = Aggregator::new(1_000_000);

    let obs_fn = |pid: i32, start_us: u64, down: u64, up: u64| Observation {
        id: ProcessId { pid, start_us },
        process_name: "Safari".into(),
        app: AppIdentity {
            application_name: Some("Safari".into()),
            bundle_id: Some("com.apple.Safari".into()),
            icon_path: None,
            executable_path: None,
        },
        counters: OsCounters {
            download: down,
            upload: up,
        },
    };

    // First sample of generation 1: process 100 has 10 MB transferred before BandPeek started
    agg.apply(1000, vec![obs_fn(100, 500_000, 10_000_000, 1_000_000)]);
    let deltas = agg.drain_deltas();
    // Must be baselined: zero deltas produced!
    assert!(deltas.is_empty());

    // Second sample of generation 1: process 100 downloads 500 KB
    agg.apply(6000, vec![obs_fn(100, 500_000, 10_500_000, 1_000_000)]);
    let deltas = agg.drain_deltas();
    assert_eq!(deltas.len(), 1);
    assert_eq!(deltas[0].download_bytes, 500_000);
    store.record_deltas(1790616000, &deltas).unwrap();

    // Now a gap occurs: nettop crashes/restarts.
    store
        .record_gap(1790616006, 1790616010, 1, "nettop exit 1")
        .unwrap();
    agg.restart(2_000_000); // Generation 2 epoch

    // Process 100 is still running, now at 20 MB download.
    // In generation 2's first sample, it MUST rebaseline, NOT record 20 MB - 10.5 MB!
    agg.apply(11000, vec![obs_fn(100, 500_000, 20_000_000, 1_000_000)]);
    let deltas_after_restart = agg.drain_deltas();
    assert!(
        deltas_after_restart.is_empty(),
        "Rebaseline after gap must NOT produce bogus deltas!"
    );

    // In generation 2's second sample, process 100 downloads 200 KB
    agg.apply(16000, vec![obs_fn(100, 500_000, 20_200_000, 1_000_000)]);
    let deltas2 = agg.drain_deltas();
    assert_eq!(deltas2.len(), 1);
    assert_eq!(deltas2[0].download_bytes, 200_000);
    store.record_deltas(1790616016, &deltas2).unwrap();

    // Total in store: 500 KB + 200 KB = 700 KB (no 10 MB or 9.5 MB jump!)
    let view = store.get_history(HistoryRange::Today, 1790616020).unwrap();
    assert_eq!(view.summary.download_bytes, 700_000);
    assert_eq!(view.rows[0].download_bytes, 700_000);
}

#[test]
fn test_flush_applies_retention_periodically() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469;
    let old_ts = now - 91 * 86400;
    store
        .record_deltas(old_ts, &[sample_delta("bundle:com.old.app", "Old", 10, 10)])
        .unwrap();
    store.flush().unwrap();
    // A later flush more than an hour after the last prune applies retention.
    store
        .record_deltas(now, &[sample_delta("bundle:com.new.app", "New", 5, 5)])
        .unwrap();
    store.flush().unwrap();
    let rows: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM traffic_buckets;", [], |r| r.get(0))
        .unwrap();
    assert_eq!(rows, 1);
    let apps: i64 = store
        .conn
        .query_row("SELECT COUNT(*) FROM apps;", [], |r| r.get(0))
        .unwrap();
    assert_eq!(apps, 1, "orphaned app outside retention is removed");
}

#[test]
fn test_set_retention_days_prunes_immediately() {
    let mut store = HistoryStore::open_memory().unwrap();
    store.last_prune_utc = i64::MAX;
    let now = 1790616469;
    for (days_ago, key) in [(100, "bundle:a"), (45, "bundle:b"), (2, "bundle:c")] {
        store
            .record_deltas(now - days_ago * 86400, &[sample_delta(key, key, 100, 0)])
            .unwrap();
        store.flush().unwrap();
    }
    assert_eq!(store.set_retention_days(90, now).unwrap(), 1);
    assert_eq!(store.set_retention_days(30, now).unwrap(), 1);
    assert_eq!(store.retention_days, 30);
    let view = store.get_history(HistoryRange::Last30Days, now).unwrap();
    assert_eq!(view.rows.len(), 1);
    assert_eq!(view.rows[0].identity_key, "bundle:c");
    // Growing retention does not restore anything and prunes nothing.
    assert_eq!(store.set_retention_days(365, now).unwrap(), 0);
}

#[test]
fn test_share_percent_and_range_rows() {
    let mut store = HistoryStore::open_memory().unwrap();
    let now = 1790616469;
    store
        .record_deltas(
            now,
            &[
                sample_delta("bundle:a", "A", 600, 150),
                sample_delta("bundle:b", "B", 200, 0),
                sample_delta("exec:/usr/bin/curl", "curl", 40, 10),
            ],
        )
        .unwrap();
    let view = store.get_history(HistoryRange::Today, now).unwrap();
    assert_eq!(view.summary.total_bytes, 1000);
    let shares: Vec<f64> = view.rows.iter().map(|r| r.share_percent).collect();
    assert_eq!(shares, vec![75.0, 20.0, 5.0]);
    assert_eq!(
        view.rows.iter().map(|r| r.total_bytes).sum::<u64>(),
        view.summary.total_bytes
    );
}

#[test]
fn test_label_change_keeps_one_identity_and_all_history() {
    // Milestone 4 stored Claude Code as "claude"; a newer resolver stores the
    // bundle's declared "Claude Code" under the same identity key.
    let path = std::env::temp_dir().join(format!("bandpeek_label_{}.db", std::process::id()));
    let _ = std::fs::remove_file(&path);
    let key = "bundle:com.anthropic.claude-code";
    let yesterday = 1790616469 - 86400;
    let now = 1790616469;
    {
        let mut store = HistoryStore::open(&path).unwrap();
        store
            .record_deltas(yesterday, &[sample_delta(key, "claude", 1000, 100)])
            .unwrap();
    }
    {
        let mut store = HistoryStore::open(&path).unwrap();
        store
            .record_deltas(now, &[sample_delta(key, "Claude Code", 500, 50)])
            .unwrap();
        store.flush().unwrap();
        let apps: i64 = store
            .conn
            .query_row("SELECT COUNT(*) FROM apps", [], |r| r.get(0))
            .unwrap();
        assert_eq!(apps, 1, "a label change must not create a second identity");
        let view = store.get_history(HistoryRange::Last7Days, now).unwrap();
        assert_eq!(view.rows.len(), 1);
        assert_eq!(view.rows[0].identity_key, key);
        assert_eq!(view.rows[0].total_bytes, 1650);
        assert_eq!(view.rows[0].display_name, "Claude Code");
        assert_eq!(
            view.rows[0].kind,
            crate::presentation::AppKind::CommandLineTool
        );
    }
    // Rows never seen again keep their stored name but still present well.
    {
        let mut store = HistoryStore::open_memory().unwrap();
        store
            .record_deltas(now, &[sample_delta(key, "claude", 10, 0)])
            .unwrap();
        let row = &store.get_history(HistoryRange::Today, now).unwrap().rows[0];
        assert_eq!(row.application_name, "claude");
        assert_eq!(row.display_name, "Claude Code");
    }
    let _ = std::fs::remove_file(&path);
    let _ = std::fs::remove_file(path.with_extension("db-wal"));
    let _ = std::fs::remove_file(path.with_extension("db-shm"));
}
