//! Platform-independent part of application icon lookup: which bundle, if any,
//! a persisted application row can be drawn from. Rendering lives in the shell.
use std::path::{Path, PathBuf};

/// Outermost `.app` containing `path`. Helpers nested inside an application
/// (Chromium/Electron helpers, XPC services) resolve to the user-facing app.
pub fn outer_bundle(path: &Path) -> Option<PathBuf> {
    path.ancestors()
        .filter(|p| p.extension().is_some_and(|x| x == "app"))
        .last()
        .map(Path::to_path_buf)
}

/// Candidate bundle for an application row, derived only from stored paths.
/// CLI tools and daemons outside any bundle return `None`; the UI then shows
/// its neutral fallback rather than a generic executable icon.
pub fn bundle_for(executable_path: Option<&str>, icon_path: Option<&str>) -> Option<PathBuf> {
    executable_path
        .and_then(|p| outer_bundle(Path::new(p)))
        .or_else(|| icon_path.and_then(|p| outer_bundle(Path::new(p))))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_outer_application_bundle() {
        assert_eq!(
            bundle_for(
                Some("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper"),
                None
            ),
            Some(PathBuf::from("/Applications/Google Chrome.app"))
        );
        assert_eq!(
            bundle_for(
                None,
                Some("/Applications/Slack.app/Contents/Resources/electron.icns")
            ),
            Some(PathBuf::from("/Applications/Slack.app"))
        );
        assert_eq!(bundle_for(Some("/usr/bin/curl"), None), None);
        assert_eq!(bundle_for(None, None), None);
    }
}
