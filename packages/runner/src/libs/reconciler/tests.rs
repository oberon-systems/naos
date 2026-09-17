use super::*;
use crate::libs::testing::{dead_vm, desired, desired_run, vm, FakeApi, FakeRuntime, FakeSource};

const IMAGE: &[u8] = b"qcow2-alpha";

fn claim(id: &str) -> Action {
    Action::Claim(id.into())
}

#[test]
fn plan_covers_every_status() {
    let runs = [
        desired_run("run_a", RunStatus::Pending),
        desired_run("run_b", RunStatus::Starting),
        desired_run("run_c", RunStatus::Started),
        desired_run("run_d", RunStatus::Started),
        desired_run("run_e", RunStatus::Stopping),
        desired_run("run_f", RunStatus::Collecting),
        desired_run("run_g", RunStatus::Failed),
    ];
    let vms = [vm("run_c"), vm("run_e"), vm("run_f"), vm("run_g")];

    assert_eq!(
        plan(&runs, &vms),
        vec![
            claim("run_a"),
            Action::Start("run_b".into()),
            Action::Sync(vm("run_c")),
            Action::Fail {
                run_id: "run_d".into(),
                reason: VM_LOST
            },
            Action::Stop {
                run_id: "run_e".into(),
                vm: Some(vm("run_e"))
            },
            Action::DestroyOrphan(vm("run_g")),
        ]
    );
}

#[test]
fn plan_destroys_unknown_and_duplicate_vms() {
    let runs = [desired_run("run_a", RunStatus::Started)];
    let twin = LocalVm {
        vm_id: "vm_twin".into(),
        run_id: "run_a".into(),
        running: true,
    };

    let actions = plan(&runs, &[vm("run_a"), twin.clone(), vm("run_x")]);

    assert_eq!(
        actions,
        vec![
            Action::DestroyOrphan(twin),
            Action::Sync(vm("run_a")),
            Action::DestroyOrphan(vm("run_x"))
        ]
    );
}

#[test]
fn plan_ignores_duplicate_desired_entries() {
    let runs = [
        desired_run("run_a", RunStatus::Pending),
        desired_run("run_a", RunStatus::Pending),
    ];

    assert_eq!(plan(&runs, &[]), vec![claim("run_a")]);
}

#[test]
fn dead_vms_are_replaced_reported_or_kept_for_collection() {
    let runs = [
        desired_run("run_a", RunStatus::Pending),
        desired_run("run_b", RunStatus::Starting),
        desired_run("run_c", RunStatus::Started),
        desired_run("run_d", RunStatus::Collecting),
        desired_run("run_e", RunStatus::Stopping),
    ];
    let vms = [
        dead_vm("run_a"),
        dead_vm("run_b"),
        dead_vm("run_c"),
        dead_vm("run_d"),
        dead_vm("run_e"),
    ];

    assert_eq!(
        plan(&runs, &vms),
        vec![
            Action::DestroyOrphan(dead_vm("run_a")),
            claim("run_a"),
            Action::DestroyOrphan(dead_vm("run_b")),
            Action::Start("run_b".into()),
            Action::Fail {
                run_id: "run_c".into(),
                reason: VM_LOST
            },
            Action::DestroyOrphan(dead_vm("run_c")),
            Action::Stop {
                run_id: "run_e".into(),
                vm: Some(dead_vm("run_e"))
            },
        ]
    );
}

#[test]
fn restart_with_live_vm_only_syncs_it() {
    let runs = [desired_run("run_a", RunStatus::Started)];

    assert_eq!(plan(&runs, &[vm("run_a")]), vec![Action::Sync(vm("run_a"))]);
}

#[tokio::test]
async fn a_running_vm_is_synced_without_a_transition() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::with_vms(vec![vm("run_a")]);
    let state = desired(vec![desired_run("run_a", RunStatus::Started)]);
    let images = FakeSource::new(IMAGE);

    let failures = reconcile(
        &api,
        &runtime,
        &images,
        &api.credentials(),
        &state,
        &runtime.vms(),
    )
    .await;

    assert_eq!(failures, 0);
    assert_eq!(runtime.synced(), vec![vm("run_a")]);
    assert!(api.transitions().is_empty());
}

#[tokio::test]
async fn a_failed_sync_does_not_fail_the_run() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::with_vms(vec![vm("run_a")]);
    runtime.fail_sync();
    let state = desired(vec![desired_run("run_a", RunStatus::Started)]);
    let images = FakeSource::new(IMAGE);

    let failures = reconcile(
        &api,
        &runtime,
        &images,
        &api.credentials(),
        &state,
        &runtime.vms(),
    )
    .await;

    assert_eq!(failures, 1);
    assert_eq!(runtime.vms(), vec![vm("run_a")]);
    assert!(api.transitions().is_empty());
}

#[tokio::test]
async fn claim_conflict_creates_no_vm() {
    let api = FakeApi::default();
    api.conflict_on("run_a", RunStatus::Starting);
    let runtime = FakeRuntime::default();
    let state = desired(vec![desired_run("run_a", RunStatus::Pending)]);
    let images = FakeSource::new(IMAGE);

    let failures = reconcile(&api, &runtime, &images, &api.credentials(), &state, &[]).await;

    assert_eq!(failures, 1);
    assert!(runtime.vms().is_empty());
}

#[tokio::test]
async fn duplicate_delivery_creates_one_vm() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::default();
    let state = desired(vec![desired_run("run_a", RunStatus::Pending)]);
    let images = FakeSource::new(IMAGE);

    for _ in 0..2 {
        let actual = runtime.vms();
        reconcile(&api, &runtime, &images, &api.credentials(), &state, &actual).await;
    }

    assert_eq!(runtime.vms().len(), 1);
    assert_eq!(
        api.transitions(),
        vec![
            ("run_a".into(), RunStatus::Pending, RunStatus::Starting),
            ("run_a".into(), RunStatus::Starting, RunStatus::Started),
            ("run_a".into(), RunStatus::Pending, RunStatus::Starting),
            ("run_a".into(), RunStatus::Starting, RunStatus::Started),
        ]
    );
}

#[tokio::test]
async fn starting_run_is_recovered_without_a_second_vm() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::with_vms(vec![vm("run_a")]);
    let state = desired(vec![desired_run("run_a", RunStatus::Starting)]);
    let images = FakeSource::new(IMAGE);

    let failures = reconcile(
        &api,
        &runtime,
        &images,
        &api.credentials(),
        &state,
        &runtime.vms(),
    )
    .await;

    assert_eq!(failures, 0);
    assert_eq!(runtime.vms(), vec![vm("run_a")]);
    assert_eq!(
        api.transitions(),
        vec![("run_a".into(), RunStatus::Starting, RunStatus::Started)]
    );
}

#[tokio::test]
async fn failed_start_is_reported() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::default();
    runtime.fail_ensure();
    let state = desired(vec![desired_run("run_a", RunStatus::Pending)]);
    let images = FakeSource::new(IMAGE);

    let failures = reconcile(&api, &runtime, &images, &api.credentials(), &state, &[]).await;

    assert_eq!(failures, 1);
    assert_eq!(
        api.transitions().last(),
        Some(&("run_a".into(), RunStatus::Starting, RunStatus::Failed))
    );
}

#[tokio::test]
async fn orphans_are_destroyed_and_stops_collected() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::with_vms(vec![vm("run_a"), vm("run_x")]);
    let state = desired(vec![desired_run("run_a", RunStatus::Stopping)]);
    let images = FakeSource::new(IMAGE);

    reconcile(
        &api,
        &runtime,
        &images,
        &api.credentials(),
        &state,
        &runtime.vms(),
    )
    .await;

    assert_eq!(runtime.stopped(), vec![vm("run_a")]);
    assert_eq!(runtime.vms(), vec![vm("run_a")]);
    assert_eq!(
        api.transitions(),
        vec![("run_a".into(), RunStatus::Stopping, RunStatus::Collecting)]
    );
}
