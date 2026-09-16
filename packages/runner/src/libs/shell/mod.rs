//! Host-side read-only filesystem capabilities. Only the MCP broker calls it.
use crate::libs::audit;
use crate::libs::error::AgentError;
use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tokio::process::Command;

const MAX_PATH: usize = 4096;
const MAX_FILE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_ENTRIES: usize = 1000;
const MAX_MATCHES: usize = 200;
const MAX_LINE: usize = 4096;
const MAX_GREP_FILES: usize = 5000;
const MAX_OUTPUT: usize = 1024 * 1024;
const MAX_CALLS: u32 = 600;
const WINDOW: Duration = Duration::from_secs(60);
const GIT_TIMEOUT: Duration = Duration::from_secs(10);

const GIT_STATUS: &[&str] = &["status", "--porcelain=v1"];
const GIT_DIFF: &[&str] = &["diff", "--no-color", "--no-ext-diff", "--no-textconv"];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Capability {
    ReadFile,
    ListDir,
    Grep,
    GitStatus,
    GitDiff,
}

impl Capability {
    fn name(self) -> &'static str {
        match self {
            Self::ReadFile => "read_file",
            Self::ListDir => "list_dir",
            Self::Grep => "grep",
            Self::GitStatus => "git_status",
            Self::GitDiff => "git_diff",
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyDoc {
    #[serde(default)]
    allow: Vec<Capability>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MountDoc {
    #[allow(dead_code)] // The gate serves guest paths, not the working directory of the VM.
    workdir: String,
    mounts: Vec<MountEntry>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MountEntry {
    host_path: String,
    guest_path: String,
    #[allow(dead_code)]
    // Every capability reads, so the mode of the future mount is not its business.
    mode: String,
}

#[derive(Debug)]
struct Root {
    guest: PathBuf,
    host: PathBuf,
}

/// One capability call, as the broker submits it.
#[derive(Debug)]
pub enum ShellRequest {
    ReadFile { path: String },
    ListDir { path: String },
    Grep { path: String, pattern: String },
    GitStatus { path: String },
    GitDiff { path: String },
}

impl ShellRequest {
    fn capability(&self) -> Capability {
        match self {
            Self::ReadFile { .. } => Capability::ReadFile,
            Self::ListDir { .. } => Capability::ListDir,
            Self::Grep { .. } => Capability::Grep,
            Self::GitStatus { .. } => Capability::GitStatus,
            Self::GitDiff { .. } => Capability::GitDiff,
        }
    }

    fn path(&self) -> &str {
        match self {
            Self::ReadFile { path }
            | Self::ListDir { path }
            | Self::Grep { path, .. }
            | Self::GitStatus { path }
            | Self::GitDiff { path } => path,
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct Entry {
    pub name: String,
    pub kind: &'static str,
    pub size: u64,
}

#[derive(Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Match {
    pub path: String,
    pub line: usize,
    pub text: String,
}

#[derive(Debug)]
pub enum ShellResponse {
    File(Vec<u8>),
    Entries(Vec<Entry>),
    Matches(Vec<Match>),
    Text(String),
}

/// Immutable per-Run capability set over the host paths the mount policy named.
#[derive(Debug)]
pub struct ShellGate {
    run_id: String,
    allow: BTreeSet<Capability>,
    roots: Vec<Root>,
    git_binary: PathBuf,
    window: Mutex<(Instant, u32)>,
}

impl ShellGate {
    pub fn from_snapshot(
        run_id: &str,
        shell: Option<&Value>,
        mounts: Option<&Value>,
        git_binary: &Path,
    ) -> Result<Self, AgentError> {
        let policy: PolicyDoc = match shell {
            Some(value) => {
                serde_json::from_value(value.clone()).map_err(|err| invalid(&format!("{err}")))?
            }
            None => PolicyDoc { allow: vec![] },
        };
        let mut roots = Vec::new();
        if let Some(value) = mounts {
            let doc: MountDoc = serde_json::from_value(value.clone())
                .map_err(|err| bad_mount(&format!("{err}")))?;
            for entry in doc.mounts {
                roots.push(Root {
                    guest: guest_root(&entry.guest_path)?,
                    host: host_root(&entry.host_path)?,
                });
            }
        }
        // The longest guest prefix wins, so a mount nested inside another is not shadowed by it.
        roots.sort_by_key(|root| std::cmp::Reverse(root.guest.as_os_str().len()));
        Ok(Self {
            run_id: run_id.to_owned(),
            allow: policy.allow.into_iter().collect(),
            roots,
            git_binary: git_binary.to_owned(),
            window: Mutex::new((Instant::now(), 0)),
        })
    }

    pub fn granted(&self) -> impl Iterator<Item = &'static str> + '_ {
        self.allow.iter().map(|capability| capability.name())
    }

    /// The whole capability surface: budget, grant, path confinement, then the operation.
    pub async fn call(&self, request: ShellRequest) -> Result<ShellResponse, AgentError> {
        let capability = request.capability();
        let path = request.path();
        self.spend(capability, path)?;
        if !self.allow.contains(&capability) {
            return Err(self.deny(capability, path, "capability not granted"));
        }
        let target = match self.resolve(path) {
            Ok(target) => target,
            Err(reason) => return Err(self.deny(capability, path, reason)),
        };
        let outcome = match &request {
            ShellRequest::ReadFile { .. } => read_file(&target),
            ShellRequest::ListDir { .. } => list_dir(&target),
            ShellRequest::Grep { pattern, .. } => self.grep(&target, pattern),
            ShellRequest::GitStatus { .. } => self.git(&target, GIT_STATUS).await,
            ShellRequest::GitDiff { .. } => self.git(&target, GIT_DIFF).await,
        };
        match outcome {
            Ok(response) => {
                audit::shell_allowed(&self.run_id, capability.name(), path);
                Ok(response)
            }
            Err(reason) => Err(self.deny(capability, path, reason)),
        }
    }

    /// Maps a guest path onto its mount and refuses anything that leaves it.
    fn resolve(&self, raw: &str) -> Result<PathBuf, &'static str> {
        if !normalized(raw) {
            return Err("path is not absolute and normalized");
        }
        let guest = Path::new(raw);
        let (root, rest) = self
            .roots
            .iter()
            .find_map(|root| {
                guest
                    .strip_prefix(&root.guest)
                    .ok()
                    .map(|rest| (root, rest))
            })
            .ok_or("path is outside every mount")?;
        // Canonicalization is what closes a symlink escape: the link is followed first and the
        // result has to still sit under the root.
        let target = fs::canonicalize(root.host.join(rest)).map_err(|_| "path does not resolve")?;
        if !target.starts_with(&root.host) {
            return Err("path escapes its mount");
        }
        let meta = fs::symlink_metadata(&target).map_err(|_| "path does not resolve")?;
        if meta.is_dir() {
            return Ok(target);
        }
        if !meta.is_file() {
            return Err("path is neither a regular file nor a directory");
        }
        // A hard link cannot be told from its target by path, so a linked file is refused.
        if meta.nlink() > 1 {
            return Err("path is a hard link");
        }
        Ok(target)
    }

    fn guest_of(&self, host: &Path) -> String {
        self.roots
            .iter()
            .find_map(|root| {
                host.strip_prefix(&root.host)
                    .ok()
                    .map(|rest| root.guest.join(rest).to_string_lossy().into_owned())
            })
            .unwrap_or_default()
    }

    // A literal search in process: no pattern reaches a regular expression engine or a binary,
    // so neither injection nor a pathological pattern is reachable from here.
    fn grep(&self, target: &Path, pattern: &str) -> Result<ShellResponse, &'static str> {
        if pattern.is_empty() || pattern.len() > MAX_LINE {
            return Err("pattern is empty or too long");
        }
        let mut matches = Vec::new();
        let mut files = 0usize;
        let mut stack = vec![target.to_owned()];
        while let Some(current) = stack.pop() {
            let meta = fs::symlink_metadata(&current).map_err(|_| "path is unreadable")?;
            if meta.is_symlink() {
                continue;
            }
            if meta.is_dir() {
                let mut names: Vec<PathBuf> = fs::read_dir(&current)
                    .map_err(|_| "directory is unreadable")?
                    .map(|entry| entry.map(|entry| entry.path()))
                    .collect::<Result<_, _>>()
                    .map_err(|_| "directory is unreadable")?;
                names.sort();
                stack.extend(names.into_iter().rev());
                continue;
            }
            if !meta.is_file() || meta.nlink() > 1 || meta.len() > MAX_FILE_BYTES {
                continue;
            }
            files += 1;
            if files > MAX_GREP_FILES {
                return Err("too many files to search");
            }
            let Ok(text) = fs::read_to_string(&current) else {
                continue;
            };
            let guest = self.guest_of(&current);
            for (number, line) in text.lines().enumerate() {
                if line.len() > MAX_LINE || !line.contains(pattern) {
                    continue;
                }
                matches.push(Match {
                    path: guest.clone(),
                    line: number + 1,
                    text: line.to_owned(),
                });
                // Unlike a truncated file a short match list is honest output, so it is returned.
                if matches.len() >= MAX_MATCHES {
                    return Ok(ShellResponse::Matches(matches));
                }
            }
        }
        Ok(ShellResponse::Matches(matches))
    }

    // The repository config belongs to the agent, and `diff.external`, textconv filters and
    // `core.fsmonitor` are arbitrary exec, so each one is switched off rather than trusted.
    async fn git(&self, root: &Path, args: &[&str]) -> Result<ShellResponse, &'static str> {
        if !root.is_dir() {
            return Err("path is not a directory");
        }
        let output = tokio::time::timeout(
            GIT_TIMEOUT,
            Command::new(&self.git_binary)
                .arg("--no-optional-locks")
                .arg("--no-pager")
                .arg("-C")
                .arg(root)
                .args(["-c", "core.fsmonitor="])
                .args(args)
                .current_dir(root)
                .env_clear()
                .env("GIT_CONFIG_NOSYSTEM", "1")
                .env("GIT_CONFIG_GLOBAL", "/dev/null")
                .env("GIT_OPTIONAL_LOCKS", "0")
                .env("GIT_TERMINAL_PROMPT", "0")
                .stdin(Stdio::null())
                .kill_on_drop(true)
                .output(),
        )
        .await
        .map_err(|_| "git timed out")?
        .map_err(|_| "git could not run")?;
        if !output.status.success() {
            return Err("git failed");
        }
        if output.stdout.len() > MAX_OUTPUT {
            return Err("git produced too much output");
        }
        String::from_utf8(output.stdout)
            .map(ShellResponse::Text)
            .map_err(|_| "git produced invalid utf-8")
    }

    fn spend(&self, capability: Capability, path: &str) -> Result<(), AgentError> {
        let mut window = self.window.lock().expect("window");
        let now = Instant::now();
        if now.duration_since(window.0) >= WINDOW {
            *window = (now, 0);
        }
        if window.1 >= MAX_CALLS {
            drop(window);
            return Err(self.deny(capability, path, "rate limit"));
        }
        window.1 += 1;
        Ok(())
    }

    fn deny(&self, capability: Capability, path: &str, reason: &str) -> AgentError {
        audit::shell_denied(&self.run_id, capability.name(), path, reason);
        AgentError::Runtime(format!("shell deny: {reason}"))
    }
}

fn read_file(target: &Path) -> Result<ShellResponse, &'static str> {
    let meta = fs::metadata(target).map_err(|_| "file is unreadable")?;
    if !meta.is_file() {
        return Err("path is not a regular file");
    }
    if meta.len() > MAX_FILE_BYTES {
        return Err("file is too large");
    }
    fs::read(target)
        .map(ShellResponse::File)
        .map_err(|_| "file is unreadable")
}

fn list_dir(target: &Path) -> Result<ShellResponse, &'static str> {
    if !target.is_dir() {
        return Err("path is not a directory");
    }
    let mut entries = Vec::new();
    for entry in fs::read_dir(target).map_err(|_| "directory is unreadable")? {
        if entries.len() >= MAX_ENTRIES {
            return Err("directory has too many entries");
        }
        let entry = entry.map_err(|_| "directory is unreadable")?;
        // A symlink is reported as what it is and never followed, so it names no target.
        let meta = fs::symlink_metadata(entry.path()).map_err(|_| "directory is unreadable")?;
        let kind = if meta.is_dir() {
            "dir"
        } else if meta.is_file() {
            "file"
        } else {
            "other"
        };
        entries.push(Entry {
            name: entry.file_name().to_string_lossy().into_owned(),
            kind,
            size: meta.len(),
        });
    }
    entries.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(ShellResponse::Entries(entries))
}

fn invalid(reason: &str) -> AgentError {
    AgentError::Runtime(format!("invalid shell policy: {reason}"))
}

fn bad_mount(reason: &str) -> AgentError {
    AgentError::Runtime(format!("invalid mount policy: {reason}"))
}

// Mirrors _host_path in the API's mounts.py: absolute, normalized, no control characters.
fn normalized(raw: &str) -> bool {
    let mut parts = raw.split('/');
    if raw.is_empty()
        || raw.len() > MAX_PATH
        || raw.chars().any(char::is_control)
        || parts.next() != Some("")
    {
        return false;
    }
    parts.all(|part| !part.is_empty() && part != "." && part != "..")
}

fn guest_root(raw: &str) -> Result<PathBuf, AgentError> {
    if !normalized(raw) {
        return Err(bad_mount(&format!(
            "guest path {raw:?} is not absolute and normalized"
        )));
    }
    Ok(PathBuf::from(raw))
}

fn host_root(raw: &str) -> Result<PathBuf, AgentError> {
    if !normalized(raw) {
        return Err(bad_mount(&format!(
            "host path {raw:?} is not absolute and normalized"
        )));
    }
    let root = fs::canonicalize(raw)
        .map_err(|err| bad_mount(&format!("host path {raw:?} is unusable: {err}")))?;
    if !root.is_dir() {
        return Err(bad_mount(&format!("host path {raw:?} is not a directory")));
    }
    Ok(root)
}

#[cfg(test)]
mod tests;
