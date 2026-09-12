use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::api::Api;
use crate::audit;
use crate::config::Config;
use crate::credentials::{read_secret_file, CredentialStore, Credentials};
use crate::error::AgentError;
use crate::lease::LeaseClock;
use crate::reconciler::reconcile;
use crate::runtime::Runtime;

pub const STARTUP_GRACE: Duration = Duration::from_secs(60);
const RETRY_INTERVAL: Duration = Duration::from_secs(5);
const MIN_INTERVAL: Duration = Duration::from_secs(1);

pub struct Agent<A, R> {
    api: A,
    runtime: R,
    name: String,
    capacity: u32,
    store: CredentialStore,
    enrollment_token_file: PathBuf,
    credentials: Option<Credentials>,
    lease: LeaseClock,
}

impl<A: Api, R: Runtime> Agent<A, R> {
    pub fn new(config: &Config, api: A, runtime: R) -> Self {
        Self {
            api,
            runtime,
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
        Ok(reconcile(&self.api, &self.runtime, &credentials, &desired, &actual).await)
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
mod tests {
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::Ordering;

    use tempfile::TempDir;

    use super::*;
    use crate::api::RunStatus;
    use crate::config::parse_api_url;
    use crate::testing::{desired, desired_run, vm, FakeApi, FakeRuntime};

    fn setup(runtime: FakeRuntime) -> (TempDir, Agent<FakeApi, FakeRuntime>) {
        let dir = tempfile::tempdir().expect("tempdir");
        let enrollment = dir.path().join("enrollment");
        fs::write(&enrollment, "enroll-alpha\n").expect("write");
        fs::set_permissions(&enrollment, fs::Permissions::from_mode(0o600)).expect("chmod");
        let config = Config {
            api_url: parse_api_url("http://127.0.0.1:8000").expect("url"),
            name: "alpha".into(),
            capacity: 2,
            state_dir: dir.path().to_path_buf(),
            enrollment_token_file: enrollment,
        };
        let agent = Agent::new(&config, FakeApi::default(), runtime);
        (dir, agent)
    }

    #[tokio::test]
    async fn first_cycle_registers_and_persists_credentials() {
        let (dir, mut agent) = setup(FakeRuntime::default());
        agent.api.serve(desired(vec![]));

        agent.cycle().await.expect("cycle");

        let stored = CredentialStore::new(dir.path()).load().expect("load");
        assert_eq!(stored.map(|c| c.token), Some("token-1".into()));
        assert_eq!(agent.interval(), Duration::from_secs(20));
    }

    #[tokio::test]
    async fn restart_reuses_stored_credentials() {
        let (dir, mut agent) = setup(FakeRuntime::with_vms(vec![vm("run_a")]));
        CredentialStore::new(dir.path())
            .save(&agent.api.credentials())
            .expect("save");
        agent
            .api
            .serve(desired(vec![desired_run("run_a", RunStatus::Started)]));

        assert_eq!(agent.cycle().await.expect("cycle"), 0);

        assert_eq!(agent.api.registrations.load(Ordering::SeqCst), 0);
        assert_eq!(agent.runtime.vms(), vec![vm("run_a")]);
        assert!(agent.api.transitions().is_empty());
    }

    #[tokio::test]
    async fn rejected_credentials_are_dropped_and_reregistered() {
        let (dir, mut agent) = setup(FakeRuntime::default());
        agent.api.serve(desired(vec![]));
        agent.cycle().await.expect("first cycle");
        agent.api.reject_next_heartbeats(1);

        assert!(matches!(agent.cycle().await, Err(AgentError::Unauthorized)));
        assert_eq!(CredentialStore::new(dir.path()).load().expect("load"), None);

        agent.cycle().await.expect("third cycle");
        assert_eq!(agent.api.registrations.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn rotated_token_is_persisted() {
        let (dir, mut agent) = setup(FakeRuntime::default());
        agent.api.serve(desired(vec![]));
        agent.api.rotate_to("token-rotated");

        agent.cycle().await.expect("cycle");

        let stored = CredentialStore::new(dir.path()).load().expect("load");
        assert_eq!(stored.map(|c| c.token), Some("token-rotated".into()));
    }

    #[tokio::test]
    async fn unknown_desired_state_destroys_nothing() {
        let (_dir, mut agent) = setup(FakeRuntime::with_vms(vec![vm("run_a")]));

        assert!(matches!(agent.cycle().await, Err(AgentError::Transport(_))));

        assert_eq!(agent.runtime.vms(), vec![vm("run_a")]);
    }

    #[tokio::test]
    async fn unavailable_runtime_offers_no_capacity() {
        let runtime = FakeRuntime::default();
        runtime.make_unavailable();
        let (_dir, mut agent) = setup(runtime);
        agent.api.serve(desired(vec![]));

        assert!(matches!(
            agent.cycle().await,
            Err(AgentError::Unimplemented(_))
        ));

        assert_eq!(agent.api.capacities(), vec![0]);
        assert!(agent.api.transitions().is_empty());
    }

    #[tokio::test]
    async fn expired_lease_fences_every_vm() {
        let (_dir, mut agent) = setup(FakeRuntime::with_vms(vec![vm("run_a"), vm("run_b")]));
        agent.lease = LeaseClock::starting(Instant::now(), Duration::ZERO);

        let _ = agent.cycle().await;

        assert!(agent.runtime.vms().is_empty());
    }
}
