use crate::runtime::LocalVm;

pub fn registered(runner_id: &str) {
    tracing::info!(target: "audit", event = "runner_registered", runner_id);
}

pub fn credentials_dropped(runner_id: &str) {
    tracing::warn!(target: "audit", event = "runner_credentials_dropped", runner_id);
}

pub fn run_claimed(run_id: &str) {
    tracing::info!(target: "audit", event = "run_claimed", run_id);
}

pub fn run_failed(run_id: &str, reason: &str) {
    tracing::warn!(target: "audit", event = "run_failed", run_id, reason);
}

pub fn orphan_destroyed(vm: &LocalVm) {
    tracing::warn!(target: "audit", event = "orphan_destroyed", vm_id = %vm.vm_id, run_id = %vm.run_id);
}

pub fn lease_fenced(vms: usize) {
    tracing::error!(target: "audit", event = "lease_fenced", vms);
}
