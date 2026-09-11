use std::process::Command;

#[test]
fn binary_exits_with_failure() {
    let output = Command::new(env!("CARGO_BIN_EXE_naos-agent"))
        .env_remove("RUST_LOG")
        .output()
        .expect("naos-agent binary runs");

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("reconciler is not implemented"));
}
