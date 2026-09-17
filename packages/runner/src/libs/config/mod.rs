#[cfg(test)]
mod tools;

use std::fs::{self, DirBuilder};
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde::Deserialize;
use url::{Host, Url};

use crate::libs::error::AgentError;

pub const MAX_CAPACITY: u32 = 64;
const DEFAULT_CAPACITY: u32 = 1;
const PREFIX: &str = "NAOS_AGENT_";
const ENV_FILE: &str = "NAOS_AGENT_ENV_FILE";
pub const DEFAULT_QEMU_BINARY: &str = "/usr/bin/qemu-system-x86_64";
pub const DEFAULT_QEMU_IMG: &str = "/usr/bin/qemu-img";
pub const DEFAULT_GIT_BINARY: &str = "/usr/bin/git";
pub const DEFAULT_VIRTIOFSD_BINARY: &str = "/usr/libexec/virtiofsd";
pub const DEFAULT_IMAGE_MAX_BYTES: u64 = 8 * 1024 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct Config {
    pub api_url: Url,
    pub name: String,
    pub capacity: u32,
    pub state_dir: PathBuf,
    pub enrollment_token_file: PathBuf,
    pub runtime: RuntimeConfig,
}

#[derive(Debug, Clone)]
pub struct RuntimeConfig {
    pub image_dir: PathBuf,
    pub vm_dir: PathBuf,
    pub qemu_binary: PathBuf,
    pub qemu_img: PathBuf,
    pub git_binary: PathBuf,
    pub virtiofsd_binary: PathBuf,
    pub image_max_bytes: u64,
}

/// The environment as read, before any of it is checked.
#[derive(Debug, Deserialize)]
pub struct RawConfig {
    pub api_url: String,
    pub name: String,
    pub capacity: Option<u32>,
    pub state_dir: PathBuf,
    pub enrollment_token_file: PathBuf,
}

#[derive(Debug, Default, Deserialize)]
pub struct RawRuntimeConfig {
    pub image_dir: Option<PathBuf>,
    pub vm_dir: Option<PathBuf>,
    pub qemu_binary: Option<PathBuf>,
    pub qemu_img: Option<PathBuf>,
    pub git_binary: Option<PathBuf>,
    pub virtiofsd_binary: Option<PathBuf>,
    pub image_max_bytes: Option<u64>,
}

#[derive(Debug, Default)]
pub struct UserDirs {
    pub home: Option<PathBuf>,
    pub data_home: Option<PathBuf>,
    pub state_home: Option<PathBuf>,
}

impl UserDirs {
    pub fn from_env() -> Self {
        let var = |key: &str| {
            std::env::var_os(key)
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
        };
        Self {
            home: var("HOME"),
            data_home: var("XDG_DATA_HOME"),
            state_home: var("XDG_STATE_HOME"),
        }
    }
}

impl Config {
    pub fn parse(
        raw: RawConfig,
        runtime: impl FnOnce() -> Result<RuntimeConfig, AgentError>,
    ) -> Result<Self, AgentError> {
        Ok(Self {
            api_url: parse_api_url(&raw.api_url)?,
            name: parse_name(&raw.name)?,
            capacity: raw.capacity.map_or(Ok(DEFAULT_CAPACITY), check_capacity)?,
            state_dir: raw.state_dir,
            enrollment_token_file: raw.enrollment_token_file,
            runtime: runtime()?,
        })
    }
}

impl RuntimeConfig {
    pub fn resolve(raw: RawRuntimeConfig, dirs: &UserDirs) -> Result<Self, AgentError> {
        let image_dir = match raw.image_dir {
            Some(dir) => dir,
            None => default_dir(
                dirs.data_home.as_deref(),
                dirs.home.as_deref(),
                ".local/share",
            )?
            .join("naos/vms"),
        };
        let vm_dir = match raw.vm_dir {
            Some(dir) => dir,
            None => default_dir(
                dirs.state_home.as_deref(),
                dirs.home.as_deref(),
                ".local/state",
            )?
            .join("naos/runs"),
        };
        let image_max_bytes = raw.image_max_bytes.unwrap_or(DEFAULT_IMAGE_MAX_BYTES);
        if image_max_bytes == 0 {
            return Err(AgentError::Config(
                "NAOS_AGENT_IMAGE_MAX_BYTES must be positive".into(),
            ));
        }
        Ok(Self {
            image_dir: checked_path("NAOS_AGENT_IMAGE_DIR", image_dir)?,
            vm_dir: checked_path("NAOS_AGENT_VM_DIR", vm_dir)?,
            qemu_binary: checked_path(
                "NAOS_AGENT_QEMU_BINARY",
                raw.qemu_binary
                    .unwrap_or_else(|| DEFAULT_QEMU_BINARY.into()),
            )?,
            qemu_img: checked_path(
                "NAOS_AGENT_QEMU_IMG",
                raw.qemu_img.unwrap_or_else(|| DEFAULT_QEMU_IMG.into()),
            )?,
            git_binary: checked_path(
                "NAOS_AGENT_GIT_BINARY",
                raw.git_binary.unwrap_or_else(|| DEFAULT_GIT_BINARY.into()),
            )?,
            virtiofsd_binary: checked_path(
                "NAOS_AGENT_VIRTIOFSD_BINARY",
                raw.virtiofsd_binary
                    .unwrap_or_else(|| DEFAULT_VIRTIOFSD_BINARY.into()),
            )?,
            image_max_bytes,
        })
    }
}

pub fn load() -> Result<Config, AgentError> {
    load_env_file()?;
    let raw: RawConfig = envy::prefixed(PREFIX).from_env().map_err(describe)?;
    Config::parse(raw, runtime_from_env)
}

pub fn load_runtime() -> Result<RuntimeConfig, AgentError> {
    load_env_file()?;
    runtime_from_env()
}

// A .env file is read only when NAOS_AGENT_ENV_FILE names it: one picked up from the working
// directory could redirect the API URL the agent trusts.
fn load_env_file() -> Result<(), AgentError> {
    if let Ok(path) = std::env::var(ENV_FILE) {
        dotenvy::from_path(&path)
            .map_err(|err| AgentError::Config(format!("{ENV_FILE} {path}: {err}")))?;
    }
    Ok(())
}

fn runtime_from_env() -> Result<RuntimeConfig, AgentError> {
    let raw: RawRuntimeConfig = envy::prefixed(PREFIX).from_env().map_err(describe)?;
    RuntimeConfig::resolve(raw, &UserDirs::from_env())
}

fn describe(err: envy::Error) -> AgentError {
    let envy::Error::MissingValue(field) = err else {
        return AgentError::Config(err.to_string());
    };
    AgentError::Config(format!("{PREFIX}{} is not set", field.to_uppercase()))
}

fn default_dir(
    xdg: Option<&Path>,
    home: Option<&Path>,
    fallback: &str,
) -> Result<PathBuf, AgentError> {
    xdg.map(Path::to_path_buf)
        .or_else(|| home.map(|home| home.join(fallback)))
        .ok_or_else(|| {
            AgentError::Config(
                "HOME is not set, so the default image and VM directories are unknown".into(),
            )
        })
}

// QEMU options are comma separated, so a comma inside a path would smuggle in an option of its own.
fn checked_path(key: &str, path: PathBuf) -> Result<PathBuf, AgentError> {
    let clean = path.is_absolute()
        && path
            .to_str()
            .is_some_and(|text| !text.contains(',') && !text.chars().any(char::is_control));
    if clean {
        Ok(path)
    } else {
        Err(AgentError::Config(format!(
            "{key} {} must be an absolute path without commas or control characters",
            path.display()
        )))
    }
}

pub fn prepare_private_dir(path: &Path) -> Result<(), AgentError> {
    DirBuilder::new().recursive(true).mode(0o700).create(path)?;
    let meta = fs::symlink_metadata(path)?;
    if !meta.file_type().is_dir() || meta.permissions().mode() & 0o022 != 0 {
        return Err(AgentError::Config(format!(
            "{} must be a directory, not a symlink, and not writable by group or others",
            path.display()
        )));
    }
    Ok(())
}

pub fn parse_api_url(raw: &str) -> Result<Url, AgentError> {
    let reject = |why: &str| AgentError::Config(format!("NAOS_AGENT_API_URL {why}"));
    let mut url = Url::parse(raw).map_err(|_| reject("is not a valid URL"))?;
    if !url.username().is_empty() || url.password().is_some() {
        return Err(reject("must not carry credentials"));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(reject("must not carry a query or fragment"));
    }
    let loopback = match url.host() {
        Some(Host::Ipv4(ip)) => ip.is_loopback(),
        Some(Host::Ipv6(ip)) => ip.is_loopback(),
        Some(Host::Domain(name)) => name == "localhost",
        None => return Err(reject("has no host")),
    };
    match url.scheme() {
        "https" => {}
        "http" if loopback => {}
        _ => return Err(reject("must use https unless it points to loopback")),
    }
    if !url.path().ends_with('/') {
        let path = format!("{}/", url.path());
        url.set_path(&path);
    }
    Ok(url)
}

pub fn parse_name(raw: &str) -> Result<String, AgentError> {
    let mut chars = raw.chars();
    let valid_first = chars.next().is_some_and(|c| c.is_ascii_alphanumeric());
    let valid_rest = chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'));
    if valid_first && valid_rest && raw.len() <= 64 {
        Ok(raw.to_owned())
    } else {
        Err(AgentError::Config(format!(
            "NAOS_AGENT_NAME {raw:?} is not a valid runner name"
        )))
    }
}

fn check_capacity(capacity: u32) -> Result<u32, AgentError> {
    if capacity <= MAX_CAPACITY {
        Ok(capacity)
    } else {
        Err(AgentError::Config(format!(
            "NAOS_AGENT_CAPACITY must be an integer in 0..={MAX_CAPACITY}"
        )))
    }
}

#[cfg(test)]
mod tests;
