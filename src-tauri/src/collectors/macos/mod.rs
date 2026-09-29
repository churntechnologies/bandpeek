mod identity;
pub mod parser;

use super::{Collector, SharedSnapshot};
use crate::{aggregate::Aggregator, model::Observation};
use identity::Resolver;
use std::{
    collections::HashSet,
    fs::File,
    io::{self, Read},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::process::CommandExt,
    },
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::{SystemTime, UNIX_EPOCH},
};

pub const INTERVAL: u64 = crate::model::SAMPLE_INTERVAL_SECONDS;
pub struct MacosCollector {
    pub interval: u64,
    pub history: Option<crate::collectors::SharedHistory>,
    pub live_subscribers: crate::collectors::LiveSubscribers,
}

// Unlike Instant on Darwin, this clock includes time spent asleep. A long
// suspend must invalidate a pending frame before queued output is consumed.
fn continuous_ms() -> u64 {
    unsafe extern "C" {
        fn bandpeek_continuous_ms() -> u64;
    }
    unsafe { bandpeek_continuous_ms() }
}
fn stalled(now_ms: u64, last_ms: u64, interval: u64) -> bool {
    now_ms.saturating_sub(last_ms) > interval * 4 * 1000
}

fn wall_us() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_micros() as u64
}

struct Nettop {
    child: Child,
    stream: File,
}
impl Nettop {
    fn spawn(interval: u64) -> io::Result<Self> {
        let (mut master, mut slave) = (-1, -1);
        // PTY only for stdout: nettop otherwise block-buffers its CSV in a pipe.
        if unsafe {
            libc::openpty(
                &mut master,
                &mut slave,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        } != 0
        {
            return Err(io::Error::last_os_error());
        }
        let stream = unsafe { File::from_raw_fd(master) };
        let output = unsafe { File::from_raw_fd(slave) };
        unsafe {
            libc::fcntl(master, libc::F_SETFD, libc::FD_CLOEXEC);
        }
        let mut command = Command::new("/usr/bin/nettop");
        command
            .args([
                "-P",
                "-L",
                "0",
                "-s",
                &interval.to_string(),
                "-n",
                "-x",
                "-J",
                "bytes_in,bytes_out",
            ])
            .env("LC_ALL", "C")
            // Keep the parent's ChildStdin handle open without writing. On macOS
            // 27, stdin=/dev/null makes nettop spin on EOF (~145% CPU).
            // An idle pipe avoids that; -c alone does not fix the spin.
            .stdin(Stdio::piped())
            .stdout(output)
            .stderr(Stdio::piped());
        // If BandPeek dies abruptly (crash, SIGKILL) the idle stdin pipe reaches EOF
        // and an orphaned nettop spins at ~140% CPU forever (observed after
        // Milestone 3). Make the output PTY nettop's controlling terminal: when
        // our master side closes for any reason, the kernel hangs up the
        // terminal and SIGHUPs nettop. Runs in the child after stdio setup.
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 || libc::ioctl(1, libc::TIOCSCTTY.into(), 0) == -1 {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = command.spawn()?;
        Ok(Self { child, stream })
    }
}
impl Drop for Nettop {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
struct Frame {
    rows: Vec<Observation>,
    ids: HashSet<i32>,
    invalid: bool,
    at_ms: u64,
    wall_us: u64,
    unresolved: u64,
    raw: Vec<serde_json::Value>,
}
fn publish(
    aggregate: &Aggregator,
    output: &SharedSnapshot,
    subscribers: &crate::collectors::LiveSubscribers,
) {
    *output.lock().unwrap_or_else(|p| p.into_inner()) = aggregate.view();
    let rates = aggregate.snapshot.live_rates();
    subscribers
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .retain(|s| s.send(rates.clone()).is_ok());
}
impl Collector for MacosCollector {
    fn run(self, output: SharedSnapshot, stop: Arc<AtomicBool>) {
        let start = continuous_ms();
        let interval = self.interval;
        let previous = output.lock().unwrap_or_else(|p| p.into_inner()).clone();
        let mut aggregate = Aggregator::resume_from(previous, wall_us());
        aggregate.snapshot.sampling_interval_seconds = interval;
        let mut backoff = 1u64;
        while !stop.load(Ordering::Relaxed) {
            aggregate.restart(wall_us());
            aggregate.snapshot.collector_generation += 1;
            let result = match Nettop::spawn(interval) {
                Ok(mut process) => {
                    aggregate.snapshot.collector_pid = Some(process.child.id());
                    aggregate.snapshot.status =
                        "Waiting for baseline (one sample of framing latency)".into();
                    publish(&aggregate, &output, &self.live_subscribers);
                    let before = aggregate.snapshot.sample_sequence;
                    let result =
                        collect(&mut process, &mut aggregate, &output, &stop, start, &self);
                    if aggregate.snapshot.sample_sequence > before {
                        backoff = 1;
                    }
                    result
                }
                Err(error) => Err(error.to_string()),
            };
            aggregate.snapshot.collector_pid = None;
            aggregate.restart(wall_us());
            if stop.load(Ordering::Relaxed) {
                break;
            }
            let gap_err = result
                .as_ref()
                .err()
                .cloned()
                .unwrap_or_else(|| "stream ended".into());
            if let Some(ref history) = self.history {
                if let Ok(mut store) = history.lock() {
                    let now_sec = (wall_us() / 1_000_000) as i64;
                    let _ = store.record_gap(
                        now_sec,
                        now_sec,
                        aggregate.snapshot.collector_generation,
                        &gap_err,
                    );
                    let _ = store.flush();
                }
            }
            aggregate.snapshot.status = format!("Collector gap: {}; retry in {backoff}s", gap_err);
            publish(&aggregate, &output, &self.live_subscribers);
            for _ in 0..backoff * 10 {
                if stop.load(Ordering::Relaxed) {
                    break;
                }
                thread::sleep(std::time::Duration::from_millis(100));
            }
            backoff = (backoff * 2).min(30);
        }
        if let Some(ref history) = self.history {
            if let Ok(mut store) = history.lock() {
                let _ = store.flush();
            }
        }
        aggregate.snapshot.status = "Stopped".into();
        publish(&aggregate, &output, &self.live_subscribers);
    }
}
fn collect(
    process: &mut Nettop,
    aggregate: &mut Aggregator,
    output: &SharedSnapshot,
    stop: &AtomicBool,
    start: u64,
    collector: &MacosCollector,
) -> Result<(), String> {
    let interval = collector.interval;
    let history = collector.history.as_ref();
    let subscribers = &collector.live_subscribers;
    let mut resolver = Resolver::default();
    let mut pending = Vec::new();
    let mut frame: Option<Frame> = None;
    let mut last_header = continuous_ms();
    let mut buffer = [0u8; 8192];
    while !stop.load(Ordering::Relaxed) {
        if let Some(status) = process.child.try_wait().map_err(|e| e.to_string())? {
            let mut error = String::new();
            if let Some(stderr) = process.child.stderr.take() {
                let _ = stderr.take(4096).read_to_string(&mut error);
            }
            // Never commit the unterminated final frame, even on successful exit.
            return Err(format!("nettop exited {status}: {}", error.trim()));
        }
        if stalled(continuous_ms(), last_header, interval) {
            return Err("nettop stalled, system suspended, or unsupported format".into());
        }
        let mut fd = libc::pollfd {
            fd: process.stream.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        let ready = unsafe { libc::poll(&mut fd, 1, 500) };
        if ready < 0 {
            if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(io::Error::last_os_error().to_string());
        }
        // poll itself can span sleep; check again before consuming queued bytes.
        if stalled(continuous_ms(), last_header, interval) {
            return Err("nettop stalled, system suspended, or unsupported format".into());
        }
        if ready == 0 {
            continue;
        }
        let count = process
            .stream
            .read(&mut buffer)
            .map_err(|e| e.to_string())?;
        if count == 0 {
            return Err("nettop stream closed".into());
        }
        pending.extend_from_slice(&buffer[..count]);
        if pending.len() > 1_048_576 {
            return Err("nettop line exceeds size limit".into());
        }
        while let Some(end) = pending.iter().position(|b| *b == b'\n') {
            let bytes: Vec<u8> = pending.drain(..=end).collect();
            let line = std::str::from_utf8(&bytes)
                .map(parser::parse)
                .unwrap_or(parser::Line::Invalid);
            match line {
                parser::Line::Header => {
                    // Sleep can also interrupt parsing an already-read buffer.
                    // Check before refreshing the deadline or committing its frame.
                    let now = continuous_ms();
                    if stalled(now, last_header, interval) {
                        return Err(
                            "nettop stalled, system suspended, or unsupported format".into()
                        );
                    }
                    last_header = now;
                    if let Some(previous) = frame.take() {
                        aggregate.snapshot.unresolved_rows += previous.unresolved;
                        if previous.invalid {
                            aggregate.snapshot.rejected_samples += 1;
                            aggregate.snapshot.download_bytes_per_second = 0.0;
                            aggregate.snapshot.upload_bytes_per_second = 0.0;
                            aggregate.snapshot.status =
                                "Malformed sample discarded; waiting for recovery".into();
                        } else {
                            resolver.retain(&previous.rows.iter().map(|r| r.id.clone()).collect());
                            let before = aggregate.snapshot.clone();
                            let observations =
                                crate::diagnostics::enabled().then(|| previous.rows.clone());
                            aggregate.apply(previous.at_ms, previous.rows);
                            if let Some(observations) = observations {
                                crate::diagnostics::trace(serde_json::json!({
                                    "stage": "sample", "sequence": aggregate.snapshot.sample_sequence,
                                    "generation": aggregate.snapshot.collector_generation,
                                    "sample_ms": previous.at_ms, "published_ms": now.saturating_sub(start),
                                    "raw": previous.raw, "resolved": observations,
                                    "elapsed_ms": previous.at_ms.saturating_sub(before.sample_elapsed_ms),
                                    "delta_rx": aggregate.snapshot.session_bytes.download - before.session_bytes.download,
                                    "delta_tx": aggregate.snapshot.session_bytes.upload - before.session_bytes.upload,
                                    "rx_bps": aggregate.snapshot.download_bytes_per_second,
                                    "tx_bps": aggregate.snapshot.upload_bytes_per_second,
                                }));
                            }
                            let deltas = aggregate.drain_deltas();
                            if let Some(history) = history {
                                if !deltas.is_empty() {
                                    let ts_sec = (previous.wall_us / 1_000_000) as i64;
                                    if let Ok(mut store) = history.lock() {
                                        let _ = store.record_deltas(ts_sec, &deltas);
                                    }
                                }
                            }
                            aggregate.snapshot.status = format!(
                                "Tracking · TCP/UDP · all interfaces · ~{interval}s display delay"
                            );
                        }
                        publish(aggregate, output, subscribers);
                    }
                    frame = Some(Frame {
                        at_ms: continuous_ms().saturating_sub(start),
                        wall_us: wall_us(),
                        ..Frame::default()
                    });
                }
                parser::Line::Row {
                    pid,
                    name,
                    counters,
                } => {
                    if let Some(frame) = frame.as_mut() {
                        if crate::diagnostics::enabled() {
                            frame.raw.push(serde_json::json!({"pid":pid,"name":name,"rx":counters.download,"tx":counters.upload}));
                        }
                        if !frame.ids.insert(pid) || frame.ids.len() >= 16384 {
                            frame.invalid = true;
                            continue;
                        }
                        if let Some((id, app)) = resolver.resolve(pid, frame.wall_us) {
                            frame.rows.push(Observation {
                                id,
                                app,
                                process_name: name,
                                counters,
                            });
                        } else {
                            frame.unresolved += 1;
                        }
                    }
                }
                parser::Line::Invalid => {
                    if let Some(frame) = frame.as_mut() {
                        frame.invalid = true;
                    }
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;
    #[test]
    fn publication_notifies_gaps_even_with_unchanged_sequence_and_removes_dead_receivers() {
        let mut aggregate = Aggregator::new(0);
        let output = Arc::new(std::sync::Mutex::new(crate::model::Snapshot::default()));
        let (sender, receiver) = std::sync::mpsc::channel();
        let subscribers = Arc::new(std::sync::Mutex::new(vec![sender]));
        publish(&aggregate, &output, &subscribers);
        assert_eq!(receiver.recv().unwrap().sample_sequence, 0);
        aggregate.snapshot.status = "Collector gap: test".into();
        publish(&aggregate, &output, &subscribers);
        assert!(matches!(
            receiver.recv().unwrap().state,
            crate::model::TrackingState::Gap
        ));
        drop(receiver);
        publish(&aggregate, &output, &subscribers);
        assert!(subscribers.lock().unwrap().is_empty());
    }

    #[test]
    fn suspend_or_stall_expires_before_reading_queued_frames() {
        assert!(!stalled(20_000, 0, 5));
        assert!(stalled(20_001, 0, 5));
        assert!(stalled(3_600_000, 0, 5));
        assert!(stalled(8_001, 0, 2));
        assert!(continuous_ms() > 0);
    }
}
