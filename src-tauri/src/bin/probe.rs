use bandpeek_core::Monitor;
use std::time::{Duration, Instant};
fn main() {
    let seconds: u64 = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "60".into())
        .parse()
        .expect("duration in seconds");
    let interval = std::env::args()
        .nth(2)
        .map(|s| s.parse().expect("interval seconds"))
        .unwrap_or(5);
    let monitor = Monitor::start_with_interval(interval);
    let start = Instant::now();
    let mut last = (u64::MAX, String::new());
    while start.elapsed().as_secs() < seconds {
        let view = monitor.snapshot.lock().unwrap().clone();
        if (view.sample_sequence, view.status.clone()) != last {
            println!("{}", serde_json::to_string(&view).unwrap());
            last = (view.sample_sequence, view.status);
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}
