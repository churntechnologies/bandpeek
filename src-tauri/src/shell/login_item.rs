//! Launch at Login through ServiceManagement's `SMAppService.mainAppService`
//! (macOS 13+): the app itself is registered as a login item. No launch agent
//! plist, helper, daemon, shell script or administrator rights. The system
//! stores the registration, and the user can also change it in System Settings
//! → General → Login Items, so BandPeek always reads the state back from the
//! system rather than keeping its own copy.
use objc2::{class, msg_send, rc::Retained, runtime::AnyObject};
use objc2_foundation::{NSAppleEventDescriptor, NSAppleEventManager, NSError};
use serde::Serialize;

#[link(name = "ServiceManagement", kind = "framework")]
unsafe extern "C" {}

/// `SMAppServiceStatus`.
const NOT_REGISTERED: isize = 0;
const ENABLED: isize = 1;
const REQUIRES_APPROVAL: isize = 2;

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LoginItemState {
    Enabled,
    Disabled,
    /// Registered, but the user must allow it in System Settings.
    RequiresApproval,
    /// Not running from an installed `.app` bundle (development binary).
    Unavailable,
}

#[derive(Clone, Debug, Serialize)]
pub struct LoginItem {
    pub state: LoginItemState,
    pub error: Option<String>,
}

fn running_from_app_bundle() -> bool {
    std::env::current_exe()
        .ok()
        .and_then(|exe| bandpeek_core::app_icon::outer_bundle(&exe))
        .is_some()
}

fn main_app_service() -> Option<Retained<AnyObject>> {
    if !running_from_app_bundle() {
        return None;
    }
    unsafe { msg_send![class!(SMAppService), mainAppService] }
}

fn state_of(service: &AnyObject) -> LoginItemState {
    let status: isize = unsafe { msg_send![service, status] };
    match status {
        ENABLED => LoginItemState::Enabled,
        REQUIRES_APPROVAL => LoginItemState::RequiresApproval,
        NOT_REGISTERED => LoginItemState::Disabled,
        // NotFound: the system cannot see this copy of the app as registered.
        _ => LoginItemState::Disabled,
    }
}

pub fn status() -> LoginItem {
    LoginItem {
        state: main_app_service()
            .map(|s| state_of(&s))
            .unwrap_or(LoginItemState::Unavailable),
        error: None,
    }
}

pub fn set_enabled(enabled: bool) -> LoginItem {
    let Some(service) = main_app_service() else {
        return status();
    };
    let current = state_of(&service);
    let wanted = if enabled {
        current == LoginItemState::Disabled
    } else {
        current != LoginItemState::Disabled
    };
    let mut error: *mut NSError = std::ptr::null_mut();
    let ok: bool = if !wanted {
        true
    } else if enabled {
        unsafe { msg_send![&*service, registerAndReturnError: &mut error] }
    } else {
        unsafe { msg_send![&*service, unregisterAndReturnError: &mut error] }
    };
    let error = (!ok).then(|| {
        unsafe { error.as_ref() }
            .map(|e| e.localizedDescription().to_string())
            .unwrap_or_else(|| "The system did not accept the change.".into())
    });
    LoginItem {
        state: state_of(&service),
        error,
    }
}

/// Opens System Settings → General → Login Items.
pub fn open_system_settings() {
    let _: () = unsafe { msg_send![class!(SMAppService), openSystemSettingsLoginItems] };
}

fn event_id(event: &NSAppleEventDescriptor) -> u32 {
    unsafe { msg_send![event, eventID] }
}

fn param(event: &NSAppleEventDescriptor, keyword: u32) -> Option<Retained<NSAppleEventDescriptor>> {
    unsafe { msg_send![event, paramDescriptorForKeyword: keyword] }
}

const K_AE_OPEN_APPLICATION: u32 = u32::from_be_bytes(*b"oapp");
const KEY_AE_PROP_DATA: u32 = u32::from_be_bytes(*b"prdt");
const KEY_AE_LAUNCHED_AS_LOGIN_ITEM: u32 = u32::from_be_bytes(*b"lgit");

/// A login-item launch: the open-application Apple event carries
/// `keyAEPropData = keyAELaunchedAsLogInItem`. A normal launch (Finder, Dock,
/// Spotlight, `open`) has no such property.
fn is_login_item_launch(event: &NSAppleEventDescriptor) -> bool {
    event_id(event) == K_AE_OPEN_APPLICATION
        && param(event, KEY_AE_PROP_DATA)
            .is_some_and(|p| p.enumCodeValue() == KEY_AE_LAUNCHED_AS_LOGIN_ITEM)
}

/// True when macOS opened BandPeek as a login item. Valid while the launch
/// event is being handled (Tauri's setup hook runs inside it).
pub fn launched_as_login_item() -> bool {
    NSAppleEventManager::sharedAppleEventManager()
        .currentAppleEvent()
        .is_some_and(|event| is_login_item_launch(&event))
}

/// Validation: which launch event (if any) setup saw, as four-char codes.
pub fn launch_event_description() -> String {
    let fourcc = |v: u32| String::from_utf8_lossy(&v.to_be_bytes()).into_owned();
    match NSAppleEventManager::sharedAppleEventManager().currentAppleEvent() {
        None => "none".into(),
        Some(event) => {
            let prop = param(&event, KEY_AE_PROP_DATA)
                .map(|p| fourcc(p.enumCodeValue()))
                .unwrap_or_else(|| "-".into());
            format!("{} prop={prop}", fourcc(event_id(&event)))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(class: &[u8; 4], id: &[u8; 4]) -> Retained<NSAppleEventDescriptor> {
        let target = NSAppleEventDescriptor::nullDescriptor();
        unsafe {
            msg_send![
                class!(NSAppleEventDescriptor),
                appleEventWithEventClass: u32::from_be_bytes(*class),
                eventID: u32::from_be_bytes(*id),
                targetDescriptor: &*target,
                returnID: -1i16,
                transactionID: 0i32
            ]
        }
    }

    fn with_prop(event: &NSAppleEventDescriptor, code: &[u8; 4]) {
        let value: Retained<NSAppleEventDescriptor> = unsafe {
            msg_send![class!(NSAppleEventDescriptor), descriptorWithEnumCode: u32::from_be_bytes(*code)]
        };
        let _: () =
            unsafe { msg_send![event, setParamDescriptor: &*value, forKeyword: KEY_AE_PROP_DATA] };
    }

    #[test]
    fn only_the_login_item_open_event_starts_in_the_menu_bar() {
        let login = event(b"aevt", b"oapp");
        with_prop(&login, b"lgit");
        assert!(is_login_item_launch(&login));

        // Finder, Dock, Spotlight and `open` send a plain open-application event.
        assert!(!is_login_item_launch(&event(b"aevt", b"oapp")));

        // Some other launch property.
        let other = event(b"aevt", b"oapp");
        with_prop(&other, b"xxxx");
        assert!(!is_login_item_launch(&other));

        // Reopen (Dock click on a running app) is never a login launch.
        let reopen = event(b"aevt", b"rapp");
        with_prop(&reopen, b"lgit");
        assert!(!is_login_item_launch(&reopen));
    }

    #[test]
    fn development_binary_reports_unavailable() {
        // `cargo test` runs outside any .app bundle.
        assert_eq!(status().state, LoginItemState::Unavailable);
        assert_eq!(set_enabled(true).state, LoginItemState::Unavailable);
    }
}
