mod spool;

use std::io;
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::libs::ids::random_hex;
use crate::libs::runtime::LocalVm;

pub use spool::Spool;

static SPOOL: OnceLock<Spool> = OnceLock::new();

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Event {
    pub id: String,
    pub at: u64,
    pub event: String,
    #[serde(flatten)]
    pub fields: Map<String, Value>,
}

impl Event {
    pub fn new(event: &str, fields: Map<String, Value>) -> io::Result<Self> {
        let at = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |since| since.as_secs());
        Ok(Self {
            id: format!("evt_{}", random_hex(16)?),
            at,
            event: event.into(),
            fields,
        })
    }
}

pub fn install(spool: Spool) {
    let _ = SPOOL.set(spool);
}

fn emit(event: &str, fields: Map<String, Value>) {
    let Some(spool) = SPOOL.get() else {
        return;
    };
    if let Err(err) = Event::new(event, fields).and_then(|built| spool.append(&built)) {
        tracing::error!(error = %err, event, "audit event could not be spooled");
    }
}

macro_rules! audit {
    ($level:ident, $event:literal $(, $field:ident = $value:expr)* $(,)?) => {{
        tracing::$level!(target: "audit", event = $event $(, $field = $value)*);
        emit(
            $event,
            Map::from_iter([$((stringify!($field).to_owned(), serde_json::json!($value))),*]),
        );
    }};
}

pub fn registered(runner_id: &str) {
    audit!(info, "runner_registered", runner_id = runner_id);
}

pub fn credentials_dropped(runner_id: &str) {
    audit!(warn, "runner_credentials_dropped", runner_id = runner_id);
}

pub fn run_claimed(run_id: &str) {
    audit!(info, "run_claimed", run_id = run_id);
}

pub fn run_failed(run_id: &str, reason: &str) {
    audit!(warn, "run_failed", run_id = run_id, reason = reason);
}

pub fn orphan_destroyed(vm: &LocalVm) {
    audit!(
        warn,
        "orphan_destroyed",
        vm_id = vm.vm_id.as_str(),
        run_id = vm.run_id.as_str()
    );
}

pub fn lease_fenced(vms: usize) {
    audit!(error, "lease_fenced", vms = vms);
}

pub fn image_cached(digest: &str) {
    audit!(info, "image_cached", digest = digest);
}

pub fn image_rejected(digest: &str, reason: &str) {
    audit!(warn, "image_rejected", digest = digest, reason = reason);
}

pub fn vm_created(vm_id: &str, run_id: &str) {
    audit!(info, "vm_created", vm_id = vm_id, run_id = run_id);
}

pub fn vm_stopped(vm_id: &str, run_id: &str) {
    audit!(info, "vm_stopped", vm_id = vm_id, run_id = run_id);
}

pub fn workspace_shared(run_id: &str, mode: &str) {
    audit!(info, "workspace_shared", run_id = run_id, mode = mode);
}

pub fn workspace_collected(run_id: &str, entries: usize, rejected: usize) {
    audit!(
        info,
        "workspace_collected",
        run_id = run_id,
        entries = entries,
        rejected = rejected
    );
}

pub fn merge_conflict(run_id: &str, conflicts: usize) {
    audit!(
        warn,
        "merge_conflict",
        run_id = run_id,
        conflicts = conflicts
    );
}

pub fn merge_applied(run_id: &str, applied: usize, backed_up: usize, exported: usize) {
    audit!(
        info,
        "merge_applied",
        run_id = run_id,
        applied = applied,
        backed_up = backed_up,
        exported = exported
    );
}

pub fn changes_archived(vm_id: &str, run_id: &str) {
    audit!(info, "changes_archived", vm_id = vm_id, run_id = run_id);
}

pub fn vm_destroyed(vm_id: &str, run_id: &str) {
    audit!(info, "vm_destroyed", vm_id = vm_id, run_id = run_id);
}

pub fn console_attached(vm_id: &str, run_id: &str) {
    audit!(warn, "console_attached", vm_id = vm_id, run_id = run_id);
}

pub fn network_policy_configured(run_id: &str) {
    audit!(info, "network_policy_configured", run_id = run_id);
}

pub fn network_allowed(run_id: &str, protocol: &str, host: &str, rule: &str) {
    audit!(
        info,
        "network_allowed",
        run_id = run_id,
        protocol = protocol,
        host = host,
        rule = rule
    );
}

// The host and scheme are the destination: a full URL can carry userinfo, which is a credential.
pub fn network_denied(run_id: &str, protocol: &str, host: &str, rule: &str, reason: &str) {
    audit!(
        warn,
        "network_denied",
        run_id = run_id,
        protocol = protocol,
        host = host,
        rule = rule,
        reason = reason
    );
}

pub fn shell_policy_configured(run_id: &str) {
    audit!(info, "shell_policy_configured", run_id = run_id);
}

// The guest path is what the agent asked for; the host path behind it is the host's layout.
pub fn shell_allowed(run_id: &str, capability: &str, path: &str) {
    audit!(
        info,
        "shell_allowed",
        run_id = run_id,
        capability = capability,
        path = path
    );
}

pub fn shell_denied(run_id: &str, capability: &str, path: &str, reason: &str) {
    audit!(
        warn,
        "shell_denied",
        run_id = run_id,
        capability = capability,
        path = path,
        reason = reason
    );
}

pub fn mcp_attached(run_id: &str) {
    audit!(info, "mcp_attached", run_id = run_id);
}

pub fn mcp_rejected(run_id: &str, reason: &str) {
    audit!(warn, "mcp_rejected", run_id = run_id, reason = reason);
}

pub fn mcp_policy_configured(run_id: &str) {
    audit!(info, "mcp_policy_configured", run_id = run_id);
}

// Only the secret names, which the policy document already carries.
pub fn mcp_credentials_updated(run_id: &str, names: &str) {
    audit!(
        info,
        "mcp_credentials_updated",
        run_id = run_id,
        names = names
    );
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
    audit!(
        info,
        "mcp_call",
        run_id = run_id,
        server = server,
        tool = tool,
        resource = resource,
        decision = decision,
        duration_ms = duration_ms,
        category = category
    );
}

#[cfg(test)]
mod tests;
