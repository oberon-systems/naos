use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::libs::api::Api;
use crate::libs::audit;
use crate::libs::config::Config;
use crate::libs::credentials::{read_secret_file, CredentialStore, Credentials};
use crate::libs::error::AgentError;
use crate::libs::image::ImageSource;
use crate::libs::lease::LeaseClock;
use crate::libs::reconciler::reconcile;
use crate::libs::runtime::Runtime;

pub const STARTUP_GRACE: Duration = Duration::from_secs(60);
const RETRY_INTERVAL: Duration = Duration::from_secs(5);
const MIN_INTERVAL: Duration = Duration::from_secs(1);

pub struct Agent<A, R> {
    api: A,
    runtime: R,
    images: Box<dyn ImageSource>,
    name: String,
    capacity: u32,
    store: CredentialStore,
    enrollment_token_file: PathBuf,
    credentials: Option<Credentials>,
    lease: LeaseClock,
}

impl<A: Api + Sync, R: Runtime> Agent<A, R> {
    pub fn new(config: &Config, api: A, runtime: R, images: Box<dyn ImageSource>) -> Self {
        Self {
            api,
            runtime,
            images,
            name: config.name.clone(),
            capacity: config.capacity,
            store: CredentialStore::new(&config.state_dir),
            enrollment_token_file: config.enrollment_token_file.clone(),
            credentials: None,
            lease: LeaseClock::starting(Instant::now(), STARTUP_GRACE),
        }
    }

    pub fn interval(&self) -> Duration {
        self.lease
            .ttl()
            .map_or(RETRY_INTERVAL, |ttl| (ttl / 3).max(MIN_INTERVAL))
    }

    /// One pass: fence if the lease lapsed, heartbeat, then drive local VMs to the desired state.
    pub async fn cycle(&mut self) -> Result<usize, AgentError> {
        if self.lease.expired(Instant::now()) {
            self.fence().await;
        }
        let credentials = self.credentials().await?;

        let actual = self.runtime.list().await;
        let capacity = if actual.is_ok() { self.capacity } else { 0 };
        let sent_at = Instant::now();
        let reply = match self.api.heartbeat(&credentials, capacity).await {
            Err(AgentError::Unauthorized) => {
                self.drop_credentials(&credentials)?;
                return Err(AgentError::Unauthorized);
            }
            other => other?,
        };
        self.lease
            .renewed(sent_at, Duration::from_secs(reply.lease.ttl_seconds));
        let credentials = match reply.token {
            Some(token) => self.remember(Credentials {
                runner_id: credentials.runner_id,
                token: token.value,
            })?,
            None => credentials,
        };

        let actual = actual?;
        let desired = self.api.desired(&credentials).await?;
        Ok(reconcile(
            &self.api,
            &self.runtime,
            self.images.as_ref(),
            &credentials,
            &desired,
            &actual,
        )
        .await)
    }

    async fn credentials(&mut self) -> Result<Credentials, AgentError> {
        if let Some(credentials) = &self.credentials {
            return Ok(credentials.clone());
        }
        if let Some(credentials) = self.store.load()? {
            self.credentials = Some(credentials.clone());
            return Ok(credentials);
        }

        let enrollment = read_secret_file(&self.enrollment_token_file)?;
        let sent_at = Instant::now();
        let registration = self.api.register(&self.name, &enrollment).await?;
        self.lease
            .renewed(sent_at, Duration::from_secs(registration.lease.ttl_seconds));
        audit::registered(&registration.runner_id);
        self.remember(Credentials {
            runner_id: registration.runner_id,
            token: registration.token.value,
        })
    }

    fn remember(&mut self, credentials: Credentials) -> Result<Credentials, AgentError> {
        self.store.save(&credentials)?;
        self.credentials = Some(credentials.clone());
        Ok(credentials)
    }

    fn drop_credentials(&mut self, credentials: &Credentials) -> Result<(), AgentError> {
        self.credentials = None;
        self.store.clear()?;
        audit::credentials_dropped(&credentials.runner_id);
        Ok(())
    }

    async fn fence(&self) {
        let vms = match self.runtime.list().await {
            Ok(vms) => vms,
            Err(err) => {
                tracing::error!(error = %err, "cannot list VMs to fence an expired lease");
                return;
            }
        };
        if vms.is_empty() {
            return;
        }
        for vm in &vms {
            if let Err(err) = self.runtime.destroy(vm).await {
                tracing::error!(error = %err, vm_id = %vm.vm_id, "fencing failed to destroy a VM");
            }
        }
        audit::lease_fenced(vms.len());
    }
}

#[cfg(test)]
mod tests;
