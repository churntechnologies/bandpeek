use crate::model::OsCounters;

#[derive(Debug, PartialEq)]
pub enum Line {
    Header,
    Row {
        pid: i32,
        name: String,
        counters: OsCounters,
    },
    Invalid,
}

/// nettop has a repeated CSV header, but no end-of-sample marker. The next
/// header commits the preceding frame; EOF never commits a partial frame.
/// Parse the numeric suffix from the right: process names can contain dots/commas.
pub fn parse(line: &str) -> Line {
    let line = line.trim_end_matches(['\r', '\n']);
    if line == ",bytes_in,bytes_out," {
        return Line::Header;
    }
    let Some(line) = line.strip_suffix(',') else {
        return Line::Invalid;
    };
    let mut columns = line.rsplitn(3, ',');
    let upload = columns.next().and_then(|s| s.parse::<u64>().ok());
    let download = columns.next().and_then(|s| s.parse::<u64>().ok());
    let process = columns.next();
    if let (Some(upload), Some(download), Some(process)) = (upload, download, process) {
        if let Some((name, pid)) = process.rsplit_once('.') {
            if let Ok(pid) = pid.parse::<i32>() {
                // VPN teardown can introduce this kernel row into an existing
                // stream. It is valid CSV but has no verifiable app identity.
                if (pid > 0 || (pid == 0 && name == "kernel_task"))
                    && !name.is_empty()
                    && !name.starts_with(char::is_whitespace)
                {
                    return Line::Row {
                        pid,
                        name: name.into(),
                        counters: OsCounters { download, upload },
                    };
                }
            }
        }
    }
    Line::Invalid
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn actual_csv_and_names() {
        assert_eq!(parse(",bytes_in,bytes_out,\r\n"), Line::Header);
        assert_eq!(
            parse("a,b.helper.42,123,456,"),
            Line::Row {
                pid: 42,
                name: "a,b.helper".into(),
                counters: OsCounters {
                    download: 123,
                    upload: 456
                }
            }
        );
    }
    #[test]
    fn vpn_teardown_kernel_row_does_not_poison_a_complete_frame() {
        let frame = [
            ",bytes_in,bytes_out,\r\n",
            "kernel_task.0,0,0,\r\n",
            "Python.42,136609675,118,\r\n",
        ];
        assert!(frame.iter().all(|line| parse(line) != Line::Invalid));
        assert!(matches!(parse(frame[1]), Line::Row { pid: 0, .. }));
        assert_eq!(parse("kernel_task.0,-1,0,"), Line::Invalid);
    }
    #[test]
    fn malformed_partial_connection_negative_and_overflow() {
        for s in [
            "x.1,2,",
            "x.1,-2,3,",
            " tcp4 1.2.3.4:443,2,3,",
            "x.1,18446744073709551616,3,",
            ",bytes_out,bytes_in,",
            "x.0,1,2,",
        ] {
            assert_eq!(parse(s), Line::Invalid, "{s}");
        }
    }
}
