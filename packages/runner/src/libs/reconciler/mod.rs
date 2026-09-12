use std::collections::{BTreeMap, HashSet};

use crate::libs::api::{Api, DesiredRun, DesiredState, RunStatus, Transition};
use crate::libs::audit;
use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;
use crate::libs::runtime::{LocalVm, Runtime};

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
mod tests;
