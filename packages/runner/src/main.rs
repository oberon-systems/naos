mod libs;

use std::process::ExitCode;

use tokio::signal::unix::{signal, SignalKind};
use tracing_subscriber::EnvFilter;

use libs::agent::Agent;
use libs::api::HttpApi;
use libs::config::{self, Config};
use libs::error::AgentError;
use libs::runtime::UnavailableRuntime;

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_writer(std::io::stderr)
        .init();

    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            tracing::error!(error = %err, "naos-agent stopped");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), AgentError> {
    let config = config::load()?;
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
