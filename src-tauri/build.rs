fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        let source = "src/collectors/macos/process_identity.c";
        println!("cargo:rerun-if-changed={source}");
        cc::Build::new()
            .file(source)
            .compile("bandpeek_process_identity");
    }
    #[cfg(feature = "desktop")]
    tauri_build::build()
}
