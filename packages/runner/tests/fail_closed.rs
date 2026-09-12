use std::process::Command;

#[test]
fn binary_refuses_to_start_without_configuration() {
    let output = Command::new(env!("CARGO_BIN_EXE_naos-agent"))
        .env_clear()
        .output()
        .expect("naos-agent binary runs");

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("NAOS_AGENT_API_URL is not set"));
}

#[test]
fn binary_refuses_plain_http_to_a_remote_api() {
    let output = Command::new(env!("CARGO_BIN_EXE_naos-agent"))
        .env_clear()
        .env("NAOS_AGENT_API_URL", "http://api.example.com")
        .env("NAOS_AGENT_NAME", "alpha")
        .env("NAOS_AGENT_STATE_DIR", "/nonexistent")
        .env(
            "NAOS_AGENT_ENROLLMENT_TOKEN_FILE",
            "/nonexistent/enrollment",
        )
        .output()
        .expect("naos-agent binary runs");

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("must use https"));
}
