//! Normalized public macOS interface + primary IPv4/IPv6 path state.
use std::{collections::HashSet, ffi::CStr};

const PROBE_MS: u64 = 1000;
const QUIET_MS: u64 = 1000;
// Let a new child establish and publish its baseline plus a live sample before
// another restart. Continuous notification bursts cannot churn child processes.
const MIN_RESTART_MS: u64 = 6000;

fn meaningful(rows: &[String]) -> Vec<String> {
    let routed: HashSet<_> = rows
        .iter()
        .filter_map(|r| {
            let parts: Vec<_> = r.split('|').collect();
            (parts.len() == 6 && parts[3] != "18").then_some(parts[0])
        })
        .collect();
    rows.iter()
        .filter(|r| {
            let name = r.split('|').next().unwrap_or_default();
            // Wi-Fi peer discovery is not an Internet path. Link counters and
            // transient flags were already excluded by the native adapter.
            if ["awdl", "llw", "ap", "nan"]
                .iter()
                .any(|p| name.starts_with(p))
            {
                return false;
            }
            name.starts_with("path") || name.starts_with("utun") || routed.contains(name)
        })
        .cloned()
        .collect()
}

#[derive(Default)]
pub struct Watcher {
    last_probe: Option<u64>,
    last_raw: Option<Vec<String>>,
    accepted: Option<Vec<String>>,
    pending: Option<(Vec<String>, u64)>,
    last_restart: Option<u64>,
}
impl Watcher {
    pub fn poll(&mut self, now: u64, generation: u64, pid: u32) -> bool {
        if self
            .last_probe
            .is_some_and(|t| now.saturating_sub(t) < PROBE_MS)
        {
            return false;
        }
        self.last_probe = Some(now);
        let Some(raw) = snapshot() else { return false };
        if self.last_raw.as_ref() != Some(&raw) {
            if crate::diagnostics::enabled() {
                crate::diagnostics::trace(serde_json::json!({"stage":"topology",
                    "generation":generation,"pid":pid,"alive":true,"interfaces":raw}));
            }
            self.last_raw = Some(raw.clone());
        }
        self.observe(meaningful(&raw), now)
    }

    fn observe(&mut self, state: Vec<String>, now: u64) -> bool {
        let Some(accepted) = &self.accepted else {
            self.accepted = Some(state);
            return false;
        };
        if accepted == &state {
            self.pending = None;
            return false;
        }
        match &self.pending {
            Some((pending, at)) if pending == &state => {
                if now.saturating_sub(*at) < QUIET_MS
                    || self
                        .last_restart
                        .is_some_and(|t| now.saturating_sub(t) < MIN_RESTART_MS)
                {
                    return false;
                }
                self.accepted = Some(state);
                self.pending = None;
                self.last_restart = Some(now);
                true
            }
            _ => {
                self.pending = Some((state, now));
                false
            }
        }
    }
}

pub fn snapshot() -> Option<Vec<String>> {
    unsafe extern "C" {
        fn bandpeek_network_topology() -> *mut libc::c_char;
    }
    let ptr = unsafe { bandpeek_network_topology() };
    if ptr.is_null() {
        return None;
    }
    let text = unsafe { CStr::from_ptr(ptr) }
        .to_string_lossy()
        .into_owned();
    unsafe { libc::free(ptr.cast()) };
    let mut rows: Vec<_> = text.lines().map(str::to_owned).collect();
    rows.sort_unstable();
    rows.dedup();
    Some(rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn state(value: &str) -> Vec<String> {
        vec![value.into()]
    }

    #[test]
    fn ten_tunnel_cycles_debounce_once_per_transition_and_idle_does_not_restart() {
        let mut w = Watcher::default();
        assert!(!w.observe(state("wifi"), 0));
        let mut now = 1000;
        for _ in 0..10 {
            for network in ["vpn", "wifi"] {
                assert!(!w.observe(state(network), now));
                assert!(!w.observe(state(network), now + 999));
                assert!(w.observe(state(network), now + 1000));
                for tick in 1..=10 {
                    assert!(!w.observe(state(network), now + 1000 + tick * 1000));
                }
                now += 12_000;
            }
        }
    }

    #[test]
    fn bursts_return_to_original_and_cooldown_do_not_churn() {
        let mut w = Watcher::default();
        assert!(!w.observe(state("wifi"), 0));
        assert!(!w.observe(state("vpn"), 1000));
        assert!(!w.observe(state("wifi"), 2000));
        assert!(!w.observe(state("wifi"), 3000));
        assert!(!w.observe(state("vpn"), 4000));
        assert!(!w.observe(state("vpn-address"), 4500));
        assert!(!w.observe(state("vpn-address"), 5000));
        assert!(w.observe(state("vpn-address"), 5500));
        assert!(!w.observe(state("wifi"), 6000));
        assert!(!w.observe(state("wifi"), 7000));
        assert!(w.observe(state("wifi"), 11_500));
    }

    #[test]
    fn discovery_churn_ignored_but_addressless_tunnels_and_paths_are_detected() {
        let base = vec![
            "en0|14|65|18||".into(),
            "en0|14|65|2|192.0.2.2|255.255.255.0".into(),
            "awdl0|16|65|18||".into(),
            "path0.0|en0".into(),
        ];
        let mut changed = base.clone();
        changed[2] = "awdl0|16|0|18||".into();
        changed.push("ap1|13|65|18||".into());
        assert_eq!(meaningful(&base), meaningful(&changed));
        changed.push("utun6|25|81|18||".into());
        assert_ne!(meaningful(&base), meaningful(&changed));
        changed.pop();
        changed[3] = "path0.0|utun6".into();
        assert_ne!(meaningful(&base), meaningful(&changed));
        changed = base.clone();
        changed[0] = "en0|14|0|18||".into();
        assert_ne!(meaningful(&base), meaningful(&changed));
    }
}
