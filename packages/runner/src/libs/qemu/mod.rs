use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::json;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;

use crate::libs::api::RuntimeSpec;
use crate::libs::error::AgentError;

pub const QMP_TIMEOUT: Duration = Duration::from_secs(10);
pub const PROCESS_PREFIX: &str = "naos-";
pub const SESSION_FW_CFG: &str = "opt/naos/session";
pub const MCP_PORT: &str = "naos.mcp";
pub const WORKSPACE_TAG: &str = "naos-workspace";
pub const UPPER_SERIAL: &str = "naos-upper";
const SANDBOX: &str = "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny";

/// How the guest sees the Run's workspace: `rw` gets an upper disk to write into.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkspaceMode {
    ReadOnly,
    ReadWrite,
}

#[derive(Debug, Clone)]
pub struct VmPaths {
    pub dir: PathBuf,
}

impl VmPaths {
    pub fn new(dir: PathBuf) -> Self {
        Self { dir }
    }

    pub fn meta(&self) -> PathBuf {
        self.dir.join("vm.json")
    }

    pub fn session(&self) -> PathBuf {
        self.dir.join("session.json")
    }

    pub fn overlay(&self) -> PathBuf {
        self.dir.join("overlay.qcow2")
    }

    pub fn qmp(&self) -> PathBuf {
        self.dir.join("qmp.sock")
    }

    pub fn console(&self) -> PathBuf {
        self.dir.join("console.sock")
    }

    pub fn mcp(&self) -> PathBuf {
        self.dir.join("mcp.sock")
    }

    pub fn boot_log(&self) -> PathBuf {
        self.dir.join("boot.log")
    }

    pub fn qemu_log(&self) -> PathBuf {
        self.dir.join("qemu.log")
    }

    pub fn fs_socket(&self) -> PathBuf {
        self.dir.join("fs.sock")
    }

    pub fn upper(&self) -> PathBuf {
        self.dir.join("upper.img")
    }

    pub fn virtiofsd_log(&self) -> PathBuf {
        self.dir.join("virtiofsd.log")
    }

    pub fn diff(&self) -> PathBuf {
        self.dir.join("diff.json")
    }
}

/// The complete QEMU command line: every device and host path the guest can reach is listed here.
pub fn argv(
    vm_id: &str,
    base: &Path,
    paths: &VmPaths,
    runtime: &RuntimeSpec,
    workspace: Option<WorkspaceMode>,
) -> Vec<OsString> {
    let text = |path: PathBuf| path.to_string_lossy().into_owned();
    // vhost-user-fs maps guest memory into virtiofsd, so the RAM has to be a shared memfd.
    let machine = if workspace.is_some() {
        "q35,memory-backend=mem"
    } else {
        "q35"
    };
    let mut args: Vec<String> = vec![
        "-name".into(),
        format!("{PROCESS_PREFIX}{vm_id}"),
        "-nodefaults".into(),
        "-no-user-config".into(),
        "-machine".into(),
        machine.into(),
        "-accel".into(),
        "kvm".into(),
        "-cpu".into(),
        "host".into(),
        "-smp".into(),
        runtime.cpu.to_string(),
        "-m".into(),
        format!("{}M", runtime.memory_mib),
        "-display".into(),
        "none".into(),
        "-nic".into(),
        "none".into(),
        "-sandbox".into(),
        SANDBOX.into(),
        "-blockdev".into(),
        json!({
            "driver": "file", "node-name": "base-file",
            "filename": base.to_string_lossy(), "read-only": true,
        })
        .to_string(),
        "-blockdev".into(),
        json!({ "driver": "qcow2", "node-name": "base", "file": "base-file", "read-only": true })
            .to_string(),
        "-blockdev".into(),
        json!({ "driver": "file", "node-name": "overlay-file", "filename": text(paths.overlay()) })
            .to_string(),
        "-blockdev".into(),
        json!({ "driver": "qcow2", "node-name": "disk", "file": "overlay-file", "backing": "base" })
            .to_string(),
        "-device".into(),
        "virtio-blk-pci,drive=disk".into(),
        "-chardev".into(),
        format!(
            "socket,id=console,path={},server=on,wait=off",
            text(paths.console())
        ),
        "-serial".into(),
        "chardev:console".into(),
        "-device".into(),
        "virtio-serial-pci,id=naos-serial".into(),
        "-chardev".into(),
        format!("socket,id=mcp,path={},server=on,wait=off", text(paths.mcp())),
        "-device".into(),
        format!("virtserialport,bus=naos-serial.0,chardev=mcp,name={MCP_PORT}"),
        "-chardev".into(),
        format!("file,id=boot,path={}", text(paths.boot_log())),
        "-serial".into(),
        "chardev:boot".into(),
        "-qmp".into(),
        format!("unix:{},server=on,wait=off", text(paths.qmp())),
        "-fw_cfg".into(),
        format!("name={SESSION_FW_CFG},file={}", text(paths.session())),
    ];
    if let Some(mode) = workspace {
        args.extend([
            "-object".into(),
            format!(
                "memory-backend-memfd,id=mem,size={}M,share=on",
                runtime.memory_mib
            ),
            "-chardev".into(),
            format!("socket,id=workspace,path={}", text(paths.fs_socket())),
            "-device".into(),
            format!("vhost-user-fs-pci,chardev=workspace,tag={WORKSPACE_TAG}"),
        ]);
        if mode == WorkspaceMode::ReadWrite {
            args.extend([
                "-blockdev".into(),
                json!({ "driver": "file", "node-name": "upper-file", "filename": text(paths.upper()) })
                    .to_string(),
                "-blockdev".into(),
                json!({ "driver": "raw", "node-name": "upper", "file": "upper-file" }).to_string(),
                "-device".into(),
                format!("virtio-blk-pci,drive=upper,serial={UPPER_SERIAL}"),
            ]);
        }
    }
    args.into_iter().map(OsString::from).collect()
}

pub async fn qmp(socket: &Path, command: &str) -> Result<(), AgentError> {
    tokio::time::timeout(QMP_TIMEOUT, session(socket, command))
        .await
        .map_err(|_| AgentError::Runtime(format!("qmp {command} timed out")))?
}

async fn session(socket: &Path, command: &str) -> Result<(), AgentError> {
    let stream = UnixStream::connect(socket).await?;
    let (read, mut write) = stream.into_split();
    let mut lines = BufReader::new(read).lines();
    lines.next_line().await?;
    for execute in ["qmp_capabilities", command] {
        let request = format!("{}\n", json!({ "execute": execute }));
        write.write_all(request.as_bytes()).await?;
        loop {
            let line = lines
                .next_line()
                .await?
                .ok_or_else(|| AgentError::Runtime(format!("qmp closed during {execute}")))?;
            let reply: serde_json::Value = serde_json::from_str(&line)
                .map_err(|err| AgentError::Runtime(format!("qmp reply: {err}")))?;
            if reply.get("return").is_some() {
                break;
            }
            if let Some(error) = reply.get("error") {
                return Err(AgentError::Runtime(format!("qmp {execute}: {error}")));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests;
