//! Host-side HTTP(S) capability. Only the MCP broker calls it.
use crate::libs::audit;
use crate::libs::error::AgentError;
use reqwest::header::HeaderMap;
use reqwest::{Client, Method, Response, StatusCode, Url};
use serde::Deserialize;
use serde_json::Value;
use std::net::{IpAddr, SocketAddr};
use std::sync::Mutex;
use std::time::{Duration, Instant};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_BODY: usize = 8 * 1024 * 1024;
const MAX_REQUESTS: u32 = 120;
const WINDOW: Duration = Duration::from_secs(60);
const NO_RULE: &str = "none";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyDoc {
    #[serde(default)]
    allow: Vec<RuleDoc>,
    #[serde(default)]
    deny: Vec<RuleDoc>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuleDoc {
    protocol: Option<String>,
    host: Option<String>,
    ip: Option<IpAddr>,
}

#[derive(Debug)]
struct Rule {
    protocol: Option<String>,
    host: Option<String>,
    ip: Option<IpAddr>,
}

impl Rule {
    // Every field is checked here so a match never has to fall back to "unconstrained", which
    // would turn an allow rule carrying an unusable host into allow-all.
    fn parse(doc: RuleDoc) -> Result<Self, AgentError> {
        if doc.protocol.is_none() && doc.host.is_none() && doc.ip.is_none() {
            return Err(invalid("a rule must constrain protocol, host or ip"));
        }
        let protocol = match doc.protocol {
            Some(raw) => Some(match raw.to_ascii_lowercase().as_str() {
                value @ ("http" | "https") => value.to_owned(),
                _ => return Err(invalid(&format!("protocol {raw:?} is not http or https"))),
            }),
            None => None,
        };
        let host = match doc.host {
            Some(raw) => Some(
                normalize_host(&raw)
                    .filter(|host| host.parse::<IpAddr>().is_err())
                    .ok_or_else(|| invalid(&format!("host {raw:?} is not a usable hostname")))?,
            ),
            None => None,
        };
        Ok(Self {
            protocol,
            host,
            ip: doc.ip,
        })
    }

    fn matches(&self, protocol: &str, host: &str, ips: &[IpAddr]) -> bool {
        self.protocol.as_deref().is_none_or(|v| v == protocol)
            && self.host.as_deref().is_none_or(|v| v == host)
            && self.ip.is_none_or(|v| ips.contains(&v))
    }
}

/// One outbound call, as the broker submits it. The gate owns the client, so a caller can never
/// reuse an authorization for a second destination.
#[derive(Debug)]
pub struct GateRequest {
    pub method: Method,
    pub url: String,
    pub headers: HeaderMap,
    pub body: Option<Vec<u8>>,
}

#[derive(Debug)]
pub struct GateResponse {
    pub status: StatusCode,
    pub headers: HeaderMap,
    pub body: Vec<u8>,
}

/// Immutable per-Run policy evaluated before each host-side outbound connection.
#[derive(Debug)]
pub struct NetworkGate {
    run_id: String,
    allow: Vec<Rule>,
    deny: Vec<Rule>,
    window: Mutex<(Instant, u32)>,
    forbidden: fn(IpAddr) -> bool,
    #[cfg(test)]
    resolved: Option<Vec<IpAddr>>,
}

impl NetworkGate {
    pub fn from_snapshot(run_id: &str, document: Option<&Value>) -> Result<Self, AgentError> {
        let doc: PolicyDoc = match document {
            Some(value) => {
                serde_json::from_value(value.clone()).map_err(|err| invalid(&format!("{err}")))?
            }
            None => PolicyDoc {
                allow: vec![],
                deny: vec![],
            },
        };
        Ok(Self {
            run_id: run_id.to_owned(),
            allow: doc
                .allow
                .into_iter()
                .map(Rule::parse)
                .collect::<Result<_, _>>()?,
            deny: doc
                .deny
                .into_iter()
                .map(Rule::parse)
                .collect::<Result<_, _>>()?,
            window: Mutex::new((Instant::now(), 0)),
            forbidden,
            #[cfg(test)]
            resolved: None,
        })
    }

    pub fn allows_any(&self) -> bool {
        !self.allow.is_empty()
    }

    /// The whole egress surface: budget, authorization, the pinned request and a bounded body.
    pub async fn send(&self, request: GateRequest) -> Result<GateResponse, AgentError> {
        let url = Url::parse(&request.url)
            .map_err(|err| AgentError::Runtime(format!("network deny: invalid URL: {err}")))?;
        let protocol = url.scheme().to_owned();
        let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
        self.spend(&protocol, &host)?;

        let client = self.client_for(&url).await?;
        let mut builder = client.request(request.method, url).headers(request.headers);
        if let Some(body) = request.body {
            builder = builder.body(body);
        }
        let response = builder
            .send()
            .await
            .map_err(|err| AgentError::Runtime(format!("network deny: request failed: {err}")))?;
        let status = response.status();
        let headers = response.headers().clone();
        Ok(GateResponse {
            status,
            headers,
            body: self.read_body(response, &protocol, &host).await?,
        })
    }

    fn spend(&self, protocol: &str, host: &str) -> Result<(), AgentError> {
        let mut window = self.window.lock().expect("window");
        let now = Instant::now();
        if now.duration_since(window.0) >= WINDOW {
            *window = (now, 0);
        }
        if window.1 >= MAX_REQUESTS {
            audit::network_denied(&self.run_id, protocol, host, NO_RULE, "rate limit");
            return Err(AgentError::Runtime("network deny: rate limit".into()));
        }
        window.1 += 1;
        Ok(())
    }

    // The cap covers what was read, not Content-Length, which the destination controls.
    async fn read_body(
        &self,
        mut response: Response,
        protocol: &str,
        host: &str,
    ) -> Result<Vec<u8>, AgentError> {
        let mut body: Vec<u8> = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|err| AgentError::Runtime(format!("network deny: response failed: {err}")))?
        {
            if body.len() + chunk.len() > MAX_BODY {
                audit::network_denied(&self.run_id, protocol, host, NO_RULE, "response too large");
                return Err(AgentError::Runtime(
                    "network deny: response too large".into(),
                ));
            }
            body.extend_from_slice(&chunk);
        }
        Ok(body)
    }

    fn authorize(&self, protocol: &str, host: &str, ips: &[IpAddr]) -> Result<(), AgentError> {
        match self.decide(protocol, host, ips) {
            Ok(rule) => {
                audit::network_allowed(&self.run_id, protocol, host, &rule);
                Ok(())
            }
            Err((rule, reason)) => {
                audit::network_denied(&self.run_id, protocol, host, &rule, reason);
                Err(AgentError::Runtime(format!("network deny: {reason}")))
            }
        }
    }

    fn decide(
        &self,
        protocol: &str,
        host: &str,
        ips: &[IpAddr],
    ) -> Result<String, (String, &'static str)> {
        let Some(host) = normalize_host(host) else {
            return Err((NO_RULE.to_owned(), "invalid hostname"));
        };
        if !matches!(protocol, "http" | "https") {
            return Err((NO_RULE.to_owned(), "unsupported protocol"));
        }
        if host.parse::<IpAddr>().is_ok() {
            return Err((NO_RULE.to_owned(), "direct address destination"));
        }
        if ips.is_empty() {
            return Err((NO_RULE.to_owned(), "destination did not resolve"));
        }
        if ips.iter().copied().any(self.forbidden) {
            return Err((
                NO_RULE.to_owned(),
                "destination resolves to a reserved address",
            ));
        }
        if let Some(at) = self
            .deny
            .iter()
            .position(|rule| rule.matches(protocol, &host, ips))
        {
            return Err((format!("deny[{at}]"), "explicit deny rule"));
        }
        self.allow
            .iter()
            .position(|rule| rule.matches(protocol, &host, ips))
            .map(|at| format!("allow[{at}]"))
            .ok_or_else(|| (NO_RULE.to_owned(), "no matching allow rule"))
    }

    /// Resolution is pinned, redirects are disabled so each Location is re-authorized, and the
    /// environment's proxy settings are ignored so neither can be routed around.
    async fn client_for(&self, url: &Url) -> Result<Client, AgentError> {
        let host = url
            .host_str()
            .ok_or_else(|| AgentError::Runtime("network deny: URL has no hostname".into()))?;
        let port = url
            .port_or_known_default()
            .ok_or_else(|| AgentError::Runtime("network deny: unsupported port".into()))?;
        let ips = self.lookup(host, port).await?;
        self.authorize(url.scheme(), host, &ips)?;

        let host = normalize_host(host).expect("authorized hostname");
        let mut builder = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .timeout(REQUEST_TIMEOUT);
        for ip in ips {
            builder = builder.resolve(&host, SocketAddr::new(ip, port));
        }
        builder
            .build()
            .map_err(|err| AgentError::Runtime(format!("network deny: client setup: {err}")))
    }

    async fn lookup(&self, host: &str, port: u16) -> Result<Vec<IpAddr>, AgentError> {
        #[cfg(test)]
        if let Some(ips) = &self.resolved {
            return Ok(ips.clone());
        }
        Ok(tokio::net::lookup_host((host, port))
            .await
            .map_err(|err| AgentError::Runtime(format!("network deny: DNS failure: {err}")))?
            .map(|address| address.ip())
            .collect())
    }
}

#[cfg(test)]
impl NetworkGate {
    /// Test-only: reaches a local server by allowing loopback and answering DNS from `ips`.
    pub(crate) fn local(document: &Value, ips: Vec<IpAddr>) -> Result<Self, AgentError> {
        let mut gate = Self::from_snapshot("run_a", Some(document))?;
        gate.forbidden = |ip| !ip.is_loopback() && forbidden(ip);
        gate.resolved = Some(ips);
        Ok(gate)
    }
}

fn invalid(reason: &str) -> AgentError {
    AgentError::Runtime(format!("invalid network policy: {reason}"))
}

fn normalize_host(host: &str) -> Option<String> {
    let host = host.strip_suffix('.').unwrap_or(host);
    if host.is_empty()
        || host.len() > 253
        || !host.is_ascii()
        || host.eq_ignore_ascii_case("localhost")
        || host.split('.').any(|label| {
            label.is_empty()
                || label.len() > 63
                || label.starts_with('-')
                || label.ends_with('-')
                || !label
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'-')
        })
    {
        None
    } else {
        Some(host.to_ascii_lowercase())
    }
}

fn forbidden(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v) => {
            let [a, b, ..] = v.octets();
            v.is_unspecified()
                || v.is_loopback()
                || v.is_multicast()
                || a == 0
                || a == 10
                || (a == 100 && (64..=127).contains(&b))
                || (a == 169 && b == 254)
                || (a == 172 && (16..=31).contains(&b))
                || (a == 192 && (b == 0 || b == 168))
                || (a == 198 && (18..=19).contains(&b))
                || a >= 224
        }
        IpAddr::V6(v) => {
            v.is_unspecified()
                || v.is_loopback()
                || v.is_multicast()
                || v.to_ipv4_mapped()
                    .map(IpAddr::V4)
                    .map(forbidden)
                    .unwrap_or(false)
                || (v.segments()[0] & 0xfe00) == 0xfc00
                || (v.segments()[0] & 0xffc0) == 0xfe80
        }
    }
}

#[cfg(test)]
mod tests;
