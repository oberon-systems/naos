use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;

use crate::libs::api::{
    Api, DesiredRun, DesiredState, HeartbeatReply, IssuedToken, LeaseGrant, Registration,
    RunStatus, Transition,
};
use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;
use crate::libs::runtime::{LocalVm, Runtime};

pub const LEASE_ID: &str = "lease_alpha";

pub fn desired_run(id: &str, status: RunStatus) -> DesiredRun {
    DesiredRun {
        id: id.into(),
        status,
    }
}

pub fn desired(runs: Vec<DesiredRun>) -> DesiredState {
    DesiredState {
        lease_id: LEASE_ID.into(),
        runs,
    }
}

pub fn vm(run_id: &str) -> LocalVm {
    LocalVm {
        vm_id: format!("vm_{run_id}"),
        run_id: run_id.into(),
    }
}

fn lease() -> LeaseGrant {
    LeaseGrant { ttl_seconds: 60 }
}

#[derive(Default)]
pub struct FakeApi {
    conflicts: Mutex<HashSet<(String, RunStatus)>>,
    transitions: Mutex<Vec<(String, RunStatus, RunStatus)>>,
    capacities: Mutex<Vec<u32>>,
    desired: Mutex<Option<DesiredState>>,
    rotated_token: Mutex<Option<String>>,
    reject_heartbeats: AtomicUsize,
    pub registrations: AtomicUsize,
}

impl FakeApi {
    pub fn credentials(&self) -> Credentials {
        Credentials {
            runner_id: "rnr_alpha".into(),
            token: "token-alpha".into(),
        }
    }

    pub fn conflict_on(&self, run_id: &str, target: RunStatus) {
        lock(&self.conflicts).insert((run_id.into(), target));
    }

    pub fn serve(&self, state: DesiredState) {
        *lock(&self.desired) = Some(state);
    }

    pub fn rotate_to(&self, token: &str) {
        *lock(&self.rotated_token) = Some(token.into());
    }

    pub fn reject_next_heartbeats(&self, count: usize) {
        self.reject_heartbeats.store(count, Ordering::SeqCst);
    }

    pub fn transitions(&self) -> Vec<(String, RunStatus, RunStatus)> {
        lock(&self.transitions).clone()
    }

    pub fn capacities(&self) -> Vec<u32> {
        lock(&self.capacities).clone()
    }
}

impl Api for FakeApi {
    async fn register(&self, _: &str, _: &str) -> Result<Registration, AgentError> {
        let count = self.registrations.fetch_add(1, Ordering::SeqCst) + 1;
        Ok(Registration {
            runner_id: "rnr_alpha".into(),
            token: IssuedToken {
                value: format!("token-{count}"),
            },
            lease: lease(),
        })
    }

    async fn heartbeat(
        &self,
        _: &Credentials,
        capacity: u32,
    ) -> Result<HeartbeatReply, AgentError> {
        lock(&self.capacities).push(capacity);
        let rejected =
            self.reject_heartbeats
                .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| n.checked_sub(1));
        if rejected.is_ok() {
            return Err(AgentError::Unauthorized);
        }
        Ok(HeartbeatReply {
            lease: lease(),
            token: lock(&self.rotated_token)
                .take()
                .map(|value| IssuedToken { value }),
        })
    }

    async fn desired(&self, _: &Credentials) -> Result<DesiredState, AgentError> {
        lock(&self.desired)
            .clone()
            .ok_or_else(|| AgentError::Transport("api unavailable".into()))
    }

    async fn transition(
        &self,
        _: &Credentials,
        run_id: &str,
        transition: &Transition<'_>,
    ) -> Result<(), AgentError> {
        if lock(&self.conflicts).contains(&(run_id.to_owned(), transition.target)) {
            return Err(AgentError::Conflict("taken".into()));
        }
        lock(&self.transitions).push((run_id.into(), transition.expected, transition.target));
        Ok(())
    }
}

#[derive(Default)]
pub struct FakeRuntime {
    vms: Mutex<Vec<LocalVm>>,
    stopped: Mutex<Vec<LocalVm>>,
    unavailable: AtomicBool,
    fail_ensure: AtomicBool,
}

impl FakeRuntime {
    pub fn with_vms(vms: Vec<LocalVm>) -> Self {
        let runtime = Self::default();
        *lock(&runtime.vms) = vms;
        runtime
    }

    pub fn vms(&self) -> Vec<LocalVm> {
        lock(&self.vms).clone()
    }

    pub fn stopped(&self) -> Vec<LocalVm> {
        lock(&self.stopped).clone()
    }

    pub fn make_unavailable(&self) {
        self.unavailable.store(true, Ordering::SeqCst);
    }

    pub fn fail_ensure(&self) {
        self.fail_ensure.store(true, Ordering::SeqCst);
    }
}

impl Runtime for FakeRuntime {
    async fn list(&self) -> Result<Vec<LocalVm>, AgentError> {
        if self.unavailable.load(Ordering::SeqCst) {
            return Err(AgentError::Unimplemented("fake runtime"));
        }
        Ok(self.vms())
    }

    async fn ensure(&self, run: &DesiredRun) -> Result<LocalVm, AgentError> {
        if self.fail_ensure.load(Ordering::SeqCst) {
            return Err(AgentError::Runtime("boot failed".into()));
        }
        let mut vms = lock(&self.vms);
        if let Some(existing) = vms.iter().find(|vm| vm.run_id == run.id) {
            return Ok(existing.clone());
        }
        let created = vm(&run.id);
        vms.push(created.clone());
        Ok(created)
    }

    async fn stop(&self, vm: &LocalVm) -> Result<(), AgentError> {
        lock(&self.stopped).push(vm.clone());
        Ok(())
    }

    async fn destroy(&self, vm: &LocalVm) -> Result<(), AgentError> {
        lock(&self.vms).retain(|existing| existing != vm);
        Ok(())
    }
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
