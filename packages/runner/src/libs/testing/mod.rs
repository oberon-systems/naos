use std::collections::{BTreeMap, HashSet};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use sha2::{Digest, Sha256};

use crate::libs::api::{
    Api, DesiredRun, DesiredState, HeartbeatReply, ImageRef, IssuedToken, LeaseGrant, Registration,
    RunSpec, RunStatus, RuntimeSpec, Transition,
};
use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;
use crate::libs::ids::hex;
use crate::libs::image::{BoxFuture, ImageSource};
use crate::libs::runtime::{LocalVm, Runtime};

pub const LEASE_ID: &str = "lease_alpha";

/// An ext4 disk built without root: `mkfs.ext4`, then `debugfs -w` runs `script` from `dir`.
pub fn ext4_image(dir: &Path, name: &str, script: &str) -> PathBuf {
    ext4_image_with(dir, name, &[], script)
}

pub fn ext4_image_with(dir: &Path, name: &str, options: &[&str], script: &str) -> PathBuf {
    let image = dir.join(name);
    std::fs::File::create(&image)
        .and_then(|file| file.set_len(32 * 1024 * 1024))
        .expect("image");
    let mkfs = Command::new(e2fs_tool("mkfs.ext4"))
        .args(["-q", "-F"])
        .args(options)
        .arg(&image)
        .output()
        .expect("mkfs.ext4");
    assert!(
        mkfs.status.success(),
        "{}",
        String::from_utf8_lossy(&mkfs.stderr)
    );
    let commands = dir.join(format!("{name}.debugfs"));
    std::fs::write(&commands, script).expect("script");
    let debugfs = Command::new(e2fs_tool("debugfs"))
        .arg("-w")
        .arg(&image)
        .arg("-f")
        .arg(&commands)
        .current_dir(dir)
        .output()
        .expect("debugfs");
    let errors: Vec<_> = String::from_utf8_lossy(&debugfs.stderr)
        .lines()
        .filter(|line| !line.starts_with("debugfs "))
        .map(str::to_owned)
        .collect();
    assert!(debugfs.status.success() && errors.is_empty(), "{errors:?}");
    image
}

pub fn e2fs_tool(name: &str) -> PathBuf {
    ["/usr/sbin", "/sbin", "/usr/bin", "/bin"]
        .iter()
        .map(|dir| Path::new(dir).join(name))
        .find(|path| path.exists())
        .unwrap_or_else(|| panic!("{name} from e2fsprogs is needed by this test"))
}

pub fn digest_of(bytes: &[u8]) -> String {
    format!("sha256:{}", hex(&Sha256::digest(bytes)))
}

pub fn spec() -> RunSpec {
    RunSpec {
        image: ImageRef {
            id: "image_alpha".into(),
            digest: format!("sha256:{}", "a".repeat(64)),
        },
        runtime: RuntimeSpec {
            cpu: 1,
            memory_mib: 512,
            disk_gib: 1,
        },
    }
}

pub fn desired_run(id: &str, status: RunStatus) -> DesiredRun {
    DesiredRun {
        id: id.into(),
        status,
        spec: spec(),
        image_url: "https://images.example.com/naos-agents-1.0.0.qcow2".into(),
        policies: BTreeMap::new(),
        credentials: BTreeMap::new(),
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
        running: true,
    }
}

pub fn dead_vm(run_id: &str) -> LocalVm {
    LocalVm {
        running: false,
        ..vm(run_id)
    }
}

#[derive(Default)]
pub struct FakeApi {
    lease_ttl: Mutex<Option<u64>>,
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

    pub fn grant_lease_ttl(&self, seconds: u64) {
        *lock(&self.lease_ttl) = Some(seconds);
    }

    fn lease(&self) -> LeaseGrant {
        LeaseGrant {
            ttl_seconds: lock(&self.lease_ttl).unwrap_or(60),
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
            lease: self.lease(),
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
            lease: self.lease(),
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

pub struct FakeSource {
    bytes: Vec<u8>,
    calls: AtomicUsize,
}

impl FakeSource {
    pub fn new(bytes: impl Into<Vec<u8>>) -> Self {
        Self {
            bytes: bytes.into(),
            calls: AtomicUsize::new(0),
        }
    }

    pub fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }
}

impl ImageSource for FakeSource {
    fn fetch<'a>(
        &'a self,
        _: &'a str,
        sink: &'a mut (dyn Write + Send),
        limit: u64,
    ) -> BoxFuture<'a, Result<u64, AgentError>> {
        Box::pin(async move {
            self.calls.fetch_add(1, Ordering::SeqCst);
            if self.bytes.len() as u64 > limit {
                return Err(AgentError::Image("image exceeds the limit".into()));
            }
            sink.write_all(&self.bytes)?;
            Ok(self.bytes.len() as u64)
        })
    }
}

#[derive(Default)]
pub struct FakeRuntime {
    vms: Mutex<Vec<LocalVm>>,
    stopped: Mutex<Vec<LocalVm>>,
    unavailable: AtomicBool,
    fail_ensure: AtomicBool,
    fail_sync: AtomicBool,
    fail_collect: AtomicBool,
    synced: Mutex<Vec<LocalVm>>,
    collected: Mutex<Vec<LocalVm>>,
    ensure_delay: Mutex<Duration>,
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

    pub fn fail_sync(&self) {
        self.fail_sync.store(true, Ordering::SeqCst);
    }

    pub fn synced(&self) -> Vec<LocalVm> {
        lock(&self.synced).clone()
    }

    pub fn fail_collect(&self) {
        self.fail_collect.store(true, Ordering::SeqCst);
    }

    pub fn collected(&self) -> Vec<LocalVm> {
        lock(&self.collected).clone()
    }

    pub fn delay_ensure(&self, delay: Duration) {
        *lock(&self.ensure_delay) = delay;
    }
}

impl Runtime for FakeRuntime {
    async fn list(&self) -> Result<Vec<LocalVm>, AgentError> {
        if self.unavailable.load(Ordering::SeqCst) {
            return Err(AgentError::Runtime("fake runtime is unavailable".into()));
        }
        Ok(self.vms())
    }

    async fn ensure(&self, run: &DesiredRun, _: &dyn ImageSource) -> Result<LocalVm, AgentError> {
        let delay = *lock(&self.ensure_delay);
        tokio::time::sleep(delay).await;
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

    async fn sync(&self, _: &DesiredRun, vm: &LocalVm) -> Result<(), AgentError> {
        if self.fail_sync.load(Ordering::SeqCst) {
            return Err(AgentError::Runtime("sync failed".into()));
        }
        lock(&self.synced).push(vm.clone());
        Ok(())
    }

    async fn stop(&self, vm: &LocalVm) -> Result<(), AgentError> {
        lock(&self.stopped).push(vm.clone());
        Ok(())
    }

    async fn collect(&self, _: &DesiredRun, vm: &LocalVm) -> Result<(), AgentError> {
        if self.fail_collect.load(Ordering::SeqCst) {
            return Err(AgentError::Runtime("collection failed".into()));
        }
        lock(&self.collected).push(vm.clone());
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
