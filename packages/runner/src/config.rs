use std::path::PathBuf;

use url::{Host, Url};

use crate::error::AgentError;

pub const MAX_CAPACITY: u32 = 64;
const DEFAULT_CAPACITY: u32 = 1;

#[derive(Debug, Clone)]
pub struct Config {
    pub api_url: Url,
    pub name: String,
    pub capacity: u32,
    pub state_dir: PathBuf,
    pub enrollment_token_file: PathBuf,
}

impl Config {
    pub fn from_env() -> Result<Self, AgentError> {
        Self::from_lookup(|key| std::env::var(key).ok())
    }

    pub fn from_lookup(get: impl Fn(&str) -> Option<String>) -> Result<Self, AgentError> {
        let required = |key: &str| {
            get(key)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| AgentError::Config(format!("{key} is not set")))
        };
        let capacity = match get("NAOS_AGENT_CAPACITY") {
            None => DEFAULT_CAPACITY,
            Some(raw) => parse_capacity(&raw)?,
        };
        Ok(Self {
            api_url: parse_api_url(&required("NAOS_AGENT_API_URL")?)?,
            name: parse_name(&required("NAOS_AGENT_NAME")?)?,
            capacity,
            state_dir: PathBuf::from(required("NAOS_AGENT_STATE_DIR")?),
            enrollment_token_file: PathBuf::from(required("NAOS_AGENT_ENROLLMENT_TOKEN_FILE")?),
        })
    }
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

fn parse_capacity(raw: &str) -> Result<u32, AgentError> {
    raw.parse::<u32>()
        .ok()
        .filter(|capacity| *capacity <= MAX_CAPACITY)
        .ok_or_else(|| {
            AgentError::Config(format!(
                "NAOS_AGENT_CAPACITY must be an integer in 0..={MAX_CAPACITY}"
            ))
        })
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    fn lookup(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect();
        move |key| map.get(key).cloned()
    }

    const FULL: &[(&str, &str)] = &[
        ("NAOS_AGENT_API_URL", "https://api.example.com/naos"),
        ("NAOS_AGENT_NAME", "alpha"),
        ("NAOS_AGENT_STATE_DIR", "/var/lib/naos-agent"),
        (
            "NAOS_AGENT_ENROLLMENT_TOKEN_FILE",
            "/etc/naos-agent/enrollment",
        ),
    ];

    #[test]
    fn full_config_parses_with_default_capacity() {
        let config = Config::from_lookup(lookup(FULL)).expect("valid config");

        assert_eq!(config.api_url.as_str(), "https://api.example.com/naos/");
        assert_eq!(config.capacity, DEFAULT_CAPACITY);
    }

    #[test]
    fn every_required_key_is_enforced() {
        for missing in FULL.iter().map(|(key, _)| *key) {
            let pairs: Vec<_> = FULL
                .iter()
                .copied()
                .filter(|(k, _)| *k != missing)
                .collect();
            let err = Config::from_lookup(lookup(&pairs)).expect_err(missing);
            assert!(err.to_string().contains(missing));
        }
    }

    #[test]
    fn plain_http_is_only_allowed_to_loopback() {
        for ok in [
            "https://api.example.com",
            "http://127.0.0.1:8000",
            "http://[::1]:8000",
            "http://localhost:8000",
        ] {
            assert!(parse_api_url(ok).is_ok(), "{ok}");
        }
        for bad in [
            "http://api.example.com",
            "http://192.0.2.10:8000",
            "ftp://api.example.com",
            "https://user:pass@api.example.com",
            "https://api.example.com/?x=1",
            "not a url",
        ] {
            assert!(parse_api_url(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn names_and_capacity_are_validated() {
        assert!(parse_name("alpha-01.beta_2").is_ok());
        for bad in ["", "-alpha", "alpha beta", "alpha/beta", &"a".repeat(65)] {
            assert!(parse_name(bad).is_err(), "{bad}");
        }
        assert_eq!(parse_capacity("0").ok(), Some(0));
        for bad in ["-1", "65", "x", ""] {
            assert!(parse_capacity(bad).is_err(), "{bad}");
        }
    }
}
