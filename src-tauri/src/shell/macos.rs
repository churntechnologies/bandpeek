//! Public AppKit calls used by the desktop shell. No private API.
//! Every function here must run on the main thread.
use objc2::{msg_send, rc::Retained, runtime::AnyObject, AllocAnyThread, MainThreadMarker};
use objc2_app_kit::{
    NSBitmapImageFileType, NSBitmapImageRep, NSColor, NSCompositingOperation,
    NSDeviceRGBColorSpace, NSFont, NSFontAttributeName, NSFontWeightMedium, NSGraphicsContext,
    NSImage, NSImageInterpolation, NSStatusItem, NSStringDrawing, NSTextAlignment, NSWindow,
    NSWindowCollectionBehavior, NSWorkspace,
};
use objc2_foundation::{NSAttributedStringKey, NSDictionary, NSPoint, NSRect, NSSize, NSString};
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

// Use NSStatusBarButton's own cell. Native label/image subviews caused macOS
// 27's status-bar backdrop to redraw continuously (~35% CPU).
struct StatusViews {
    font: Retained<NSFont>,
    attributes: Retained<NSDictionary<NSString, AnyObject>>,
    rates_width: f64,
    mark14: Retained<NSImage>,
    mark16: Retained<NSImage>,
    combined: Option<Retained<NSImage>>,
    mode: bandpeek_core::settings::MenuBarDisplay,
    download: String,
    upload: String,
}
thread_local! {
    static STATUS: std::cell::RefCell<Option<StatusViews>> = const { std::cell::RefCell::new(None) };
}

pub fn set_status_rates(
    item: &NSStatusItem,
    mode: bandpeek_core::settings::MenuBarDisplay,
    download: &str,
    upload: &str,
) {
    use bandpeek_core::settings::MenuBarDisplay;
    use objc2_app_kit::{
        NSBaselineOffsetAttributeName, NSCellImagePosition, NSForegroundColorAttributeName,
        NSImageScaling, NSMutableParagraphStyle, NSParagraphStyleAttributeName,
    };
    use objc2_foundation::{NSArray, NSData, NSNumber};
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };
    let Some(button) = item.button(mtm) else {
        return;
    };
    STATUS.with_borrow_mut(|slot| {
        let views = slot.get_or_insert_with(|| {
            let font =
                NSFont::monospacedDigitSystemFontOfSize_weight(10.0, unsafe { NSFontWeightMedium });
            let paragraph = NSMutableParagraphStyle::new();
            paragraph.setAlignment(NSTextAlignment::Left);
            paragraph.setMinimumLineHeight(10.0);
            paragraph.setMaximumLineHeight(10.0);
            let color = NSColor::labelColor();
            let offset = NSNumber::new_f64(-4.5);
            let keys = unsafe {
                [
                    key(NSFontAttributeName),
                    key(NSParagraphStyleAttributeName),
                    key(NSForegroundColorAttributeName),
                    key(NSBaselineOffsetAttributeName),
                ]
            };
            let values: [&AnyObject; 4] = [&font, &paragraph, &color, &offset];
            let attributes = NSDictionary::from_slices(&keys, &values);
            let keys = [unsafe { key(NSFontAttributeName) }];
            let values: [&AnyObject; 1] = [&font];
            let attrs = NSDictionary::from_slices(&keys, &values);
            let width = [
                "↓ 888.8 MB/s",
                "↓ 999.9 GB/s",
                "↑ 999.9 KB/s",
                "↓ 999.9 B/s",
            ]
            .iter()
            .map(|text| unsafe {
                NSString::from_str(text)
                    .sizeWithAttributes(Some(&attrs))
                    .width
            })
            .fold(0.0, f64::max)
            .ceil();
            let tab = unsafe {
                objc2_app_kit::NSTextTab::initWithTextAlignment_location_options(
                    objc2_app_kit::NSTextTab::alloc(),
                    NSTextAlignment::Right,
                    width,
                    &NSDictionary::new(),
                )
            };
            paragraph.setTabStops(Some(&NSArray::from_retained_slice(&[tab])));
            let load = |bytes: &[u8], size| {
                let data = NSData::with_bytes(bytes);
                let image =
                    NSImage::initWithData(NSImage::alloc(), &data).expect("Packet template PNG");
                image.setSize(NSSize::new(size, size));
                image.setTemplate(true);
                image
            };
            let mark14 = load(include_bytes!("../../icons/PacketTemplate14@2x.png"), 14.0);
            let mark16 = load(include_bytes!("../../icons/PacketTemplate16@2x.png"), 16.0);
            StatusViews {
                font,
                attributes,
                rates_width: width,
                mark14,
                mark16,
                combined: None,
                mode,
                download: String::new(),
                upload: String::new(),
            }
        });
        let title = if mode == MenuBarDisplay::IconOnly {
            String::new()
        } else {
            format!("\t{download}\n\t{upload}")
        };
        let attributed = unsafe {
            objc2_foundation::NSAttributedString::initWithString_attributes(
                objc2_foundation::NSAttributedString::alloc(),
                &NSString::from_str(&title),
                Some(&views.attributes),
            )
        };
        button.setAttributedTitle(&attributed);
        button.setImageScaling(NSImageScaling::ScaleNone);
        button.setImageHugsTitle(false);
        match mode {
            MenuBarDisplay::SpeedsOnly => {
                button.setImage(None);
                button.setImagePosition(NSCellImagePosition::NoImage);
            }
            MenuBarDisplay::IconOnly => {
                button.setImage(Some(&views.mark16));
                button.setImagePosition(NSCellImagePosition::ImageOnly);
            }
            MenuBarDisplay::IconAndSpeeds => {
                button.setImage(Some(views.combined.as_deref().unwrap_or(&views.mark14)));
                button.setImagePosition(NSCellImagePosition::ImageLeft);
            }
        }
        item.setLength(mode.item_width(views.rates_width));
        if mode == MenuBarDisplay::IconAndSpeeds && views.combined.is_none() {
            if let Some(cell) = button.cell() {
                let icon = cell.imageRectForBounds(button.bounds());
                let text = cell.titleRectForBounds(button.bounds());
                let native_gap = text.origin.x - (icon.origin.x + icon.size.width);
                let padding = (4.0 - native_gap).max(0.0);
                // Add transparent trailing space to the native template image;
                // the visible Packet itself stays exactly 14 × 14 pt. AppKit
                // still draws/tints it through the actual status button cell.
                let mark = views.mark14.clone();
                let drawing = block2::RcBlock::new(move |_rect: NSRect| {
                    mark.drawInRect_fromRect_operation_fraction(
                        NSRect::new(NSPoint::new(0.0, 0.0), NSSize::new(14.0, 14.0)),
                        NSRect::ZERO,
                        NSCompositingOperation::SourceOver,
                        1.0,
                    );
                    objc2::runtime::Bool::YES
                });
                let image = NSImage::imageWithSize_flipped_drawingHandler(
                    NSSize::new(14.0 + padding, 14.0),
                    false,
                    &drawing,
                );
                image.setTemplate(true);
                button.setImage(Some(&image));
                views.combined = Some(image);
            }
        }
        // Keep tray-icon's original event target covering the fixed item width.
        for child in button.subviews() {
            child.setFrame(button.bounds());
        }
        views.mode = mode;
        views.download = download.into();
        views.upload = upload.into();
    });
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

/// Native inspection for release validation; never exposed through the UI.
pub fn status_report(item: &NSStatusItem) -> serde_json::Value {
    use bandpeek_core::settings::MenuBarDisplay;
    let Some(mtm) = MainThreadMarker::new() else {
        return serde_json::Value::Null;
    };
    let Some(button) = item.button(mtm) else {
        return serde_json::Value::Null;
    };
    let Some(cell) = button.cell() else {
        return serde_json::Value::Null;
    };
    let icon = cell.imageRectForBounds(button.bounds());
    let text = cell.titleRectForBounds(button.bounds());
    STATUS.with_borrow(|slot| match slot {
        Some(v) => serde_json::json!({
            "length": item.length(), "rates_width": v.rates_width,
            "down": v.download, "up": v.upload,
            "actual_title": button.attributedTitle().string().to_string(),
            "rates_hidden": v.mode == MenuBarDisplay::IconOnly,
            "icon_hidden": v.mode == MenuBarDisplay::SpeedsOnly,
            "icon_size": if v.mode==MenuBarDisplay::IconOnly {16.0} else {14.0},
            "template": button.image().is_some_and(|i| i.isTemplate()) || v.mode==MenuBarDisplay::SpeedsOnly,
            "font_size": v.font.pointSize(), "font_name": v.font.fontName().to_string(),
            "line_spacing": 10.0,
            "text_x": text.origin.x, "icon_x": icon.origin.x,
            "text_rect": [text.origin.x,text.origin.y,text.size.width,text.size.height],
            "image_rect": [icon.origin.x,icon.origin.y,icon.size.width,icon.size.height],
            "button_width": button.bounds().size.width,
        }),
        None => serde_json::Value::Null,
    })
}
