//! CI verifier. Excluded from app bundles through required-features.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 4 {
        return Err("Usage: bandpeek-verify-update CONFIG ARTIFACT SIGNATURE".into());
    }
    let config: serde_json::Value = serde_json::from_slice(&std::fs::read(&args[1])?)?;
    let public_key = config["plugins"]["updater"]["pubkey"]
        .as_str()
        .ok_or("Missing public key")?;
    bandpeek_core::update_signature::verify(
        &std::fs::read(&args[2])?,
        public_key,
        &std::fs::read_to_string(&args[3])?,
    )?;
    println!("Updater signature verifies against the embedded public key.");
    Ok(())
}
