#[cfg(test)]
mod tools;

use std::path::PathBuf;

use serde::Deserialize;
use url::{Host, Url};

use crate::libs::error::AgentError;

pub const MAX_CAPACITY: u32 = 64;
const DEFAULT_CAPACITY: u32 = 1;
const PREFIX: &str = "NAOS_AGENT_";
const ENV_FILE: &str = "NAOS_AGENT_ENV_FILE";

#[derive(Debug, Clone)]
pub struct Config {
    pub api_url: Url,
    pub name: String,
    pub capacity: u32,
    pub state_dir: PathBuf,
    pub enrollment_token_file: PathBuf,
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

impl TryFrom<RawConfig> for Config {
    type Error = AgentError;

    fn try_from(raw: RawConfig) -> Result<Self, Self::Error> {
        Ok(Self {
            api_url: parse_api_url(&raw.api_url)?,
            name: parse_name(&raw.name)?,
            capacity: raw.capacity.map_or(Ok(DEFAULT_CAPACITY), check_capacity)?,
            state_dir: raw.state_dir,
            enrollment_token_file: raw.enrollment_token_file,
        })
    }
}

// A .env file is read only when NAOS_AGENT_ENV_FILE names it: one picked up from the working
// directory could redirect the API URL the agent trusts.
pub fn load() -> Result<Config, AgentError> {
    if let Ok(path) = std::env::var(ENV_FILE) {
        dotenvy::from_path(&path)
            .map_err(|err| AgentError::Config(format!("{ENV_FILE} {path}: {err}")))?;
    }
    let raw: RawConfig = envy::prefixed(PREFIX).from_env().map_err(describe)?;
    Config::try_from(raw)
}

fn describe(err: envy::Error) -> AgentError {
    let envy::Error::MissingValue(field) = err else {
        return AgentError::Config(err.to_string());
    };
    AgentError::Config(format!("{PREFIX}{} is not set", field.to_uppercase()))
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
