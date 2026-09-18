use std::collections::{BTreeMap, HashSet};

use crate::libs::api::{
    Api, DesiredRun, DesiredState, DiffReport, MergeReport, RunStatus, Transition,
};
use crate::libs::audit;
use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;
use crate::libs::image::ImageSource;
use crate::libs::runtime::{LocalVm, Runtime};

pub const VM_LOST: &str = "vm lost";
pub const VM_START_FAILED: &str = "vm start failed";
pub const COLLECTION_FAILED: &str = "collection failed";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    Claim(String),
    Start(String),
    Sync(LocalVm),
    Fail {
        run_id: String,
        from: RunStatus,
        reason: &'static str,
    },
    Stop {
        run_id: String,
        vm: Option<LocalVm>,
    },
    Collect(LocalVm),
    Merge(LocalVm),
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
        let (live, dead) = match vms.remove(run.id.as_str()).cloned() {
            Some(vm) if vm.running => (Some(vm), None),
            other => (None, other),
        };
        let run_id = run.id.clone();
        match run.status {
            RunStatus::Pending => {
                actions.extend(dead.map(Action::DestroyOrphan));
                actions.push(Action::Claim(run_id));
            }
            RunStatus::Starting => {
                actions.extend(dead.map(Action::DestroyOrphan));
                actions.push(Action::Start(run_id));
            }
            RunStatus::Started => match live {
                Some(vm) => actions.push(Action::Sync(vm)),
                None => {
                    actions.push(Action::Fail {
                        run_id,
                        from: RunStatus::Started,
                        reason: VM_LOST,
                    });
                    actions.extend(dead.map(Action::DestroyOrphan));
                }
            },
            RunStatus::Stopping => actions.push(Action::Stop {
                run_id,
                vm: live.or(dead),
            }),
            RunStatus::Collecting => actions.push(match live.or(dead) {
                Some(vm) => Action::Collect(vm),
                None => Action::Fail {
                    run_id,
                    from: RunStatus::Collecting,
                    reason: VM_LOST,
                },
            }),
            RunStatus::WaitingMerge => match (live.or(dead), &run.merge) {
                (Some(vm), Some(_)) => actions.push(Action::Merge(vm)),
                (Some(_), None) => {}
                (None, _) => actions.push(Action::Fail {
                    run_id,
                    from: RunStatus::WaitingMerge,
                    reason: VM_LOST,
                }),
            },
            RunStatus::Completed | RunStatus::Failed | RunStatus::Cancelled => {
                actions.extend(live.or(dead).map(Action::DestroyOrphan));
            }
        }
    }

    actions.extend(vms.into_values().cloned().map(Action::DestroyOrphan));
    actions
}

pub async fn reconcile<A: Api + Sync, R: Runtime>(
    api: &A,
    runtime: &R,
    images: &dyn ImageSource,
    credentials: &Credentials,
    desired: &DesiredState,
    actual: &[LocalVm],
) -> usize {
    let executor = Executor {
        api,
        runtime,
        images,
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
    images: &'a dyn ImageSource,
    credentials: &'a Credentials,
    desired: &'a DesiredState,
}

impl<A: Api + Sync, R: Runtime> Executor<'_, A, R> {
    async fn apply(&self, action: &Action) -> Result<(), AgentError> {
        match action {
            Action::Claim(run_id) => {
                self.advance(run_id, RunStatus::Pending, RunStatus::Starting, None)
                    .await?;
                audit::run_claimed(run_id);
                self.start(run_id).await
            }
            Action::Start(run_id) => self.start(run_id).await,
            Action::Sync(vm) => self.runtime.sync(self.run(&vm.run_id)?, vm).await,
            Action::Fail {
                run_id,
                from,
                reason,
            } => {
                self.advance(run_id, *from, RunStatus::Failed, Some(reason))
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
            Action::Collect(vm) => {
                let err = match self.runtime.collect(self.run(&vm.run_id)?, vm).await {
                    Ok(diff) => {
                        let report = DiffReport {
                            lease_id: &self.desired.lease_id,
                            entries: &diff.entries,
                        };
                        return self
                            .api
                            .report_diff(self.credentials, &vm.run_id, &report)
                            .await;
                    }
                    Err(err) => err,
                };
                self.advance(
                    &vm.run_id,
                    RunStatus::Collecting,
                    RunStatus::Failed,
                    Some(COLLECTION_FAILED),
                )
                .await?;
                audit::run_failed(&vm.run_id, COLLECTION_FAILED);
                Err(err)
            }
            Action::Merge(vm) => {
                let run = self.run(&vm.run_id)?;
                let Some(decision) = &run.merge else {
                    return Ok(());
                };
                let outcome = self.runtime.merge(run, vm, decision).await?;
                let report = MergeReport {
                    lease_id: &self.desired.lease_id,
                    outcome: &outcome,
                };
                self.api
                    .report_merge(self.credentials, &vm.run_id, &report)
                    .await
            }
            Action::DestroyOrphan(vm) => {
                self.runtime.destroy(vm).await?;
                audit::orphan_destroyed(vm);
                Ok(())
            }
        }
    }

    fn run(&self, run_id: &str) -> Result<&DesiredRun, AgentError> {
        self.desired
            .runs
            .iter()
            .find(|run| run.id == run_id)
            .ok_or_else(|| AgentError::Runtime(format!("run {run_id} is not desired")))
    }

    async fn start(&self, run_id: &str) -> Result<(), AgentError> {
        let run = self.run(run_id)?;
        match self.runtime.ensure(run, self.images).await {
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
