//! Human-facing names for stored application identities.
//!
//! Presentation only: this never changes `identity_key` or anything persisted,
//! so improving a label cannot split or merge history. Every rule is keyed on
//! evidence already stored for the row (bundle identifier, executable path);
//! nothing here attributes a process's traffic to a different application.
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AppKind {
    /// Identified by an application bundle identifier.
    Application,
    /// A command-line program (outside the macOS system locations).
    CommandLineTool,
    /// A macOS system daemon or service acting for itself.
    SystemProcess,
    /// A macOS helper that performs network work on behalf of other apps. Its
    /// traffic cannot be attributed to the app that asked for it.
    SharedSystemProcess,
    /// Only a process name was available.
    Process,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Presentation {
    pub display_name: String,
    pub kind: AppKind,
}

/// Command-line tools shipped inside an application-style bundle whose Finder
/// label is just the executable name. The label comes from the bundle's own
/// `CFBundleName`; the bundle identifier is the evidence.
const CLI_BUNDLES: &[(&str, &str)] = &[("com.anthropic.claude-code", "Claude Code")];

/// macOS helpers that carry network traffic for many client apps.
/// (executable name, label)
const SHARED_HELPERS: &[(&str, &str)] = &[
    ("com.apple.WebKit.Networking", "WebKit Networking"),
    ("com.apple.WebKit.WebContent", "WebKit Web Content"),
    ("com.apple.WebKit.GPU", "WebKit GPU"),
    ("nsurlsessiond", "nsurlsessiond"),
    ("mDNSResponder", "mDNSResponder"),
];

/// Locations where only the operating system installs executables.
fn is_system_path(path: &str) -> bool {
    const ROOTS: &[&str] = &[
        "/System/",
        "/usr/libexec/",
        "/usr/sbin/",
        "/sbin/",
        "/Library/Apple/",
    ];
    ROOTS.iter().any(|root| path.starts_with(root))
}

fn file_name(path: &str) -> Option<&str> {
    Path::new(path).file_name().and_then(|n| n.to_str())
}

/// `com.apple.Safari.SearchHelper` → `Safari SearchHelper`. Only used for
/// executables in system locations, whose names are Apple's own identifiers.
fn readable_apple_identifier(name: &str) -> Option<String> {
    let rest = name.strip_prefix("com.apple.")?;
    let words: Vec<&str> = rest.split('.').filter(|w| !w.is_empty()).collect();
    (!words.is_empty()).then(|| words.join(" "))
}

pub fn present(
    identity_key: &str,
    stored_name: &str,
    bundle_id: Option<&str>,
    executable_path: Option<&str>,
) -> Presentation {
    let stored = || stored_name.trim().to_string();
    if identity_key.starts_with("bundle:") {
        let bundle = identity_key.trim_start_matches("bundle:");
        let bundle = bundle_id.unwrap_or(bundle);
        if let Some((_, label)) = CLI_BUNDLES.iter().find(|(id, _)| *id == bundle) {
            return Presentation {
                display_name: (*label).into(),
                kind: AppKind::CommandLineTool,
            };
        }
        return Presentation {
            display_name: stored(),
            kind: AppKind::Application,
        };
    }
    if let Some(path) = identity_key
        .strip_prefix("exec:")
        .or(executable_path)
        .filter(|p| !p.is_empty())
    {
        let name = file_name(path).unwrap_or(stored_name);
        if is_system_path(path) {
            if let Some((_, label)) = SHARED_HELPERS.iter().find(|(exe, _)| *exe == name) {
                return Presentation {
                    display_name: (*label).into(),
                    kind: AppKind::SharedSystemProcess,
                };
            }
            return Presentation {
                display_name: readable_apple_identifier(name).unwrap_or_else(stored),
                kind: AppKind::SystemProcess,
            };
        }
        return Presentation {
            display_name: stored(),
            kind: AppKind::CommandLineTool,
        };
    }
    Presentation {
        display_name: stored(),
        kind: AppKind::Process,
    }
}

/// Resolver rule for new observations: a bundle folder named exactly like its
/// executable (`claude.app` → `claude`) is a packaged tool, not a Finder-facing
/// application name; prefer the bundle's declared name when it is a real name.
pub fn declared_bundle_name(
    finder_label: Option<&str>,
    executable: Option<&str>,
    declared: Option<&str>,
    bundle_id: Option<&str>,
) -> Option<String> {
    let label = finder_label?;
    let declared = declared.map(str::trim).filter(|d| !d.is_empty())?;
    let packaged_tool = executable == Some(label) && label != declared;
    // Reverse-DNS "names" (CFBundleName = bundle ID) are not an improvement.
    let real_name = Some(declared) != bundle_id && !declared.contains('.');
    (packaged_tool && real_name).then(|| declared.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    const WEBKIT_NETWORKING: &str = "/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.Networking.xpc/Contents/MacOS/com.apple.WebKit.Networking";
    const CRYPTEX_NETWORKING: &str = "/System/Volumes/Preboot/Cryptexes/Incoming/OS/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.Networking.xpc/Contents/MacOS/com.apple.WebKit.Networking";

    fn exec(path: &str, stored: &str) -> Presentation {
        present(&format!("exec:{path}"), stored, None, Some(path))
    }

    #[test]
    fn claude_code_is_named_and_kept_apart_from_claude_desktop() {
        let code = present(
            "bundle:com.anthropic.claude-code",
            "claude",
            Some("com.anthropic.claude-code"),
            Some("/Users/someone/Library/Application Support/Claude/claude-code/2.1.281/claude.app/Contents/MacOS/claude"),
        );
        assert_eq!(code.display_name, "Claude Code");
        assert_eq!(code.kind, AppKind::CommandLineTool);
        let desktop = present(
            "bundle:com.anthropic.claudefordesktop",
            "Claude",
            Some("com.anthropic.claudefordesktop"),
            Some("/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper"),
        );
        assert_eq!(desktop.display_name, "Claude");
        assert_eq!(desktop.kind, AppKind::Application);
        assert_ne!(code.display_name, desktop.display_name);
    }

    #[test]
    fn webkit_networking_is_a_shared_helper_not_a_parent_app() {
        for path in [WEBKIT_NETWORKING, CRYPTEX_NETWORKING] {
            let p = exec(path, "com.apple.WebKit.Networking");
            assert_eq!(p.display_name, "WebKit Networking");
            assert_eq!(p.kind, AppKind::SharedSystemProcess);
            for app in ["Safari", "Claude", "ChatGPT"] {
                assert!(!p.display_name.contains(app));
            }
        }
        // Rows stored before the executable-name fix still carry nettop's
        // 15-character name; the stored path decides.
        assert_eq!(
            exec(WEBKIT_NETWORKING, "com.apple.WebKi").display_name,
            "WebKit Networking"
        );
    }

    #[test]
    fn shared_helper_names_require_a_system_location() {
        let fake = exec("/Users/someone/bin/com.apple.WebKit.Networking", "x");
        assert_eq!(fake.kind, AppKind::CommandLineTool);
        assert_eq!(fake.display_name, "x");
    }

    #[test]
    fn system_processes_keep_their_own_names() {
        let geod = exec("/System/Library/PrivateFrameworks/GeoServices.framework/Versions/A/XPCServices/com.apple.geod.xpc/Contents/MacOS/com.apple.geod", "com.apple.geod");
        assert_eq!(geod.display_name, "geod");
        assert_eq!(geod.kind, AppKind::SystemProcess);
        let search = exec("/System/Volumes/Preboot/Cryptexes/Incoming/OS/System/Library/PrivateFrameworks/SafariShared.framework/Versions/A/XPCServices/com.apple.Safari.SearchHelper.xpc/Contents/MacOS/com.apple.Safari.SearchHelper", "com.apple.Safari.SearchHelper");
        assert_eq!(search.display_name, "Safari SearchHelper");
        let rapportd = exec("/usr/libexec/rapportd", "rapportd");
        assert_eq!(rapportd.display_name, "rapportd");
        assert_eq!(rapportd.kind, AppKind::SystemProcess);
        let dns = exec("/usr/sbin/mDNSResponder", "mDNSResponder");
        assert_eq!(dns.kind, AppKind::SharedSystemProcess);
        assert_eq!(dns.display_name, "mDNSResponder");
    }

    #[test]
    fn command_line_tools_and_bare_processes() {
        assert_eq!(exec("/usr/bin/curl", "curl").kind, AppKind::CommandLineTool);
        assert_eq!(exec("/usr/bin/curl", "curl").display_name, "curl");
        let gh = exec("/opt/homebrew/Cellar/gh/2.98.0/bin/gh", "gh");
        assert_eq!(
            (gh.display_name.as_str(), gh.kind),
            ("gh", AppKind::CommandLineTool)
        );
        let bare = present("proc:someproc", "someproc", None, None);
        assert_eq!(
            (bare.display_name.as_str(), bare.kind),
            ("someproc", AppKind::Process)
        );
    }

    #[test]
    fn ordinary_applications_are_unchanged() {
        let chrome = present(
            "bundle:com.google.Chrome",
            "Google Chrome",
            Some("com.google.Chrome"),
            Some("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper"),
        );
        assert_eq!(chrome.display_name, "Google Chrome");
        assert_eq!(chrome.kind, AppKind::Application);
    }

    #[test]
    fn declared_name_only_replaces_executable_style_labels() {
        let d = |label, exe, declared, id| {
            declared_bundle_name(Some(label), Some(exe), Some(declared), Some(id))
        };
        assert_eq!(
            d(
                "claude",
                "claude",
                "Claude Code",
                "com.anthropic.claude-code"
            )
            .as_deref(),
            Some("Claude Code")
        );
        // Finder label differs from the executable: the label is user-facing.
        assert_eq!(
            d(
                "Google Chrome",
                "Google Chrome",
                "Google Chrome",
                "com.google.Chrome"
            ),
            None
        );
        assert_eq!(
            d(
                "Visual Studio Code",
                "Electron",
                "Code",
                "com.microsoft.VSCode"
            ),
            None
        );
        // Declared name is the bundle identifier: no improvement.
        assert_eq!(
            d(
                "com.apple.WebKit.Networking",
                "com.apple.WebKit.Networking",
                "com.apple.WebKit.Networking",
                "com.apple.WebKit.Networking"
            ),
            None
        );
        assert_eq!(
            declared_bundle_name(Some("tool"), Some("tool"), Some("  "), None),
            None
        );
        assert_eq!(
            declared_bundle_name(Some("tool"), Some("tool"), None, None),
            None
        );
    }
}
