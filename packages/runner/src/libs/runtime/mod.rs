use std::collections::{BTreeMap, HashMap};
use std::ffi::{OsStr, OsString};
use std::fmt::Display;
use std::fs::{self, DirBuilder, OpenOptions};
use std::future::Future;
use std::io::{ErrorKind, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use nix::errno::Errno;
use nix::sys::signal::{kill, Signal};
use nix::unistd::Pid;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::net::UnixStream;
use tokio::process::Command;
use tokio::task::{AbortHandle, JoinSet};
use tokio_vsock::{VsockAddr, VsockListener, VMADDR_CID_ANY};

use crate::libs::api::DesiredRun;
use crate::libs::audit;
use crate::libs::config::{prepare_private_dir, RuntimeConfig};
use crate::libs::error::AgentError;
use crate::libs::ids::random_hex;
use crate::libs::image::{ImageCache, ImageSource};
use crate::libs::mcp::{self, McpGate};
use crate::libs::model::{self, ModelGate};
use crate::libs::network::NetworkGate;
use crate::libs::overlay::merge::{Decision, Outcome};
use crate::libs::overlay::{self, Diff};
use crate::libs::qemu::{self, VmPaths, WorkspaceMode, PROCESS_PREFIX};
use crate::libs::shell::ShellGate;

const READY_TIMEOUT: Duration = Duration::from_secs(30);
const POWERDOWN_TIMEOUT: Duration = Duration::from_secs(30);
const KILL_TIMEOUT: Duration = Duration::from_secs(5);
const POLL_INTERVAL: Duration = Duration::from_millis(100);
const GIB: u64 = 1024 * 1024 * 1024;
const ARCHIVE_DIR: &str = "archive";
const PRIVATE_MODE: u32 = 0o600;
const DEFAULT_AGENT: &str = "claude";
const VIRTIOFSD_MIN: (u64, u64) = (1, 13);
// The uid and gid of `naos`, the first user setup-alpine creates in the image.
const GUEST_ID: u32 = 1000;
const VHOST_VSOCK: &str = "/dev/vhost-vsock";
// Guest CIDs are host-wide; 0 to 2 are reserved and low numbers are what other tools pick.
const CID_FIRST: u32 = 1000;
const CID_LAST: u32 = 0x7fff_ffff;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalVm {
    pub vm_id: String,
    pub run_id: String,
    pub running: bool,
}

pub trait Runtime {
    fn list(&self) -> impl Future<Output = Result<Vec<LocalVm>, AgentError>> + Send;

    /// Returns the VM already running for `run.id` instead of creating a second one.
    fn ensure(
        &self,
        run: &DesiredRun,
        images: &dyn ImageSource,
    ) -> impl Future<Output = Result<LocalVm, AgentError>> + Send;

    /// Keeps a running VM in step with its Run: fresh credentials and a live MCP session.
    fn sync(
        &self,
        run: &DesiredRun,
        vm: &LocalVm,
    ) -> impl Future<Output = Result<(), AgentError>> + Send;

    fn stop(&self, vm: &LocalVm) -> impl Future<Output = Result<(), AgentError>> + Send;

    /// Writes the workspace diff of a stopped Run once and returns it; a Run without a writable
    /// workspace gets an empty one.
    fn collect(
        &self,
        run: &DesiredRun,
        vm: &LocalVm,
    ) -> impl Future<Output = Result<Diff, AgentError>> + Send;

    /// Applies the decided part of the collected diff to the workspace; repeating it returns the
    /// same result.
    fn merge(
        &self,
        run: &DesiredRun,
        vm: &LocalVm,
        decision: &Decision,
    ) -> impl Future<Output = Result<Outcome, AgentError>> + Send;

    /// Removes the VM; one that holds the agent's changes is archived instead of deleted.
    fn destroy(&self, vm: &LocalVm) -> impl Future<Output = Result<(), AgentError>> + Send;

    /// Lets go of what every Run holds in memory: the lease is gone, so nothing is served under it.
    fn revoke(&self);
}

#[derive(Debug, Serialize, Deserialize)]
struct VmMeta {
    vm_id: String,
    run_id: String,
    image_id: String,
    digest: String,
    #[serde(default)]
    model_cid: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct MountDoc {
    workdir: String,
    mounts: Vec<MountEntry>,
}

#[derive(Debug, Deserialize)]
struct MountEntry {
    host_path: String,
    guest_path: String,
    mode: String,
}

/// The mount the guest works in, shared read-only by virtiofsd.
#[derive(Debug)]
struct Workspace {
    host: PathBuf,
    guest: String,
    mode: WorkspaceMode,
}

/// The host-side capabilities of one Run, built once and dropped with its VM.
#[derive(Debug)]
pub struct RunGates {
    pub network: NetworkGate,
    pub shell: ShellGate,
    pub mcp: McpGate,
    pub model: ModelGate,
}

pub struct QemuRuntime {
    vm_dir: PathBuf,
    images: ImageCache,
    qemu_binary: PathBuf,
    qemu_img: PathBuf,
    git_binary: PathBuf,
    virtiofsd_binary: PathBuf,
    gates: Mutex<HashMap<String, Arc<RunGates>>>,
    sessions: Mutex<HashMap<String, AbortHandle>>,
    model_sessions: Mutex<HashMap<String, AbortHandle>>,
}

impl QemuRuntime {
    pub fn new(config: &RuntimeConfig) -> Result<Self, AgentError> {
        prepare_private_dir(&config.image_dir)?;
        prepare_private_dir(&config.vm_dir)?;
        Ok(Self {
            vm_dir: config.vm_dir.clone(),
            images: ImageCache::new(config.image_dir.clone(), config.image_max_bytes),
            qemu_binary: config.qemu_binary.clone(),
            qemu_img: config.qemu_img.clone(),
            git_binary: config.git_binary.clone(),
            virtiofsd_binary: config.virtiofsd_binary.clone(),
            gates: Mutex::new(HashMap::new()),
            sessions: Mutex::new(HashMap::new()),
            model_sessions: Mutex::new(HashMap::new()),
        })
    }

    /// Admission check and gate registration: an unusable policy fails the run before an image is
    /// ever fetched, and every call hands the kept MCP gate the latest credentials.
    fn register(&self, run: &DesiredRun) -> Result<(), AgentError> {
        let policy = |kind: &str| run.policies.get(kind).and_then(Option::as_ref);
        let gates = RunGates {
            network: NetworkGate::from_snapshot(&run.id, policy("network"))?,
            shell: ShellGate::from_snapshot(
                &run.id,
                policy("shell"),
                policy("mount"),
                &self.git_binary,
            )?,
            mcp: McpGate::from_snapshot(&run.id, policy("mcp"))?,
            model: ModelGate::from_snapshot(&run.id, policy("model"))?,
        };
        let granted: Vec<&str> = run
            .granted_policies()
            .into_iter()
            .filter(|kind| !matches!(*kind, "network" | "shell" | "mount" | "mcp" | "model"))
            .collect();
        if !granted.is_empty() {
            return Err(AgentError::Runtime(format!(
                "run {} grants {} which this runtime cannot enforce yet",
                run.id,
                granted.join(", ")
            )));
        }
        // A reconcile keeps the gate it registered, so budgets survive a tick; only the mcp
        // policy can change under a running VM, and its gate swaps it in place.
        let kept = self
            .gates
            .lock()
            .expect("gates")
            .entry(run.id.clone())
            .or_insert_with(|| Arc::new(gates))
            .clone();
        kept.mcp.replace(policy("mcp"));
        kept.mcp.refresh(&run.credentials, &run.secrets);
        kept.model.refresh(&run.credentials);
        Ok(())
    }

    /// The gates of a running Run, which its MCP session borrows.
    pub fn gates(&self, run_id: &str) -> Option<Arc<RunGates>> {
        self.gates.lock().expect("gates").get(run_id).cloned()
    }

    /// Lets go of everything held for one Run: its gates with their credentials and secrets,
    /// the guest sessions that borrowed them and the sessions it opened on external servers.
    fn release(&self, run_id: &str) {
        let gates = self.gates.lock().expect("gates").remove(run_id);
        if let Some(session) = self.sessions.lock().expect("sessions").remove(run_id) {
            session.abort();
        }
        if let Some(session) = self.model_sessions.lock().expect("sessions").remove(run_id) {
            session.abort();
        }
        if let Some(gates) = gates {
            gates.mcp.close();
            gates.model.refresh(&BTreeMap::new());
        }
    }

    /// Serves the guest's gate ports unless live sessions already do; a session that ended is
    /// replaced, which is also how a restarted runner reattaches to a running VM.
    fn attach(&self, vm: &LocalVm, paths: &VmPaths) {
        let Some(gates) = self.gates(&vm.run_id) else {
            tracing::warn!(run_id = %vm.run_id, "no gates registered, the ports stay closed");
            return;
        };
        self.attach_mcp(vm, paths, Arc::clone(&gates));
        self.attach_model(vm, paths, gates);
    }

    fn attach_mcp(&self, vm: &LocalVm, paths: &VmPaths, gates: Arc<RunGates>) {
        let mut sessions = self.sessions.lock().expect("sessions");
        if sessions
            .get(&vm.run_id)
            .is_some_and(|session| !session.is_finished())
        {
            return;
        }
        let run_id = vm.run_id.clone();
        let socket = paths.mcp();
        let task = tokio::spawn(async move {
            let stream = match UnixStream::connect(&socket).await {
                Ok(stream) => stream,
                Err(err) => {
                    tracing::warn!(run_id = %run_id, error = %err, "cannot reach the mcp port");
                    return;
                }
            };
            audit::mcp_attached(&run_id);
            let (read, write) = stream.into_split();
            if let Err(err) = mcp::serve(&run_id, &gates, read, write).await {
                tracing::warn!(run_id = %run_id, error = %err, "mcp session ended");
            }
        });
        sessions.insert(vm.run_id.clone(), task.abort_handle());
    }

    /// Serves the model gateway of a Run with a model policy: one vsock listener per VM, each
    /// guest connection its own task, and a connection from any other CID refused.
    fn attach_model(&self, vm: &LocalVm, paths: &VmPaths, gates: Arc<RunGates>) {
        let Some(cid) = read_meta(&paths.dir).and_then(|meta| meta.model_cid) else {
            return;
        };
        let mut sessions = self.model_sessions.lock().expect("sessions");
        if sessions
            .get(&vm.run_id)
            .is_some_and(|session| !session.is_finished())
        {
            return;
        }
        let run_id = vm.run_id.clone();
        let task = tokio::spawn(async move {
            // The port is the guest's CID, so every VM listens on its own and a restart finds it again.
            let listener = match VsockListener::bind(VsockAddr::new(VMADDR_CID_ANY, cid)) {
                Ok(listener) => listener,
                Err(err) => {
                    tracing::warn!(run_id = %run_id, error = %err, "cannot listen for the model gateway");
                    return;
                }
            };
            audit::model_attached(&run_id);
            let mut connections = JoinSet::new();
            loop {
                tokio::select! {
                    accepted = listener.accept() => match accepted {
                        Ok((stream, peer)) if peer.cid() == cid => {
                            let gates = Arc::clone(&gates);
                            connections.spawn(async move {
                                let (read, write) = tokio::io::split(stream);
                                if let Err(err) = model::serve(&gates.model, read, write).await {
                                    tracing::debug!(error = %err, "model connection ended");
                                }
                            });
                        }
                        Ok((_, peer)) => audit::model_rejected(
                            &run_id,
                            &format!("connection from cid {}", peer.cid()),
                        ),
                        Err(err) => {
                            tracing::warn!(run_id = %run_id, error = %err, "model gateway stopped");
                            return;
                        }
                    },
                    Some(_) = connections.join_next(), if !connections.is_empty() => {}
                }
            }
        });
        sessions.insert(vm.run_id.clone(), task.abort_handle());
    }

    fn paths(&self, vm_id: &str) -> Result<VmPaths, AgentError> {
        if !is_vm_id(vm_id) {
            return Err(AgentError::Runtime(format!(
                "refusing unsafe vm id {vm_id:?}"
            )));
        }
        Ok(VmPaths::new(self.vm_dir.join(vm_id)))
    }

    /// Moves the agent's changes, the diff and the merge record out of the VM directory; they stay
    /// under `archive/<vm_id>` until an operator removes them.
    fn archive(&self, paths: &VmPaths) -> Result<(), AgentError> {
        let archive = self.vm_dir.join(ARCHIVE_DIR);
        prepare_private_dir(&archive)?;
        let Some(vm_id) = paths.dir.file_name() else {
            return Err(AgentError::Runtime("a vm directory without a name".into()));
        };
        let target = archive.join(vm_id);
        match DirBuilder::new().mode(0o700).create(&target) {
            Err(err) if err.kind() != ErrorKind::AlreadyExists => return Err(err.into()),
            _ => {}
        }
        for kept in [paths.upper(), paths.diff(), paths.merge(), paths.meta()] {
            let Some(name) = kept.file_name() else {
                continue;
            };
            match fs::rename(&kept, target.join(name)) {
                Err(err) if err.kind() != ErrorKind::NotFound => return Err(err.into()),
                _ => {}
            }
        }
        Ok(())
    }

    async fn qemu_img(&self, args: &[&OsStr]) -> Result<Vec<u8>, AgentError> {
        let output = Command::new(&self.qemu_img)
            .args(args)
            .stdin(Stdio::null())
            .output()
            .await?;
        if !output.status.success() {
            let command = args
                .first()
                .map_or_else(String::new, |arg| arg.to_string_lossy().into());
            return Err(AgentError::Runtime(format!(
                "qemu-img {command} failed: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            )));
        }
        Ok(output.stdout)
    }

    async fn base_virtual_size(&self, base: &Path) -> Result<u64, AgentError> {
        let stdout = self
            .qemu_img(&[
                OsStr::new("info"),
                OsStr::new("-f"),
                OsStr::new("qcow2"),
                OsStr::new("--output=json"),
                base.as_os_str(),
            ])
            .await?;
        let info: Value = serde_json::from_slice(&stdout).map_err(runtime_error)?;
        // A backing file named inside the image header would make QEMU open a host path of its choosing.
        if info.get("backing-filename").is_some() || info.get("full-backing-filename").is_some() {
            return Err(AgentError::Image(
                "base image must not reference a backing file".into(),
            ));
        }
        info.get("virtual-size")
            .and_then(Value::as_u64)
            .ok_or_else(|| AgentError::Image("qemu-img did not report the image size".into()))
    }

    async fn check_virtiofsd(&self) -> Result<(), AgentError> {
        let refused =
            || AgentError::Runtime("a workspace needs virtiofsd 1.13 or newer (--readonly)".into());
        let output = Command::new(&self.virtiofsd_binary)
            .arg("--version")
            .stdin(Stdio::null())
            .output()
            .await
            .map_err(|_| refused())?;
        let text = String::from_utf8_lossy(&output.stdout);
        let version = text.split_whitespace().nth(1).and_then(|version| {
            let mut parts = version.split('.').map(str::parse::<u64>);
            Some((parts.next()?.ok()?, parts.next()?.ok()?))
        });
        match version {
            Some(found) if output.status.success() && found >= VIRTIOFSD_MIN => Ok(()),
            _ => Err(refused()),
        }
    }

    /// Starts virtiofsd over the workspace; the host refuses every write, whatever the guest mounts.
    async fn share(&self, workspace: &Workspace, paths: &VmPaths) -> Result<(), AgentError> {
        let own = fs::metadata("/proc/self")?;
        let flag = |name: &str, value: &Path| {
            let mut arg = OsString::from(name);
            arg.push(value);
            arg
        };
        let log = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(PRIVATE_MODE)
            .open(paths.virtiofsd_log())?;
        let mut child = Command::new(&self.virtiofsd_binary)
            .arg(flag("--socket-path=", &paths.fs_socket()))
            .arg(flag("--shared-dir=", &workspace.host))
            .args(["--readonly", "--sandbox=namespace", "--cache=never"])
            .arg(format!("--uid-map=:{GUEST_ID}:{}:1:", own.uid()))
            .arg(format!("--gid-map=:{GUEST_ID}:{}:1:", own.gid()))
            .current_dir(&paths.dir)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(log))
            .process_group(0)
            .spawn()?;
        let deadline = Instant::now() + READY_TIMEOUT;
        loop {
            if let Some(status) = child.try_wait()? {
                return Err(AgentError::Runtime(format!(
                    "virtiofsd exited with {status}, see {}",
                    paths.virtiofsd_log().display()
                )));
            }
            if paths.fs_socket().exists() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(AgentError::Runtime(
                    "virtiofsd did not open its socket in time".into(),
                ));
            }
            tokio::time::sleep(POLL_INTERVAL).await;
        }
    }

    async fn launch(
        &self,
        run: &DesiredRun,
        vm_id: &str,
        paths: &VmPaths,
        base: &Path,
        workspace: Option<&Workspace>,
        model_cid: Option<u32>,
    ) -> Result<(), AgentError> {
        let meta = VmMeta {
            vm_id: vm_id.to_owned(),
            run_id: run.id.clone(),
            image_id: run.spec.image.id.clone(),
            digest: run.spec.image.digest.clone(),
            model_cid,
        };
        write_private(
            &paths.meta(),
            &serde_json::to_vec(&meta).map_err(runtime_error)?,
        )?;
        let session = json!({
            "run_id": run.id,
            "vm_id": vm_id,
            "agent": DEFAULT_AGENT,
            "workspace": workspace.map(|workspace| workspace.guest.as_str()),
            "workspace_mode": workspace.map(|workspace| mode_name(workspace.mode)),
            "model_port": model_cid,
        });
        write_private(&paths.session(), session.to_string().as_bytes())?;

        let disk = u64::from(run.spec.runtime.disk_gib) * GIB;
        let size = self.base_virtual_size(base).await?;
        if disk < size {
            return Err(AgentError::Runtime(format!(
                "disk_gib {} is smaller than the image ({size} bytes)",
                run.spec.runtime.disk_gib
            )));
        }
        let overlay = paths.overlay();
        let disk_bytes = disk.to_string();
        self.qemu_img(&[
            OsStr::new("create"),
            OsStr::new("-f"),
            OsStr::new("qcow2"),
            overlay.as_os_str(),
            OsStr::new(&disk_bytes),
        ])
        .await?;
        if let Some(workspace) = workspace {
            if workspace.mode == WorkspaceMode::ReadWrite {
                OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(PRIVATE_MODE)
                    .open(paths.upper())?
                    .set_len(disk)?;
            }
            self.share(workspace, paths).await?;
        }

        let log = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(PRIVATE_MODE)
            .open(paths.qemu_log())?;
        let mut child = Command::new(&self.qemu_binary)
            .args(qemu::argv(
                vm_id,
                base,
                paths,
                &run.spec.runtime,
                workspace.map(|workspace| workspace.mode),
                model_cid,
            ))
            .current_dir(&paths.dir)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(log))
            .process_group(0)
            .spawn()?;

        let deadline = Instant::now() + READY_TIMEOUT;
        loop {
            if let Some(status) = child.try_wait()? {
                return Err(AgentError::Runtime(format!(
                    "qemu exited with {status}, see {}",
                    paths.qemu_log().display()
                )));
            }
            if qemu::qmp(&paths.qmp(), "query-status").await.is_ok() {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(AgentError::Runtime(
                    "qemu did not answer on its monitor in time".into(),
                ));
            }
            tokio::time::sleep(POLL_INTERVAL).await;
        }
    }
}

impl Runtime for QemuRuntime {
    async fn list(&self) -> Result<Vec<LocalVm>, AgentError> {
        scan(&self.vm_dir)
    }

    async fn ensure(
        &self,
        run: &DesiredRun,
        images: &dyn ImageSource,
    ) -> Result<LocalVm, AgentError> {
        self.register(run)?;
        let started = self.start(run, images).await;
        // A start that failed before a VM existed has nothing to destroy, so the gates go here.
        if started.is_err() {
            self.release(&run.id);
        }
        started
    }

    async fn sync(&self, run: &DesiredRun, vm: &LocalVm) -> Result<(), AgentError> {
        self.register(run)?;
        self.attach(vm, &self.paths(&vm.vm_id)?);
        Ok(())
    }

    async fn stop(&self, vm: &LocalVm) -> Result<(), AgentError> {
        self.release(&vm.run_id);
        let paths = self.paths(&vm.vm_id)?;
        if find_pid(&vm.vm_id).is_some() {
            let powered_down = qemu::qmp(&paths.qmp(), "system_powerdown").await.is_ok()
                && wait_gone(&vm.vm_id, POWERDOWN_TIMEOUT).await;
            if !powered_down {
                terminate(&vm.vm_id).await?;
            }
        }
        release_share(&paths).await?;
        audit::vm_stopped(&vm.vm_id, &vm.run_id);
        Ok(())
    }

    async fn collect(&self, run: &DesiredRun, vm: &LocalVm) -> Result<Diff, AgentError> {
        self.release(&vm.run_id);
        let paths = self.paths(&vm.vm_id)?;
        if let Ok(raw) = fs::read(paths.diff()) {
            return serde_json::from_slice(&raw).map_err(runtime_error);
        }
        terminate(&vm.vm_id).await?;
        release_share(&paths).await?;
        let diff = match workspace_of(run)? {
            Some(workspace) if workspace.mode == WorkspaceMode::ReadWrite => {
                let upper = paths.upper();
                tokio::task::spawn_blocking(move || overlay::collect(&upper, &workspace.host))
                    .await
                    .map_err(runtime_error)??
            }
            _ => Diff::default(),
        };
        let staged = paths.dir.join("diff.json.tmp");
        match fs::remove_file(&staged) {
            Err(err) if err.kind() != ErrorKind::NotFound => return Err(err.into()),
            _ => {}
        }
        write_private(&staged, &serde_json::to_vec(&diff).map_err(runtime_error)?)?;
        fs::rename(&staged, paths.diff())?;
        audit::workspace_collected(&vm.run_id, diff.entries.len(), diff.rejected());
        Ok(diff)
    }

    async fn merge(
        &self,
        run: &DesiredRun,
        vm: &LocalVm,
        decision: &Decision,
    ) -> Result<Outcome, AgentError> {
        let paths = self.paths(&vm.vm_id)?;
        let diff: Diff = serde_json::from_slice(&fs::read(paths.diff())?).map_err(runtime_error)?;
        let workspace = workspace_of(run)?
            .filter(|workspace| workspace.mode == WorkspaceMode::ReadWrite)
            .map(|workspace| workspace.host);
        let decision = decision.clone();
        let outcome = tokio::task::spawn_blocking(move || {
            overlay::merge::merge(
                &paths.upper(),
                workspace.as_deref(),
                &diff,
                &decision,
                &paths.merge(),
            )
        })
        .await
        .map_err(runtime_error)??;
        match &outcome {
            Outcome::Applied(report) => audit::merge_applied(
                &vm.run_id,
                report.applied.len(),
                report.backed_up.len(),
                report.exported.len(),
            ),
            Outcome::Conflict { conflicts } => audit::merge_conflict(&vm.run_id, conflicts.len()),
        }
        Ok(outcome)
    }

    async fn destroy(&self, vm: &LocalVm) -> Result<(), AgentError> {
        self.teardown(vm, true).await
    }

    fn revoke(&self) {
        let held: Vec<String> = self.gates.lock().expect("gates").keys().cloned().collect();
        for run_id in held {
            self.release(&run_id);
        }
    }
}

impl QemuRuntime {
    async fn start(
        &self,
        run: &DesiredRun,
        images: &dyn ImageSource,
    ) -> Result<LocalVm, AgentError> {
        if let Some(existing) = scan(&self.vm_dir)?
            .into_iter()
            .find(|vm| vm.run_id == run.id && vm.running)
        {
            self.attach(&existing, &self.paths(&existing.vm_id)?);
            return Ok(existing);
        }

        let workspace = workspace_of(run)?;
        if workspace.is_some() {
            self.check_virtiofsd().await?;
        }
        let model_cid = if run.policies.get("model").is_some_and(Option::is_some) {
            check_vhost_vsock()?;
            Some(random_cid()?)
        } else {
            None
        };
        let digest = run.spec.image.digest.clone();
        self.images.fetch(images, &run.image_url, &digest).await?;
        let cache = self.images.clone();
        let base = tokio::task::spawn_blocking(move || cache.open_verified(&digest))
            .await
            .map_err(runtime_error)??;
        // QEMU reopens the verified descriptor through /proc, so it has to stay open until QEMU is up.
        let base_path = PathBuf::from(format!(
            "/proc/{}/fd/{}",
            std::process::id(),
            base.as_raw_fd()
        ));

        let vm = LocalVm {
            vm_id: format!("vm_{}", random_hex(16)?),
            run_id: run.id.clone(),
            running: true,
        };
        let paths = self.paths(&vm.vm_id)?;
        DirBuilder::new().mode(0o700).create(&paths.dir)?;
        let launched = self
            .launch(
                run,
                &vm.vm_id,
                &paths,
                &base_path,
                workspace.as_ref(),
                model_cid,
            )
            .await;
        drop(base);
        match launched {
            Ok(()) => {
                let granted = |kind: &str| run.policies.get(kind).is_some_and(Option::is_some);
                if granted("network") {
                    audit::network_policy_configured(&vm.run_id);
                }
                if granted("shell") {
                    audit::shell_policy_configured(&vm.run_id);
                }
                if granted("mcp") {
                    audit::mcp_policy_configured(&vm.run_id);
                }
                if granted("model") {
                    audit::model_policy_configured(&vm.run_id);
                }
                if let Some(workspace) = &workspace {
                    audit::workspace_shared(&vm.run_id, mode_name(workspace.mode));
                }
                audit::vm_created(&vm.vm_id, &vm.run_id);
                self.attach(&vm, &paths);
                Ok(vm)
            }
            Err(err) => {
                if let Err(cleanup) = self.teardown(&vm, false).await {
                    tracing::error!(error = %cleanup, vm_id = %vm.vm_id, "cleanup after a failed start failed");
                }
                Err(err)
            }
        }
    }

    /// Stops everything the VM holds; `keep_changes` archives an upper disk instead of deleting it.
    async fn teardown(&self, vm: &LocalVm, keep_changes: bool) -> Result<(), AgentError> {
        // First, so a VM that cannot be killed or removed is left with nothing to ask for.
        self.release(&vm.run_id);
        let paths = self.paths(&vm.vm_id)?;
        terminate(&vm.vm_id).await?;
        release_share(&paths).await?;
        if keep_changes && paths.upper().exists() {
            self.archive(&paths)?;
            audit::changes_archived(&vm.vm_id, &vm.run_id);
        }
        match fs::remove_dir_all(&paths.dir) {
            Err(err) if err.kind() != ErrorKind::NotFound => return Err(err.into()),
            _ => {}
        }
        audit::vm_destroyed(&vm.vm_id, &vm.run_id);
        Ok(())
    }
}

pub fn is_vm_id(value: &str) -> bool {
    value.strip_prefix("vm_").is_some_and(|hex| {
        hex.len() == 32 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

/// Every VM directory, whether its QEMU is still alive or not; dead ones are the reconciler's to clean.
pub fn scan(vm_dir: &Path) -> Result<Vec<LocalVm>, AgentError> {
    let processes = qemu_processes();
    let mut vms = Vec::new();
    for entry in fs::read_dir(vm_dir)? {
        let entry = entry?;
        let name = entry.file_name();
        let Some(vm_id) = name.to_str().filter(|name| is_vm_id(name)) else {
            continue;
        };
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let run_id = read_meta(&entry.path())
            .filter(|meta| meta.vm_id == vm_id)
            .map(|meta| meta.run_id)
            .unwrap_or_default();
        vms.push(LocalVm {
            vm_id: vm_id.to_owned(),
            run_id,
            running: processes.contains_key(vm_id),
        });
    }
    vms.sort_by(|a, b| a.vm_id.cmp(&b.vm_id));
    Ok(vms)
}

/// The pid and argv of every process running as the agent's own user.
fn own_processes() -> Vec<(i32, Vec<Vec<u8>>)> {
    let mut found = Vec::new();
    let Ok(own_uid) = fs::metadata("/proc/self").map(|meta| meta.uid()) else {
        return found;
    };
    let Ok(entries) = fs::read_dir("/proc") else {
        return found;
    };
    for entry in entries.flatten() {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|name| name.parse::<i32>().ok())
        else {
            continue;
        };
        if entry.metadata().map(|meta| meta.uid()).ok() != Some(own_uid) {
            continue;
        }
        let Ok(cmdline) = fs::read(entry.path().join("cmdline")) else {
            continue;
        };
        let args = cmdline
            .split(|byte| *byte == 0)
            .map(<[u8]>::to_vec)
            .collect();
        found.push((pid, args));
    }
    found
}

// Matching the -name argument of our own processes survives pid reuse and agent restarts alike.
fn qemu_processes() -> HashMap<String, i32> {
    let mut found = HashMap::new();
    for (pid, args) in own_processes() {
        let vm_id = args
            .windows(2)
            .filter(|pair| pair[0] == b"-name")
            .filter_map(|pair| std::str::from_utf8(&pair[1]).ok())
            .filter_map(|name| name.strip_prefix(PROCESS_PREFIX))
            .find(|vm_id| is_vm_id(vm_id));
        if let Some(vm_id) = vm_id {
            found.insert(vm_id.to_owned(), pid);
        }
    }
    found
}

// The namespace sandbox forks, so one share can be more than one process with the same argv.
fn virtiofsd_pids(paths: &VmPaths) -> Vec<i32> {
    let mut wanted = b"--socket-path=".to_vec();
    wanted.extend_from_slice(paths.fs_socket().as_os_str().as_bytes());
    own_processes()
        .into_iter()
        .filter(|(_, args)| args.contains(&wanted))
        .map(|(pid, _)| pid)
        .collect()
}

async fn release_share(paths: &VmPaths) -> Result<(), AgentError> {
    for pid in virtiofsd_pids(paths) {
        match kill(Pid::from_raw(pid), Signal::SIGKILL) {
            Ok(()) | Err(Errno::ESRCH) => {}
            Err(err) => {
                return Err(AgentError::Runtime(format!(
                    "cannot kill virtiofsd {pid}: {err}"
                )))
            }
        }
    }
    let deadline = Instant::now() + KILL_TIMEOUT;
    while !virtiofsd_pids(paths).is_empty() {
        if Instant::now() >= deadline {
            return Err(AgentError::Runtime("virtiofsd survived SIGKILL".into()));
        }
        tokio::time::sleep(POLL_INTERVAL).await;
    }
    Ok(())
}

fn find_pid(vm_id: &str) -> Option<i32> {
    qemu_processes().remove(vm_id)
}

async fn wait_gone(vm_id: &str, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while find_pid(vm_id).is_some() {
        if Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(POLL_INTERVAL).await;
    }
    true
}

async fn terminate(vm_id: &str) -> Result<(), AgentError> {
    let Some(pid) = find_pid(vm_id) else {
        return Ok(());
    };
    match kill(Pid::from_raw(pid), Signal::SIGKILL) {
        Ok(()) | Err(Errno::ESRCH) => {}
        Err(err) => {
            return Err(AgentError::Runtime(format!(
                "cannot kill qemu {pid} of {vm_id}: {err}"
            )))
        }
    }
    if wait_gone(vm_id, KILL_TIMEOUT).await {
        Ok(())
    } else {
        Err(AgentError::Runtime(format!(
            "qemu of {vm_id} survived SIGKILL"
        )))
    }
}

fn workspace_of(run: &DesiredRun) -> Result<Option<Workspace>, AgentError> {
    let Some(document) = run.policies.get("mount").and_then(Option::as_ref) else {
        return Ok(None);
    };
    let refuse = |reason: String| AgentError::Runtime(format!("invalid mount policy: {reason}"));
    let doc: MountDoc =
        serde_json::from_value(document.clone()).map_err(|err| refuse(err.to_string()))?;
    let Some(entry) = doc
        .mounts
        .into_iter()
        .find(|entry| entry.guest_path == doc.workdir)
    else {
        return Ok(None);
    };
    let mode = match entry.mode.as_str() {
        "rw" => WorkspaceMode::ReadWrite,
        "ro" => WorkspaceMode::ReadOnly,
        other => return Err(refuse(format!("unknown workspace mode {other:?}"))),
    };
    let host = PathBuf::from(&entry.host_path);
    // A symlink anywhere on the path would share a directory the policy never named.
    match fs::canonicalize(&host) {
        Ok(canonical) if canonical == host && canonical.is_dir() => Ok(Some(Workspace {
            host: canonical,
            guest: entry.guest_path,
            mode,
        })),
        _ => Err(refuse(format!(
            "workspace {} is not a directory reached without symlinks",
            host.display()
        ))),
    }
}

fn mode_name(mode: WorkspaceMode) -> &'static str {
    match mode {
        WorkspaceMode::ReadOnly => "ro",
        WorkspaceMode::ReadWrite => "rw",
    }
}

fn check_vhost_vsock() -> Result<(), AgentError> {
    OpenOptions::new()
        .read(true)
        .write(true)
        .open(VHOST_VSOCK)
        .map(drop)
        .map_err(|err| {
            AgentError::Runtime(format!(
                "a model policy needs read and write access to {VHOST_VSOCK} ({err}), \
                 see docs/host/vhost-vsock.md"
            ))
        })
}

fn random_cid() -> Result<u32, AgentError> {
    let raw = u32::from_str_radix(&random_hex(4)?, 16).map_err(runtime_error)?;
    Ok(CID_FIRST + raw % (CID_LAST - CID_FIRST))
}

fn read_meta(dir: &Path) -> Option<VmMeta> {
    let raw = fs::read(VmPaths::new(dir.to_path_buf()).meta()).ok()?;
    serde_json::from_slice(&raw).ok()
}

fn write_private(path: &Path, body: &[u8]) -> Result<(), AgentError> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(PRIVATE_MODE)
        .open(path)?;
    file.write_all(body)?;
    Ok(())
}

fn runtime_error(err: impl Display) -> AgentError {
    AgentError::Runtime(err.to_string())
}

#[cfg(test)]
mod tests;
