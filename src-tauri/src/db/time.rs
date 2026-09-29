use crate::model::HistoryRange;

pub fn minute_bucket(ts_utc: i64) -> i64 {
    (ts_utc / 60) * 60
}

/// Computes the UTC Unix epoch seconds for local midnight (00:00:00) of the date
/// corresponding to `now_utc` in the system's local timezone.
pub fn local_midnight_utc(now_utc: i64) -> i64 {
    days_ago_midnight_utc(now_utc, 0)
}

/// Computes the UTC Unix epoch seconds for local midnight (00:00:00) `days` calendar days
/// prior to the local date of `now_utc`. Correctly handles month/year rollbacks and DST
/// via POSIX `mktime` field normalization.
pub fn days_ago_midnight_utc(now_utc: i64, days: i32) -> i64 {
    let mut tm = std::mem::MaybeUninit::<libc::tm>::uninit();
    let sec = now_utc as libc::time_t;
    unsafe {
        libc::localtime_r(&sec, tm.as_mut_ptr());
        let mut tm = tm.assume_init();
        tm.tm_mday -= days;
        tm.tm_hour = 0;
        tm.tm_min = 0;
        tm.tm_sec = 0;
        // Setting tm_isdst to -1 instructs mktime to determine whether DST is in effect.
        tm.tm_isdst = -1;
        libc::mktime(&mut tm) as i64
    }
}

/// Returns the `[start_utc, end_utc)` bounds for a given `HistoryRange` at reference `now_utc`.
pub fn range_bounds(range: HistoryRange, now_utc: i64) -> (i64, i64) {
    let today_start = local_midnight_utc(now_utc);
    match range {
        HistoryRange::Today => (today_start, now_utc + 1),
        HistoryRange::Yesterday => {
            let yesterday_start = days_ago_midnight_utc(now_utc, 1);
            (yesterday_start, today_start)
        }
        HistoryRange::Last7Days => {
            // Trailing 7 calendar days: 6 calendar days ago at 00:00:00 through now
            let start = days_ago_midnight_utc(now_utc, 6);
            (start, now_utc + 1)
        }
        HistoryRange::Last30Days => {
            // Trailing 30 calendar days: 29 calendar days ago at 00:00:00 through now
            let start = days_ago_midnight_utc(now_utc, 29);
            (start, now_utc + 1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn minute_bucket_alignment() {
        assert_eq!(minute_bucket(1700000000), 1699999980);
        assert_eq!(minute_bucket(1700000060), 1700000040);
        assert_eq!(minute_bucket(120), 120);
        assert_eq!(minute_bucket(125), 120);
        assert_eq!(minute_bucket(179), 120);
        assert_eq!(minute_bucket(180), 180);
    }

    #[test]
    fn midnight_and_yesterday_partitioning() {
        let now = 1790616469; // 2026-09-29 01:27:49 (UTC+8)
        let today_mid = local_midnight_utc(now);
        let yest_mid = days_ago_midnight_utc(now, 1);

        assert!(today_mid <= now);
        assert!(yest_mid < today_mid);
        assert_eq!(today_mid - yest_mid, 86400);

        let (today_start, today_end) = range_bounds(HistoryRange::Today, now);
        let (yest_start, yest_end) = range_bounds(HistoryRange::Yesterday, now);

        // Yesterday does NOT include Today
        assert_eq!(yest_end, today_start);
        assert!(yest_start < yest_end);
        assert_eq!(today_start, today_mid);
        assert!(today_end > now);

        let (seven_start, seven_end) = range_bounds(HistoryRange::Last7Days, now);
        assert!(seven_start < yest_start);
        assert_eq!(seven_end, now + 1);

        let (thirty_start, thirty_end) = range_bounds(HistoryRange::Last30Days, now);
        assert!(thirty_start < seven_start);
        assert_eq!(thirty_end, now + 1);
    }
}
