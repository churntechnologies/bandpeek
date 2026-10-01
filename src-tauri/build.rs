fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        let source = "src/collectors/macos/process_identity.c";
        let topology = "src/collectors/macos/topology.c";
        println!("cargo:rerun-if-changed={source}");
        println!("cargo:rerun-if-changed={topology}");
        cc::Build::new()
            .file(source)
            .file(topology)
            .compile("bandpeek_process_identity");
        println!("cargo:rustc-link-lib=framework=SystemConfiguration");
        println!("cargo:rustc-link-lib=framework=CoreFoundation");
    }
    #[cfg(feature = "desktop")]
    tauri_build::build()
}
