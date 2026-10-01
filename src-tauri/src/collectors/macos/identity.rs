use crate::{
    app_icon::outer_bundle,
    model::{AppIdentity, ProcessId},
};
use std::{
    collections::HashMap,
    ffi::CStr,
    path::{Path, PathBuf},
};

#[link(name = "proc")]
unsafe extern "C" {
    fn bandpeek_process_birth(pid: i32) -> u64;
    fn proc_pidpath(pid: i32, buffer: *mut libc::c_void, size: u32) -> i32;
}
fn process_birth(pid: i32) -> Option<u64> {
    let birth = unsafe { bandpeek_process_birth(pid) };
    (birth > 0).then_some(birth)
}

#[derive(Default)]
pub struct Resolver {
    processes: HashMap<ProcessId, AppIdentity>,
    bundles: HashMap<PathBuf, AppIdentity>,
}
impl Resolver {
    pub fn resolve(&mut self, pid: i32, sample_wall_us: u64) -> Option<(ProcessId, AppIdentity)> {
        if pid <= 0 {
            return None; // Kernel rows never acquire a fabricated PID-only identity.
        }
        let start_us = process_birth(pid)?;
        // A PID born after this snapshot cannot safely identify its row.
        if start_us > sample_wall_us {
            return None;
        }
        let id = ProcessId { pid, start_us };
        if let Some(app) = self.processes.get(&id) {
            return Some((id, app.clone()));
        }
        let mut buffer = [0u8; 4096];
        let length = unsafe { proc_pidpath(pid, buffer.as_mut_ptr().cast(), buffer.len() as u32) };
        let mut app = AppIdentity::default();
        if length > 0 {
            let executable = unsafe { CStr::from_ptr(buffer.as_ptr().cast()) }
                .to_string_lossy()
                .into_owned();
            if let Some(bundle) = outer_bundle(Path::new(&executable)) {
                if self.bundles.len() >= 256 {
                    self.bundles.clear();
                }
                app = self
                    .bundles
                    .entry(bundle.clone())
                    .or_insert_with(|| read_bundle(&bundle))
                    .clone();
            }
            app.executable_path = Some(executable);
        }
        // Validate around the path lookup as well (PID reuse during resolution).
        if process_birth(pid) != Some(start_us) {
            return None;
        }
        self.processes.insert(id.clone(), app.clone());
        Some((id, app))
    }
    pub fn retain(&mut self, ids: &std::collections::HashSet<ProcessId>) {
        self.processes.retain(|id, _| ids.contains(id));
    }
}
fn read_bundle(path: &Path) -> AppIdentity {
    let mut app = AppIdentity::default();
    let value = plist::Value::from_file(path.join("Contents/Info.plist")).ok();
    let dict = value.as_ref().and_then(plist::Value::as_dictionary);
    let get = |key: &str| {
        dict.and_then(|d| d.get(key))
            .and_then(plist::Value::as_string)
            .map(str::to_owned)
    };
    app.bundle_id = get("CFBundleIdentifier");
    // Use the installed application label (Finder name). A packaged tool whose
    // bundle is named after its executable uses its declared name instead.
    let label = path.file_stem().map(|x| x.to_string_lossy().into_owned());
    let declared = get("CFBundleDisplayName").or_else(|| get("CFBundleName"));
    app.application_name = crate::presentation::declared_bundle_name(
        label.as_deref(),
        get("CFBundleExecutable").as_deref(),
        declared.as_deref(),
        app.bundle_id.as_deref(),
    )
    .or(label)
    .or(declared);
    // Known bundle-ID normalization.
    if app.bundle_id.as_deref() == Some("com.openai.codex") {
        app.application_name = Some("Codex".into());
    }
    if let Some(icon) = get("CFBundleIconFile") {
        let mut icon = path.join("Contents/Resources").join(icon);
        if icon.extension().is_none() {
            icon.set_extension("icns");
        }
        if icon.is_file() {
            app.icon_path = Some(icon.to_string_lossy().into_owned());
        }
    }
    app
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn helper_maps_to_outer_application() {
        assert_eq!(outer_bundle(Path::new("/Applications/Google Chrome.app/Contents/Frameworks/Helper.app/Contents/MacOS/Helper")), Some(PathBuf::from("/Applications/Google Chrome.app")));
        assert!(outer_bundle(Path::new("/usr/bin/curl")).is_none());
    }
    fn fake_bundle(dir: &Path, folder: &str, plist: &[(&str, &str)]) -> PathBuf {
        let bundle = dir.join(folder);
        std::fs::create_dir_all(bundle.join("Contents")).unwrap();
        let mut dict = plist::Dictionary::new();
        for (k, v) in plist {
            dict.insert((*k).into(), plist::Value::String((*v).into()));
        }
        plist::Value::Dictionary(dict)
            .to_file_xml(bundle.join("Contents/Info.plist"))
            .unwrap();
        bundle
    }
    #[test]
    fn packaged_tool_uses_declared_bundle_name() {
        let dir = std::env::temp_dir().join(format!("bandpeek_bundles_{}", std::process::id()));
        let tool = fake_bundle(
            &dir,
            "claude.app",
            &[
                ("CFBundleExecutable", "claude"),
                ("CFBundleName", "Claude Code"),
                ("CFBundleIdentifier", "com.anthropic.claude-code"),
            ],
        );
        let app = read_bundle(&tool);
        assert_eq!(app.application_name.as_deref(), Some("Claude Code"));
        assert_eq!(app.bundle_id.as_deref(), Some("com.anthropic.claude-code"));
        let desktop = fake_bundle(
            &dir,
            "Claude.app",
            &[
                ("CFBundleExecutable", "Claude"),
                ("CFBundleDisplayName", "Claude"),
                ("CFBundleIdentifier", "com.anthropic.claudefordesktop"),
            ],
        );
        assert_eq!(
            read_bundle(&desktop).application_name.as_deref(),
            Some("Claude")
        );
        // Finder label wins for normal applications.
        let renamed = fake_bundle(
            &dir,
            "Visual Studio Code.app",
            &[("CFBundleExecutable", "Electron"), ("CFBundleName", "Code")],
        );
        assert_eq!(
            read_bundle(&renamed).application_name.as_deref(),
            Some("Visual Studio Code")
        );
        let _ = std::fs::remove_dir_all(dir);
    }
    #[test]
    fn public_api_returns_own_birth() {
        assert!(process_birth(std::process::id() as i32).is_some());
    }
}
