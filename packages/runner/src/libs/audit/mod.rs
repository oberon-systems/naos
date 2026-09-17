use crate::libs::runtime::LocalVm;

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

pub fn image_cached(digest: &str) {
    tracing::info!(target: "audit", event = "image_cached", digest);
}

pub fn image_rejected(digest: &str, reason: &str) {
    tracing::warn!(target: "audit", event = "image_rejected", digest, reason);
}

pub fn vm_created(vm_id: &str, run_id: &str) {
    tracing::info!(target: "audit", event = "vm_created", vm_id, run_id);
}

pub fn vm_stopped(vm_id: &str, run_id: &str) {
    tracing::info!(target: "audit", event = "vm_stopped", vm_id, run_id);
}

pub fn workspace_shared(run_id: &str, mode: &str) {
    tracing::info!(target: "audit", event = "workspace_shared", run_id, mode);
}

pub fn workspace_collected(run_id: &str, entries: usize, rejected: usize) {
    tracing::info!(target: "audit", event = "workspace_collected", run_id, entries, rejected);
}

pub fn vm_destroyed(vm_id: &str, run_id: &str) {
    tracing::info!(target: "audit", event = "vm_destroyed", vm_id, run_id);
}

pub fn console_attached(vm_id: &str, run_id: &str) {
    tracing::warn!(target: "audit", event = "console_attached", vm_id, run_id);
}

pub fn network_policy_configured(run_id: &str) {
    tracing::info!(target: "audit", event = "network_policy_configured", run_id);
}

pub fn network_allowed(run_id: &str, protocol: &str, host: &str, rule: &str) {
    tracing::info!(target: "audit", event = "network_allowed", run_id, protocol, host, rule);
}

// The host and scheme are the destination: a full URL can carry userinfo, which is a credential.
pub fn network_denied(run_id: &str, protocol: &str, host: &str, rule: &str, reason: &str) {
    tracing::warn!(target: "audit", event = "network_denied", run_id, protocol, host, rule, reason);
}

pub fn shell_policy_configured(run_id: &str) {
    tracing::info!(target: "audit", event = "shell_policy_configured", run_id);
}

// The guest path is what the agent asked for; the host path behind it is the host's layout.
pub fn shell_allowed(run_id: &str, capability: &str, path: &str) {
    tracing::info!(target: "audit", event = "shell_allowed", run_id, capability, path);
}

pub fn shell_denied(run_id: &str, capability: &str, path: &str, reason: &str) {
    tracing::warn!(target: "audit", event = "shell_denied", run_id, capability, path, reason);
}

pub fn mcp_attached(run_id: &str) {
    tracing::info!(target: "audit", event = "mcp_attached", run_id);
}

pub fn mcp_rejected(run_id: &str, reason: &str) {
    tracing::warn!(target: "audit", event = "mcp_rejected", run_id, reason);
}

pub fn mcp_policy_configured(run_id: &str) {
    tracing::info!(target: "audit", event = "mcp_policy_configured", run_id);
}

// Only the secret names, which the policy document already carries.
pub fn mcp_credentials_updated(run_id: &str, names: &str) {
    tracing::info!(target: "audit", event = "mcp_credentials_updated", run_id, names);
}

// Arguments are not logged: a header or body may carry a secret. The resource is the policy prefix that matched.
pub fn mcp_call(
    run_id: &str,
    server: &str,
    tool: &str,
    resource: &str,
    decision: &str,
    duration_ms: u64,
    category: &str,
) {
    tracing::info!(target: "audit", event = "mcp_call", run_id, server, tool, resource, decision, duration_ms, category);
}
