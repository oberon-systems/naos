use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::sync::atomic::Ordering;

use tempfile::TempDir;

use super::*;
use crate::libs::api::RunStatus;
use crate::libs::config::parse_api_url;
use crate::libs::testing::{desired, desired_run, vm, FakeApi, FakeRuntime};

fn setup(runtime: FakeRuntime) -> (TempDir, Agent<FakeApi, FakeRuntime>) {
    let dir = tempfile::tempdir().expect("tempdir");
    let enrollment = dir.path().join("enrollment");
    fs::write(&enrollment, "enroll-alpha\n").expect("write");
    fs::set_permissions(&enrollment, fs::Permissions::from_mode(0o600)).expect("chmod");
    let config = Config {
        api_url: parse_api_url("http://127.0.0.1:8000").expect("url"),
        name: "alpha".into(),
        capacity: 2,
        state_dir: dir.path().to_path_buf(),
        enrollment_token_file: enrollment,
    };
    let agent = Agent::new(&config, FakeApi::default(), runtime);
    (dir, agent)
}

#[tokio::test]
async fn first_cycle_registers_and_persists_credentials() {
    let (dir, mut agent) = setup(FakeRuntime::default());
    agent.api.serve(desired(vec![]));

    agent.cycle().await.expect("cycle");

    let stored = CredentialStore::new(dir.path()).load().expect("load");
    assert_eq!(stored.map(|c| c.token), Some("token-1".into()));
    assert_eq!(agent.interval(), Duration::from_secs(20));
}

#[tokio::test]
async fn restart_reuses_stored_credentials() {
    let (dir, mut agent) = setup(FakeRuntime::with_vms(vec![vm("run_a")]));
    CredentialStore::new(dir.path())
        .save(&agent.api.credentials())
        .expect("save");
    agent
        .api
        .serve(desired(vec![desired_run("run_a", RunStatus::Started)]));

    assert_eq!(agent.cycle().await.expect("cycle"), 0);

    assert_eq!(agent.api.registrations.load(Ordering::SeqCst), 0);
    assert_eq!(agent.runtime.vms(), vec![vm("run_a")]);
    assert!(agent.api.transitions().is_empty());
}

#[tokio::test]
async fn rejected_credentials_are_dropped_and_reregistered() {
    let (dir, mut agent) = setup(FakeRuntime::default());
    agent.api.serve(desired(vec![]));
    agent.cycle().await.expect("first cycle");
    agent.api.reject_next_heartbeats(1);

    assert!(matches!(agent.cycle().await, Err(AgentError::Unauthorized)));
    assert_eq!(CredentialStore::new(dir.path()).load().expect("load"), None);

    agent.cycle().await.expect("third cycle");
    assert_eq!(agent.api.registrations.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn rotated_token_is_persisted() {
    let (dir, mut agent) = setup(FakeRuntime::default());
    agent.api.serve(desired(vec![]));
    agent.api.rotate_to("token-rotated");

    agent.cycle().await.expect("cycle");

    let stored = CredentialStore::new(dir.path()).load().expect("load");
    assert_eq!(stored.map(|c| c.token), Some("token-rotated".into()));
}

#[tokio::test]
async fn unknown_desired_state_destroys_nothing() {
    let (_dir, mut agent) = setup(FakeRuntime::with_vms(vec![vm("run_a")]));

    assert!(matches!(agent.cycle().await, Err(AgentError::Transport(_))));

    assert_eq!(agent.runtime.vms(), vec![vm("run_a")]);
}

#[tokio::test]
async fn unavailable_runtime_offers_no_capacity() {
    let runtime = FakeRuntime::default();
    runtime.make_unavailable();
    let (_dir, mut agent) = setup(runtime);
    agent.api.serve(desired(vec![]));

    assert!(matches!(
        agent.cycle().await,
        Err(AgentError::Unimplemented(_))
    ));

    assert_eq!(agent.api.capacities(), vec![0]);
    assert!(agent.api.transitions().is_empty());
}

#[tokio::test]
async fn expired_lease_fences_every_vm() {
    let (_dir, mut agent) = setup(FakeRuntime::with_vms(vec![vm("run_a"), vm("run_b")]));
    agent.lease = LeaseClock::starting(Instant::now(), Duration::ZERO);

    let _ = agent.cycle().await;

    assert!(agent.runtime.vms().is_empty());
}
