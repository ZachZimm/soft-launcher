fn main() {
    if std::env::var_os("CARGO_FEATURE_DESKTOP").is_some() {
        if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("linux") {
            println!(
                "cargo:rustc-link-arg-bin=soft-launcher=-Wl,--enable-new-dtags,-rpath,$ORIGIN/lib"
            );
        }
        tauri_build::build();
    }
}
