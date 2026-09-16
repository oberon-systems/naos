use std::collections::HashMap;
use std::ffi::OsStr;
use std::fmt::Display;
use std::fs::{self, DirBuilder, OpenOptions};
use std::future::Future;
use std::io::{ErrorKind, Write};
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
use tokio::task::AbortHandle;

use crate::libs::api::DesiredRun;
use crate::libs::audit;
use crate::libs::config::{prepare_private_dir, RuntimeConfig};
use crate::libs::error::AgentError;
use crate::libs::ids::random_hex;
use crate::libs::image::{ImageCache, ImageSource};
use crate::libs::mcp;
use crate::libs::network::NetworkGate;
use crate::libs::qemu::{self, VmPaths, PROCESS_PREFIX};
use crate::libs::shell::ShellGate;

const READY_TIMEOUT: Duration = Duration::from_secs(30);
const POWERDOWN_TIMEOUT: Duration = Duration::from_secs(30);
const KILL_TIMEOUT: Duration = Duration::from_secs(5);
const POLL_INTERVAL: Duration = Duration::from_millis(100);
const GIB: u64 = 1024 * 1024 * 1024;
const PRIVATE_MODE: u32 = 0o600;
const DEFAULT_AGENT: &str = "claude";

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

    fn stop(&self, vm: &LocalVm) -> impl Future<Output = Result<(), AgentError>> + Send;

    fn destroy(&self, vm: &LocalVm) -> impl Future<Output = Result<(), AgentError>> + Send;
}

#[derive(Debug, Serialize, Deserialize)]
struct VmMeta {
    vm_id: String,
    run_id: String,
    image_id: String,
    digest: String,
}

/// The host-side capabilities of one Run, built once and dropped with its VM.
#[derive(Debug)]
pub struct RunGates {
    #[allow(dead_code)] // The MCP broker of prompt 06 is the only caller.
    pub network: NetworkGate,
    #[allow(dead_code)]
    pub shell: ShellGate,
}

pub struct QemuRuntime {
    vm_dir: PathBuf,
    images: ImageCache,
    qemu_binary: PathBuf,
    qemu_img: PathBuf,
    git_binary: PathBuf,
    gates: Mutex<HashMap<String, Arc<RunGates>>>,
    sessions: Mutex<HashMap<String, AbortHandle>>,
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
            gates: Mutex::new(HashMap::new()),
            sessions: Mutex::new(HashMap::new()),
        })
    }

    /// The gates of a running Run, for the MCP broker of prompt 06 to borrow.
    #[allow(dead_code)]
    pub fn gates(&self, run_id: &str) -> Option<Arc<RunGates>> {
        self.gates.lock().expect("gates").get(run_id).cloned()
    }

    /// Serves the guest's MCP port unless a live session already does; a session that ended is
    /// replaced, which is also how a restarted runner reattaches to a running VM.
    fn attach(&self, vm: &LocalVm, paths: &VmPaths) {
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
            if let Err(err) = mcp::serve(&run_id, read, write).await {
                tracing::warn!(run_id = %run_id, error = %err, "mcp session ended");
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

    async fn launch(
        &self,
        run: &DesiredRun,
        vm_id: &str,
        paths: &VmPaths,
        base: &Path,
    ) -> Result<(), AgentError> {
        let meta = VmMeta {
            vm_id: vm_id.to_owned(),
            run_id: run.id.clone(),
            image_id: run.spec.image.id.clone(),
            digest: run.spec.image.digest.clone(),
        };
        write_private(
            &paths.meta(),
            &serde_json::to_vec(&meta).map_err(runtime_error)?,
        )?;
        let session = json!({ "run_id": run.id, "vm_id": vm_id, "agent": DEFAULT_AGENT });
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

        let log = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(PRIVATE_MODE)
            .open(paths.qemu_log())?;
        let mut child = Command::new(&self.qemu_binary)
            .args(qemu::argv(vm_id, base, paths, &run.spec.runtime))
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
        // Admission check: an unusable policy fails the run before an image is ever fetched.
        let network = run.policies.get("network").and_then(Option::as_ref);
        let shell = run.policies.get("shell").and_then(Option::as_ref);
        let mounts = run.policies.get("mount").and_then(Option::as_ref);
        let gates = RunGates {
            network: NetworkGate::from_snapshot(&run.id, network)?,
            shell: ShellGate::from_snapshot(&run.id, shell, mounts, &self.git_binary)?,
        };
        let granted: Vec<&str> = run
            .granted_policies()
            .into_iter()
            .filter(|kind| !matches!(*kind, "network" | "shell" | "mount"))
            .collect();
        if !granted.is_empty() {
            return Err(AgentError::Runtime(format!(
                "run {} grants {} which this runtime cannot enforce yet",
                run.id,
                granted.join(", ")
            )));
        }
        // The policy is immutable for the life of the Run, so a reconcile keeps the gate it
        // registered: rebuilding it would hand the Run a fresh request budget every tick.
        self.gates
            .lock()
            .expect("gates")
            .entry(run.id.clone())
            .or_insert_with(|| Arc::new(gates));
        if let Some(existing) = scan(&self.vm_dir)?
            .into_iter()
            .find(|vm| vm.run_id == run.id && vm.running)
        {
            self.attach(&existing, &self.paths(&existing.vm_id)?);
            return Ok(existing);
        }

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
        let launched = self.launch(run, &vm.vm_id, &paths, &base_path).await;
        drop(base);
        match launched {
            Ok(()) => {
                if network.is_some() {
                    audit::network_policy_configured(&vm.run_id);
                }
                if shell.is_some() {
                    audit::shell_policy_configured(&vm.run_id);
                }
                audit::vm_created(&vm.vm_id, &vm.run_id);
                self.attach(&vm, &paths);
                Ok(vm)
            }
            Err(err) => {
                if let Err(cleanup) = self.destroy(&vm).await {
                    tracing::error!(error = %cleanup, vm_id = %vm.vm_id, "cleanup after a failed start failed");
                }
                Err(err)
            }
        }
    }

    async fn stop(&self, vm: &LocalVm) -> Result<(), AgentError> {
        let paths = self.paths(&vm.vm_id)?;
        if find_pid(&vm.vm_id).is_some() {
            let powered_down = qemu::qmp(&paths.qmp(), "system_powerdown").await.is_ok()
                && wait_gone(&vm.vm_id, POWERDOWN_TIMEOUT).await;
            if !powered_down {
                terminate(&vm.vm_id).await?;
            }
        }
        audit::vm_stopped(&vm.vm_id, &vm.run_id);
        Ok(())
    }

    async fn destroy(&self, vm: &LocalVm) -> Result<(), AgentError> {
        let paths = self.paths(&vm.vm_id)?;
        terminate(&vm.vm_id).await?;
        match fs::remove_dir_all(&paths.dir) {
            Err(err) if err.kind() != ErrorKind::NotFound => return Err(err.into()),
            _ => {}
        }
        self.gates.lock().expect("gates").remove(&vm.run_id);
        if let Some(session) = self.sessions.lock().expect("sessions").remove(&vm.run_id) {
            session.abort();
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

// Matching the -name argument of our own processes survives pid reuse and agent restarts alike.
fn qemu_processes() -> HashMap<String, i32> {
    let mut found = HashMap::new();
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
        let args: Vec<&[u8]> = cmdline.split(|byte| *byte == 0).collect();
        let vm_id = args
            .windows(2)
            .filter(|pair| pair[0] == b"-name")
            .filter_map(|pair| std::str::from_utf8(pair[1]).ok())
            .filter_map(|name| name.strip_prefix(PROCESS_PREFIX))
            .find(|vm_id| is_vm_id(vm_id));
        if let Some(vm_id) = vm_id {
            found.insert(vm_id.to_owned(), pid);
        }
    }
    found
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
