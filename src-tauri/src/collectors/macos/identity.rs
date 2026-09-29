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
    // Use the installed application label, with a known bundle-ID normalization.
    app.application_name = path
        .file_stem()
        .map(|x| x.to_string_lossy().into_owned())
        .or_else(|| get("CFBundleDisplayName"))
        .or_else(|| get("CFBundleName"));
    app.bundle_id = get("CFBundleIdentifier");
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
    #[test]
    fn public_api_returns_own_birth() {
        assert!(process_birth(std::process::id() as i32).is_some());
    }
}
