//! Update lifecycle policy, shared by the real updater and deterministic tests.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Phase {
    #[default]
    Idle,
    Checking,
    Downloading,
    Ready,
    Installing,
    Failed,
}
#[derive(Default)]
pub struct Policy {
    pub phase: Phase,
    pub main_open: bool,
}
impl Policy {
    pub fn open_main(&mut self) -> bool {
        if self.phase == Phase::Installing {
            return false;
        }
        self.main_open = true;
        true
    }
    pub fn close_main(&mut self) {
        self.main_open = false;
    }
    pub fn begin_install(&mut self) -> bool {
        if self.phase != Phase::Ready || self.main_open {
            return false;
        }
        self.phase = Phase::Installing;
        true
    }
    pub fn begin_check(&mut self) -> bool {
        if !matches!(self.phase, Phase::Idle | Phase::Failed) {
            return false;
        }
        self.phase = Phase::Checking;
        true
    }
}

pub trait InstallHost {
    fn quiesce_and_flush(&mut self) -> Result<(), String>;
    fn install_verified(&mut self) -> Result<(), String>;
    fn resume_current(&mut self);
    fn relaunch(&mut self) -> Result<(), String>;
}
/// Collection must stop and SQLite must flush before installation or relaunch.
/// Errors keep the current process alive and restore its collector.
pub fn install_and_relaunch(host: &mut impl InstallHost) -> Result<(), String> {
    let result = host
        .quiesce_and_flush()
        .and_then(|_| host.install_verified())
        .and_then(|_| host.relaunch());
    if result.is_err() {
        host.resume_current();
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn verified_update_waits_for_a_closed_window_and_reserves_installation() {
        let mut p = Policy::default();
        assert!(p.open_main());
        assert!(p.begin_check());
        assert!(!p.begin_check());
        p.phase = Phase::Downloading;
        assert!(!p.begin_install());
        p.phase = Phase::Ready;
        assert!(!p.begin_install()); // includes minimized/in-use windows
        p.close_main();
        assert!(p.begin_install());
        assert!(!p.open_main()); // cannot race an installer
        assert!(!p.begin_install());
        p.phase = Phase::Failed;
        assert!(p.open_main());
        assert!(p.begin_check()); // next scheduled check can retry
    }
    struct Host {
        events: Vec<&'static str>,
        fail: Option<&'static str>,
    }
    impl Host {
        fn step(&mut self, name: &'static str) -> Result<(), String> {
            self.events.push(name);
            if self.fail == Some(name) {
                Err(name.into())
            } else {
                Ok(())
            }
        }
    }
    impl InstallHost for Host {
        fn quiesce_and_flush(&mut self) -> Result<(), String> {
            self.step("stop-reap-flush")
        }
        fn install_verified(&mut self) -> Result<(), String> {
            self.step("install")
        }
        fn resume_current(&mut self) {
            self.events.push("resume");
        }
        fn relaunch(&mut self) -> Result<(), String> {
            self.step("relaunch")
        }
    }
    #[test]
    fn flush_precedes_install_and_relaunch() {
        let mut h = Host {
            events: vec![],
            fail: None,
        };
        install_and_relaunch(&mut h).unwrap();
        assert_eq!(h.events, ["stop-reap-flush", "install", "relaunch"]);
    }
    #[test]
    fn failed_flush_cancels_install_and_resumes() {
        let mut h = Host {
            events: vec![],
            fail: Some("stop-reap-flush"),
        };
        assert!(install_and_relaunch(&mut h).is_err());
        assert_eq!(h.events, ["stop-reap-flush", "resume"]);
    }
    #[test]
    fn failed_install_does_not_relaunch_and_resumes() {
        let mut h = Host {
            events: vec![],
            fail: Some("install"),
        };
        assert!(install_and_relaunch(&mut h).is_err());
        assert_eq!(h.events, ["stop-reap-flush", "install", "resume"]);
    }
}
