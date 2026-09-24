use serde::Serialize;

use crate::libs::config::Config;

const OS_RELEASE: &str = "/etc/os-release";
const MAX_HOST: usize = 253;
const MAX_PLATFORM: usize = 128;

/// Where the agent runs, as the operator sees it; the API adds the address it came from.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Placement {
    pub host: Option<String>,
    pub zone: Option<String>,
    pub platform: String,
    pub version: String,
    pub labels: Vec<String>,
}

impl Placement {
    pub fn detect(config: &Config) -> Self {
        let hostname = nix::unistd::gethostname()
            .ok()
            .and_then(|name| name.into_string().ok());
        let os_release = std::fs::read_to_string(OS_RELEASE).ok();
        Self {
            host: hostname.as_deref().and_then(host),
            zone: config.zone.clone(),
            platform: platform(
                std::env::consts::OS,
                std::env::consts::ARCH,
                os_release.as_deref(),
            ),
            version: env!("CARGO_PKG_VERSION").into(),
            labels: config.labels.clone(),
        }
    }
}

fn host(name: &str) -> Option<String> {
    let valid = !name.is_empty()
        && name.len() <= MAX_HOST
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'));
    valid.then(|| name.to_owned())
}

fn arch(raw: &str) -> &str {
    match raw {
        "x86_64" => "amd64",
        "aarch64" => "arm64",
        other => other,
    }
}

fn pretty_name(os_release: &str) -> Option<String> {
    let value = os_release
        .lines()
        .find_map(|line| line.strip_prefix("PRETTY_NAME="))?
        .trim()
        .trim_matches(|c| c == '"' || c == '\'');
    let clean: String = value.chars().filter(|c| !c.is_control()).collect();
    (!clean.is_empty()).then_some(clean)
}

fn platform(os: &str, cpu: &str, os_release: Option<&str>) -> String {
    let base = format!("{os}/{}", arch(cpu));
    let mut full = match os_release.and_then(pretty_name) {
        Some(pretty) => format!("{base} \u{b7} {pretty}"),
        None => base,
    };
    full.truncate(full.floor_char_boundary(MAX_PLATFORM));
    full
}

#[cfg(test)]
mod tests;
