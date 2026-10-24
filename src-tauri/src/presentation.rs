//! Human-facing names for stored application identities.
//!
//! Presentation only: this never changes `identity_key` or anything persisted,
//! so improving a label cannot split or merge history. Every rule is keyed on
//! evidence already stored for the row (bundle identifier, executable path,
//! and for WebKit Networking the name macOS gave that helper instance);
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
    /// Application a host-named helper instance works for ("Safari" for
    /// "Safari Networking"). Its traffic stays on the helper's own row.
    pub host_app: Option<String>,
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

/// WebKit runs one Networking process per embedding application, and macOS
/// names each instance after its app ("Safari Networking"). Identities built
/// from that name use this key prefix (see `AppIdentity::stable_key`).
pub const HOST_NAMED_HELPER: &str = "com.apple.WebKit.Networking";
pub const HOST_HELPER_KEY_PREFIX: &str = "helper:";
const HOST_HELPER_SUFFIX: &str = " Networking";

/// Whether `executable_path` is the system WebKit Networking helper, whose
/// instance name is worth asking for.
pub fn is_host_named_helper(executable_path: &str) -> bool {
    is_system_path(executable_path) && file_name(executable_path) == Some(HOST_NAMED_HELPER)
}

/// The host application in a WebKit Networking instance name: "Safari
/// Networking" → "Safari". Anything else (no name, the generic or a localized
/// name, a lookalike executable) is `None` and the shared row is kept.
pub fn networking_host(executable_path: &str, instance_name: &str) -> Option<String> {
    if !is_host_named_helper(executable_path) || instance_name.trim() != instance_name {
        return None;
    }
    let host = instance_name.strip_suffix(HOST_HELPER_SUFFIX)?;
    let valid = !host.trim().is_empty()
        && host.trim() == host
        && host != "WebKit"
        && host.chars().count() <= 100
        && !host.chars().any(char::is_control);
    valid.then(|| host.to_string())
}

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
    if identity_key.starts_with(HOST_HELPER_KEY_PREFIX) {
        let name = stored_name.trim();
        if let Some(host) = executable_path.and_then(|path| networking_host(path, name)) {
            return Presentation {
                display_name: name.into(),
                kind: AppKind::SharedSystemProcess,
                host_app: Some(host),
            };
        }
    }
    if identity_key.starts_with("bundle:") {
        let bundle = identity_key.trim_start_matches("bundle:");
        let bundle = bundle_id.unwrap_or(bundle);
        if let Some((_, label)) = CLI_BUNDLES.iter().find(|(id, _)| *id == bundle) {
            return Presentation {
                display_name: (*label).into(),
                kind: AppKind::CommandLineTool,
                host_app: None,
            };
        }
        return Presentation {
            display_name: stored(),
            kind: AppKind::Application,
            host_app: None,
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
                    host_app: None,
                };
            }
            return Presentation {
                display_name: readable_apple_identifier(name).unwrap_or_else(stored),
                kind: AppKind::SystemProcess,
                host_app: None,
            };
        }
        return Presentation {
            display_name: stored(),
            kind: AppKind::CommandLineTool,
            host_app: None,
        };
    }
    Presentation {
        display_name: stored(),
        kind: AppKind::Process,
        host_app: None,
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

    fn helper(path: &str, stored: &str) -> Presentation {
        present(&format!("helper:{stored}"), stored, None, Some(path))
    }

    #[test]
    fn webkit_networking_named_after_its_app_is_its_own_shared_row() {
        for path in [WEBKIT_NETWORKING, CRYPTEX_NETWORKING] {
            for (name, host) in [
                ("Safari Networking", "Safari"),
                ("Mail Networking", "Mail"),
                ("BandPeek Networking", "BandPeek"),
            ] {
                let p = helper(path, name);
                assert_eq!(p.display_name, name);
                // Still the shared helper, never the host application itself.
                assert_eq!(p.kind, AppKind::SharedSystemProcess);
                assert_eq!(p.host_app.as_deref(), Some(host));
            }
        }
        // The unnamed row keeps today's label and claims no host.
        let shared = exec(WEBKIT_NETWORKING, "com.apple.WebKit.Networking");
        assert_eq!(shared.display_name, "WebKit Networking");
        assert_eq!(shared.host_app, None);
    }

    #[test]
    fn networking_host_never_guesses() {
        assert_eq!(
            networking_host(WEBKIT_NETWORKING, "Safari Networking").as_deref(),
            Some("Safari")
        );
        assert_eq!(
            networking_host(WEBKIT_NETWORKING, "Safari Technology Preview Networking").as_deref(),
            Some("Safari Technology Preview")
        );
        for name in [
            "",
            "Networking",
            " Networking",
            "WebKit Networking",
            "com.apple.WebKit.Networking",
            "Safari",
            "Safari Netzwerk",
            "Safari Networking ",
            " Safari Networking",
            "Safari  Networking",
            "Safari Web Content",
            "Safari\u{7} Networking",
        ] {
            assert_eq!(networking_host(WEBKIT_NETWORKING, name), None, "{name:?}");
        }
        // Only the system WebKit Networking executable carries a host name.
        for path in [
            "/Users/someone/bin/com.apple.WebKit.Networking",
            "/System/Library/Frameworks/WebKit.framework/Versions/A/XPCServices/com.apple.WebKit.WebContent.xpc/Contents/MacOS/com.apple.WebKit.WebContent",
            "/usr/sbin/mDNSResponder",
        ] {
            assert_eq!(networking_host(path, "Safari Networking"), None, "{path}");
        }
    }

    #[test]
    fn unusable_host_named_rows_fall_back_to_the_shared_row() {
        let odd = helper(WEBKIT_NETWORKING, "Safari Netzwerk");
        assert_eq!(odd.display_name, "WebKit Networking");
        assert_eq!(
            (odd.kind, odd.host_app),
            (AppKind::SharedSystemProcess, None)
        );
        let fake = helper(
            "/Users/someone/bin/com.apple.WebKit.Networking",
            "Safari Networking",
        );
        assert_eq!((fake.kind, fake.host_app), (AppKind::CommandLineTool, None));
        let pathless = present("helper:Safari Networking", "Safari Networking", None, None);
        assert_eq!((pathless.kind, pathless.host_app), (AppKind::Process, None));
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
