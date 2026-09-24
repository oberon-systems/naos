use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::libs::api::{Api, Beat};
use crate::libs::audit::{self, Spool};
use crate::libs::config::Config;
use crate::libs::credentials::{read_secret_file, CredentialStore, Credentials};
use crate::libs::error::AgentError;
use crate::libs::image::ImageSource;
use crate::libs::lease::LeaseClock;
use crate::libs::placement::Placement;
use crate::libs::reconciler::reconcile;
use crate::libs::runtime::Runtime;

pub const STARTUP_GRACE: Duration = Duration::from_secs(60);
const RETRY_INTERVAL: Duration = Duration::from_secs(5);
const MIN_INTERVAL: Duration = Duration::from_secs(1);
const EVENT_BATCH: usize = 1000;
const EVENT_BATCHES: usize = 10;

pub struct Agent<A, R> {
    api: A,
    runtime: R,
    images: Box<dyn ImageSource>,
    name: String,
    capacity: u32,
    placement: Placement,
    store: CredentialStore,
    spool: Spool,
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
            placement: Placement::detect(config),
            store: CredentialStore::new(&config.state_dir),
            spool: Spool::new(&config.state_dir),
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

    /// One pass: fence if the lease lapsed, heartbeat, drive local VMs to the desired state, report.
    pub async fn cycle(&mut self) -> Result<usize, AgentError> {
        if self.lease.expired(Instant::now()) {
            self.fence().await;
        }
        let credentials = self.credentials().await?;

        let actual = self.runtime.list().await;
        let capacity = if actual.is_ok() { self.capacity } else { 0 };
        let beat = Beat {
            capacity,
            interval_seconds: self.interval().as_secs(),
            placement: &self.placement,
        };
        let beat = heartbeat(
            &self.api,
            &mut self.lease,
            &self.store,
            &mut self.credentials,
            &credentials,
            &beat,
        )
        .await;
        let credentials = match beat {
            Err(AgentError::Unauthorized) => {
                self.drop_credentials(&credentials)?;
                return Err(AgentError::Unauthorized);
            }
            other => other?,
        };

        let actual = actual?;
        let desired = self.api.desired(&credentials).await?;
        let interval = self.interval();
        let mut reconciling = std::pin::pin!(reconcile(
            &self.api,
            &self.runtime,
            self.images.as_ref(),
            &credentials,
            &desired,
            &actual,
        ));
        // Image downloads and VM boots outlast the lease, so it is renewed while they run.
        let mut current = credentials.clone();
        let failures = loop {
            tokio::select! {
                failures = &mut reconciling => break failures,
                () = tokio::time::sleep(interval) => {
                    let beat = Beat {
                        capacity: self.capacity,
                        interval_seconds: interval.as_secs(),
                        placement: &self.placement,
                    };
                    let beat = heartbeat(
                        &self.api,
                        &mut self.lease,
                        &self.store,
                        &mut self.credentials,
                        &current,
                        &beat,
                    )
                    .await;
                    match beat {
                        Ok(renewed) => current = renewed,
                        Err(err) => tracing::warn!(error = %err, "heartbeat during reconcile failed"),
                    }
                }
            }
        };
        self.report_events(&current).await;
        Ok(failures)
    }

    async fn report_events(&self, credentials: &Credentials) {
        for _ in 0..EVENT_BATCHES {
            let batch = match self.spool.take(EVENT_BATCH) {
                Ok(batch) => batch,
                Err(err) => {
                    tracing::error!(error = %err, "cannot read the audit spool");
                    return;
                }
            };
            if !batch.events.is_empty() {
                match self.api.report_events(credentials, &batch.events).await {
                    Ok(reply) if !reply.refused.is_empty() => tracing::warn!(
                        refused = ?reply.refused,
                        "the API refused audit events of runs it does not bind to this runner"
                    ),
                    Ok(_) => {}
                    Err(AgentError::Api {
                        status: 422,
                        detail,
                    }) => tracing::error!(
                        events = batch.events.len(),
                        detail = %detail,
                        "the API rejected an audit batch; dropping it"
                    ),
                    Err(err) => {
                        tracing::warn!(error = %err, "audit events stay spooled");
                        return;
                    }
                }
            }
            if let Err(err) = self.spool.ack(&batch) {
                tracing::error!(error = %err, "cannot trim the audit spool");
                return;
            }
            if batch.events.len() < EVENT_BATCH {
                return;
            }
        }
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
        let registration = self
            .api
            .register(&self.name, &self.placement, &enrollment)
            .await?;
        self.lease
            .renewed(sent_at, Duration::from_secs(registration.lease.ttl_seconds));
        audit::registered(&registration.runner_id);
        remember(
            &self.store,
            &mut self.credentials,
            Credentials {
                runner_id: registration.runner_id,
                token: registration.token.value,
            },
        )
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
        // A stopped VM runs nothing, and its upper disk may still wait for a merge.
        let vms: Vec<_> = vms.into_iter().filter(|vm| vm.running).collect();
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

async fn heartbeat<A: Api>(
    api: &A,
    lease: &mut LeaseClock,
    store: &CredentialStore,
    cached: &mut Option<Credentials>,
    credentials: &Credentials,
    beat: &Beat<'_>,
) -> Result<Credentials, AgentError> {
    let sent_at = Instant::now();
    let reply = api.heartbeat(credentials, beat).await?;
    lease.renewed(sent_at, Duration::from_secs(reply.lease.ttl_seconds));
    match reply.token {
        Some(token) => remember(
            store,
            cached,
            Credentials {
                runner_id: credentials.runner_id.clone(),
                token: token.value,
            },
        ),
        None => Ok(credentials.clone()),
    }
}

fn remember(
    store: &CredentialStore,
    cached: &mut Option<Credentials>,
    credentials: Credentials,
) -> Result<Credentials, AgentError> {
    store.save(&credentials)?;
    *cached = Some(credentials.clone());
    Ok(credentials)
}

#[cfg(test)]
mod tests;
