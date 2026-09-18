use super::*;
use crate::libs::overlay::merge::{Conflict, Decision, Outcome, Report};
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
                from: RunStatus::Started,
                reason: VM_LOST
            },
            Action::Stop {
                run_id: "run_e".into(),
                vm: Some(vm("run_e"))
            },
            Action::Collect(vm("run_f")),
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
fn dead_vms_are_replaced_reported_or_collected() {
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
                from: RunStatus::Started,
                reason: VM_LOST
            },
            Action::DestroyOrphan(dead_vm("run_c")),
            Action::Collect(dead_vm("run_d")),
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

#[test]
fn a_collecting_run_without_its_vm_is_lost() {
    let runs = [desired_run("run_a", RunStatus::Collecting)];

    assert_eq!(
        plan(&runs, &[]),
        vec![Action::Fail {
            run_id: "run_a".into(),
            from: RunStatus::Collecting,
            reason: VM_LOST
        }]
    );
}

#[tokio::test]
async fn a_collected_run_reports_its_diff_and_keeps_its_vm() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::with_vms(vec![dead_vm("run_a")]);
    let state = desired(vec![desired_run("run_a", RunStatus::Collecting)]);
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
    assert_eq!(runtime.collected(), vec![dead_vm("run_a")]);
    assert_eq!(api.diffs(), vec![("run_a".to_owned(), 0)]);
    assert_eq!(runtime.vms(), vec![dead_vm("run_a")]);
    assert!(api.transitions().is_empty());
}

fn waiting(id: &str, decision: Option<Decision>) -> DesiredRun {
    DesiredRun {
        merge: decision,
        ..desired_run(id, RunStatus::WaitingMerge)
    }
}

#[test]
fn a_waiting_run_merges_once_decided_and_is_never_destroyed() {
    let runs = [
        waiting("run_a", Some(Decision::default())),
        waiting("run_b", None),
        waiting("run_c", None),
    ];

    assert_eq!(
        plan(&runs, &[dead_vm("run_a"), dead_vm("run_b")]),
        vec![
            Action::Merge(dead_vm("run_a")),
            Action::Fail {
                run_id: "run_c".into(),
                from: RunStatus::WaitingMerge,
                reason: VM_LOST
            },
        ]
    );
}

#[tokio::test]
async fn a_merge_outcome_is_reported_as_it_came() {
    for outcome in [
        Outcome::Applied(Report {
            applied: vec!["notes.txt".into()],
            ..Report::default()
        }),
        Outcome::Conflict {
            conflicts: vec![Conflict {
                path: "notes.txt".into(),
                reason: "the host changed since collection".into(),
            }],
        },
    ] {
        let api = FakeApi::default();
        let runtime = FakeRuntime::with_vms(vec![dead_vm("run_a")]);
        runtime.merge_with(outcome.clone());
        let decision = Decision {
            paths: vec!["notes.txt".into()],
            ..Decision::default()
        };
        let state = desired(vec![waiting("run_a", Some(decision.clone()))]);
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
        assert_eq!(runtime.merged(), vec![(dead_vm("run_a"), decision)]);
        assert_eq!(api.merges(), vec![("run_a".to_owned(), outcome)]);
        assert_eq!(runtime.vms(), vec![dead_vm("run_a")]);
    }
}

#[tokio::test]
async fn a_failed_collection_fails_the_run() {
    let api = FakeApi::default();
    let runtime = FakeRuntime::with_vms(vec![dead_vm("run_a")]);
    runtime.fail_collect();
    let state = desired(vec![desired_run("run_a", RunStatus::Collecting)]);
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
    assert_eq!(
        api.transitions(),
        vec![("run_a".into(), RunStatus::Collecting, RunStatus::Failed)]
    );
}
