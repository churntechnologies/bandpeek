//! Public AppKit calls used by the desktop shell. No private API.
//! Every function here must run on the main thread.
use objc2::{msg_send, rc::Retained, runtime::AnyObject, AllocAnyThread, MainThreadMarker};
use objc2_app_kit::{
    NSBaselineOffsetAttributeName, NSBitmapImageFileType, NSBitmapImageRep, NSColor,
    NSCompositingOperation, NSDeviceRGBColorSpace, NSFont, NSFontAttributeName,
    NSFontWeightRegular, NSForegroundColorAttributeName, NSGraphicsContext, NSImageInterpolation,
    NSMutableParagraphStyle, NSParagraphStyleAttributeName, NSStatusItem, NSTextAlignment,
    NSWindow, NSWindowCollectionBehavior, NSWorkspace,
};
use objc2_foundation::{
    NSAttributedString, NSAttributedStringKey, NSDictionary, NSNumber, NSPoint, NSRect, NSSize,
    NSString,
};
use std::path::{Path, PathBuf};

/// Renders the Finder icon of an application bundle into a square PNG.
pub fn bundle_icon_png(bundle: &Path, pixels: isize) -> Option<Vec<u8>> {
    MainThreadMarker::new()?;
    if !bundle.is_dir() {
        return None;
    }
    let image = NSWorkspace::sharedWorkspace().iconForFile(&NSString::from_str(bundle.to_str()?));
    let rep = unsafe {
        NSBitmapImageRep::initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel(
            NSBitmapImageRep::alloc(),
            std::ptr::null_mut(),
            pixels,
            pixels,
            8,
            4,
            true,
            false,
            NSDeviceRGBColorSpace,
            0,
            0,
        )
    }?;
    let context = NSGraphicsContext::graphicsContextWithBitmapImageRep(&rep)?;
    NSGraphicsContext::saveGraphicsState_class();
    NSGraphicsContext::setCurrentContext(Some(&context));
    context.setImageInterpolation(NSImageInterpolation::High);
    let side = pixels as f64;
    image.drawInRect_fromRect_operation_fraction(
        NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(side, side)),
        NSRect::ZERO,
        NSCompositingOperation::SourceOver,
        1.0,
    );
    context.flushGraphics();
    NSGraphicsContext::restoreGraphicsState_class();
    let data = unsafe {
        rep.representationUsingType_properties(NSBitmapImageFileType::PNG, &NSDictionary::new())
    }?;
    Some(data.to_vec())
}

/// Installed location of an application by bundle identifier (Launch Services).
pub fn application_for_bundle_id(bundle_id: &str) -> Option<PathBuf> {
    let url = NSWorkspace::sharedWorkspace()
        .URLForApplicationWithBundleIdentifier(&NSString::from_str(bundle_id))?;
    Some(PathBuf::from(url.path()?.to_string()))
}

fn key(name: &'static NSAttributedStringKey) -> &'static NSString {
    name
}

/// Two-line, right-aligned menu-bar rates (design: 10 px, line-height 1.05).
pub fn set_status_rates(item: &NSStatusItem, download: &str, upload: &str) {
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };
    let Some(button) = item.button(mtm) else {
        return;
    };
    let font = NSFont::monospacedDigitSystemFontOfSize_weight(10.0, unsafe { NSFontWeightRegular });
    let paragraph = NSMutableParagraphStyle::new();
    paragraph.setAlignment(NSTextAlignment::Right);
    paragraph.setMinimumLineHeight(10.5);
    paragraph.setMaximumLineHeight(10.5);
    let color = NSColor::labelColor();
    let offset = NSNumber::new_f64(-4.5);
    let keys: [&NSString; 4] = unsafe {
        [
            key(NSFontAttributeName),
            key(NSParagraphStyleAttributeName),
            key(NSForegroundColorAttributeName),
            key(NSBaselineOffsetAttributeName),
        ]
    };
    let values: [&AnyObject; 4] = [&font, &paragraph, &color, &offset];
    let attributes = NSDictionary::from_slices(&keys, &values);
    let attributed = |text: &str| unsafe {
        NSAttributedString::initWithString_attributes(
            NSAttributedString::alloc(),
            &NSString::from_str(text),
            Some(&attributes),
        )
    };
    // Reserve the widest possible reading so the item (and every menu-bar item
    // to its left) does not shift each time the digits change.
    let widest = ["↓ 8.88 MiB/s\n↑ 8.88 MiB/s", "↓ 888 MiB/s\n↑ 888 MiB/s"]
        .iter()
        .filter_map(|sample| {
            button.setAttributedTitle(&attributed(sample));
            button.cell().map(|cell| cell.cellSize().width)
        })
        .fold(0.0, f64::max);
    button.setAttributedTitle(&attributed(&format!("{download}\n{upload}")));
    if widest > 0.0 {
        item.setLength(widest.ceil());
    }
}

/// Height of the native title bar overlapping the content view (0 when the
/// content view already starts below it).
///
/// # Safety
/// `ns_window` must be a live NSWindow pointer obtained on the main thread.
pub unsafe fn title_bar_inset(ns_window: *mut std::ffi::c_void) -> f64 {
    if MainThreadMarker::new().is_none() || ns_window.is_null() {
        return 0.0;
    }
    let window: &NSWindow = unsafe { &*(ns_window as *const NSWindow) };
    let Some(view) = window.contentView() else {
        return 0.0;
    };
    (view.frame().size.height - window.contentLayoutRect().size.height).max(0.0)
}

/// Borderless popup: rounded 10 px corners, native shadow, visible over
/// full-screen apps and on every Space. Public NSWindow/CALayer API only.
///
/// # Safety
/// `ns_window` must be a live NSWindow pointer obtained on the main thread.
pub unsafe fn style_popup(ns_window: *mut std::ffi::c_void) {
    if MainThreadMarker::new().is_none() || ns_window.is_null() {
        return;
    }
    let window: &NSWindow = unsafe { &*(ns_window as *const NSWindow) };
    window.setOpaque(false);
    window.setBackgroundColor(Some(&NSColor::clearColor()));
    window.setHasShadow(true);
    window.setCollectionBehavior(
        NSWindowCollectionBehavior::CanJoinAllSpaces
            | NSWindowCollectionBehavior::FullScreenAuxiliary
            | NSWindowCollectionBehavior::Transient,
    );
    if let Some(view) = window.contentView() {
        view.setWantsLayer(true);
        let layer: Option<Retained<AnyObject>> = unsafe { msg_send![&*view, layer] };
        if let Some(layer) = layer {
            let _: () = unsafe { msg_send![&*layer, setCornerRadius: 10.0f64] };
            let _: () = unsafe { msg_send![&*layer, setMasksToBounds: true] };
        }
    }
    window.invalidateShadow();
}
