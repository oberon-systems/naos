use std::collections::{BTreeMap, HashSet};

use crate::api::{Api, DesiredRun, DesiredState, RunStatus, Transition};
use crate::audit;
use crate::credentials::Credentials;
use crate::error::AgentError;
use crate::runtime::{LocalVm, Runtime};

pub const VM_LOST: &str = "vm lost";
pub const VM_START_FAILED: &str = "vm start failed";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    Claim(String),
    Start(String),
    Fail {
        run_id: String,
        reason: &'static str,
    },
    Stop {
        run_id: String,
        vm: Option<LocalVm>,
    },
    DestroyOrphan(LocalVm),
}

pub fn plan(desired: &[DesiredRun], actual: &[LocalVm]) -> Vec<Action> {
    let mut actions = Vec::new();
    let mut vms: BTreeMap<&str, &LocalVm> = BTreeMap::new();
    for vm in actual {
        if vms.contains_key(vm.run_id.as_str()) {
            actions.push(Action::DestroyOrphan(vm.clone()));
        } else {
            vms.insert(&vm.run_id, vm);
        }
    }

    let mut seen = HashSet::new();
    for run in desired.iter().filter(|run| seen.insert(run.id.as_str())) {
        let vm = vms.remove(run.id.as_str());
        let run_id = run.id.clone();
        match (run.status, vm) {
            (RunStatus::Pending, _) => actions.push(Action::Claim(run_id)),
            (RunStatus::Starting, _) => actions.push(Action::Start(run_id)),
            (RunStatus::Started, None) => actions.push(Action::Fail {
                run_id,
                reason: VM_LOST,
            }),
            (RunStatus::Stopping, vm) => actions.push(Action::Stop {
                run_id,
                vm: vm.cloned(),
            }),
            (RunStatus::Started | RunStatus::Collecting | RunStatus::WaitingMerge, _) => {}
            (RunStatus::Completed | RunStatus::Failed | RunStatus::Cancelled, vm) => {
                actions.extend(vm.cloned().map(Action::DestroyOrphan));
            }
        }
    }

    actions.extend(vms.into_values().cloned().map(Action::DestroyOrphan));
    actions
}

pub async fn reconcile<A: Api, R: Runtime>(
    api: &A,
    runtime: &R,
    credentials: &Credentials,
    desired: &DesiredState,
    actual: &[LocalVm],
) -> usize {
    let executor = Executor {
        api,
        runtime,
        credentials,
        desired,
    };
    let mut failures = 0;
    for action in plan(&desired.runs, actual) {
        if let Err(err) = executor.apply(&action).await {
            failures += 1;
            tracing::warn!(error = %err, ?action, "reconcile action failed");
        }
    }
    failures
}

struct Executor<'a, A, R> {
    api: &'a A,
    runtime: &'a R,
    credentials: &'a Credentials,
    desired: &'a DesiredState,
}

impl<A: Api, R: Runtime> Executor<'_, A, R> {
    async fn apply(&self, action: &Action) -> Result<(), AgentError> {
        match action {
            Action::Claim(run_id) => {
                self.advance(run_id, RunStatus::Pending, RunStatus::Starting, None)
                    .await?;
                audit::run_claimed(run_id);
                self.start(run_id).await
            }
            Action::Start(run_id) => self.start(run_id).await,
            Action::Fail { run_id, reason } => {
                self.advance(run_id, RunStatus::Started, RunStatus::Failed, Some(reason))
                    .await?;
                audit::run_failed(run_id, reason);
                Ok(())
            }
            Action::Stop { run_id, vm } => {
                if let Some(vm) = vm {
                    self.runtime.stop(vm).await?;
                }
                self.advance(run_id, RunStatus::Stopping, RunStatus::Collecting, None)
                    .await
            }
            Action::DestroyOrphan(vm) => {
                self.runtime.destroy(vm).await?;
                audit::orphan_destroyed(vm);
                Ok(())
            }
        }
    }

    async fn start(&self, run_id: &str) -> Result<(), AgentError> {
        let run = self
            .desired
            .runs
            .iter()
            .find(|run| run.id == run_id)
            .ok_or_else(|| AgentError::Runtime(format!("run {run_id} is not desired")))?;
        match self.runtime.ensure(run).await {
            Ok(_) => {
                self.advance(run_id, RunStatus::Starting, RunStatus::Started, None)
                    .await
            }
            Err(err) => {
                self.advance(
                    run_id,
                    RunStatus::Starting,
                    RunStatus::Failed,
                    Some(VM_START_FAILED),
                )
                .await?;
                audit::run_failed(run_id, VM_START_FAILED);
                Err(err)
            }
        }
    }

    async fn advance(
        &self,
        run_id: &str,
        expected: RunStatus,
        target: RunStatus,
        reason: Option<&str>,
    ) -> Result<(), AgentError> {
        let transition = Transition {
            lease_id: &self.desired.lease_id,
            expected,
            target,
            reason,
        };
        self.api
            .transition(self.credentials, run_id, &transition)
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::{desired, desired_run, vm, FakeApi, FakeRuntime};

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
        };

        let actions = plan(&runs, &[vm("run_a"), twin.clone(), vm("run_x")]);

        assert_eq!(
            actions,
            vec![
                Action::DestroyOrphan(twin),
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
    fn restart_with_live_vm_needs_no_action() {
        let runs = [desired_run("run_a", RunStatus::Started)];

        assert!(plan(&runs, &[vm("run_a")]).is_empty());
    }

    #[tokio::test]
    async fn claim_conflict_creates_no_vm() {
        let api = FakeApi::default();
        api.conflict_on("run_a", RunStatus::Starting);
        let runtime = FakeRuntime::default();
        let state = desired(vec![desired_run("run_a", RunStatus::Pending)]);

        let failures = reconcile(&api, &runtime, &api.credentials(), &state, &[]).await;

        assert_eq!(failures, 1);
        assert!(runtime.vms().is_empty());
    }

    #[tokio::test]
    async fn duplicate_delivery_creates_one_vm() {
        let api = FakeApi::default();
        let runtime = FakeRuntime::default();
        let state = desired(vec![desired_run("run_a", RunStatus::Pending)]);

        for _ in 0..2 {
            let actual = runtime.vms();
            reconcile(&api, &runtime, &api.credentials(), &state, &actual).await;
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

        let failures = reconcile(&api, &runtime, &api.credentials(), &state, &runtime.vms()).await;

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

        let failures = reconcile(&api, &runtime, &api.credentials(), &state, &[]).await;

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

        reconcile(&api, &runtime, &api.credentials(), &state, &runtime.vms()).await;

        assert_eq!(runtime.stopped(), vec![vm("run_a")]);
        assert_eq!(runtime.vms(), vec![vm("run_a")]);
        assert_eq!(
            api.transitions(),
            vec![("run_a".into(), RunStatus::Stopping, RunStatus::Collecting)]
        );
    }
}
