pub mod agent;
pub mod api;
mod audit;
pub mod config;
pub mod credentials;
mod error;
pub mod lease;
pub mod reconciler;
pub mod runtime;
#[cfg(test)]
mod testing;

pub use error::AgentError;

use tokio::signal::unix::{signal, SignalKind};

use crate::agent::Agent;
use crate::api::HttpApi;
use crate::config::Config;
use crate::runtime::UnavailableRuntime;

pub fn run() -> Result<(), AgentError> {
    let config = Config::from_env()?;
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(serve(config))
}

async fn serve(config: Config) -> Result<(), AgentError> {
    let api = HttpApi::new(config.api_url.clone())?;
    let mut agent = Agent::new(&config, api, UnavailableRuntime);
    let mut terminate = signal(SignalKind::terminate())?;
    tracing::info!(name = %config.name, capacity = config.capacity, "naos-agent started");

    loop {
        match agent.cycle().await {
            Ok(0) => {}
            Ok(failures) => tracing::warn!(failures, "reconcile cycle left failed actions"),
            Err(err) => tracing::warn!(error = %err, "reconcile cycle failed"),
        }
        // Stopping the agent leaves VMs running; the next start reconciles them.
        tokio::select! {
            _ = terminate.recv() => break,
            _ = tokio::signal::ctrl_c() => break,
            () = tokio::time::sleep(agent.interval()) => {}
        }
    }
    tracing::info!("naos-agent stopped by signal");
    Ok(())
}
