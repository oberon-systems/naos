use std::future::Future;

use crate::libs::api::DesiredRun;
use crate::libs::error::AgentError;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalVm {
    pub vm_id: String,
    pub run_id: String,
}

pub trait Runtime {
    fn list(&self) -> impl Future<Output = Result<Vec<LocalVm>, AgentError>> + Send;

    /// Returns the VM already running for `run.id` instead of creating a second one.
    fn ensure(&self, run: &DesiredRun) -> impl Future<Output = Result<LocalVm, AgentError>> + Send;

    fn stop(&self, vm: &LocalVm) -> impl Future<Output = Result<(), AgentError>> + Send;

    fn destroy(&self, vm: &LocalVm) -> impl Future<Output = Result<(), AgentError>> + Send;
}

pub struct UnavailableRuntime;

const UNAVAILABLE: &str = "qemu runtime";

impl Runtime for UnavailableRuntime {
    async fn list(&self) -> Result<Vec<LocalVm>, AgentError> {
        Err(AgentError::Unimplemented(UNAVAILABLE))
    }

    async fn ensure(&self, _: &DesiredRun) -> Result<LocalVm, AgentError> {
        Err(AgentError::Unimplemented(UNAVAILABLE))
    }

    async fn stop(&self, _: &LocalVm) -> Result<(), AgentError> {
        Err(AgentError::Unimplemented(UNAVAILABLE))
    }

    async fn destroy(&self, _: &LocalVm) -> Result<(), AgentError> {
        Err(AgentError::Unimplemented(UNAVAILABLE))
    }
}
