//! External MCP servers named by the Run's mcp policy, reached over Streamable HTTP through a gate of their own.
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, AUTHORIZATION, CONTENT_TYPE};
use reqwest::{Method, StatusCode, Url};
use serde::Deserialize;
use serde_json::{json, Value};

use super::PROTOCOL_VERSIONS;
use crate::libs::api::RunCredential;
use crate::libs::audit;
use crate::libs::error::AgentError;
use crate::libs::network::{GateRequest, NetworkGate};

const WINDOW: Duration = Duration::from_secs(60);
const MAX_PAGES: usize = 10;
const SESSION_HEADER: &str = "mcp-session-id";
const VERSION_HEADER: &str = "mcp-protocol-version";
const REDACTED: &str = "<redacted>";
const MAX_REASON: usize = 200;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyDoc {
    servers: Vec<ServerDoc>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ServerDoc {
    name: String,
    url: String,
    tools: Vec<String>,
    resources: Vec<String>,
    credential: Option<String>,
    timeout_seconds: u64,
    max_calls_per_minute: u32,
}

/// Why an upstream operation was refused, in the audit vocabulary of the broker.
#[derive(Debug)]
pub struct Failure {
    pub category: &'static str,
    pub reason: String,
}

impl Failure {
    fn denied(reason: &str) -> Self {
        Self {
            category: "denied",
            reason: reason.to_owned(),
        }
    }

    fn credential(reason: &str) -> Self {
        Self {
            category: "credential",
            reason: reason.to_owned(),
        }
    }

    fn provider(reason: String) -> Self {
        Self {
            category: "provider",
            reason,
        }
    }
}

#[derive(Debug, Clone)]
struct Handshake {
    session: Option<String>,
    version: String,
}

#[derive(Debug)]
pub struct Upstream {
    pub name: String,
    pub timeout: Duration,
    url: String,
    tools: BTreeSet<String>,
    resources: Vec<String>,
    credential: Option<String>,
    max_calls: u32,
    window: Mutex<(Instant, u32)>,
    network: NetworkGate,
    handshake: Mutex<Option<Handshake>>,
    next_id: AtomicU64,
}

impl Upstream {
    fn parse(
        run_id: &str,
        doc: ServerDoc,
        scheme: &str,
        gate: &dyn Fn(&str, &Value) -> Result<NetworkGate, AgentError>,
    ) -> Result<Self, AgentError> {
        let url = Url::parse(&doc.url).map_err(|err| invalid(&format!("{}: {err}", doc.name)))?;
        let host = url
            .host_str()
            .filter(|_| {
                url.scheme() == scheme && url.username().is_empty() && url.password().is_none()
            })
            .ok_or_else(|| invalid(&format!("{} must be reached over {scheme}", doc.name)))?;
        if doc.name.contains("__") || doc.timeout_seconds == 0 || doc.max_calls_per_minute == 0 {
            return Err(invalid(&format!("server {:?} is malformed", doc.name)));
        }
        let network = gate(
            run_id,
            &json!({ "allow": [{ "protocol": scheme, "host": host }] }),
        )?;
        Ok(Self {
            name: doc.name,
            timeout: Duration::from_secs(doc.timeout_seconds),
            url: doc.url,
            tools: doc.tools.into_iter().collect(),
            resources: doc.resources,
            credential: doc.credential,
            max_calls: doc.max_calls_per_minute,
            window: Mutex::new((Instant::now(), 0)),
            network,
            handshake: Mutex::new(None),
            next_id: AtomicU64::new(1),
        })
    }

    pub fn allows_tool(&self, tool: &str) -> bool {
        self.tools.contains(tool)
    }

    pub fn lists_tools(&self) -> bool {
        !self.tools.is_empty()
    }

    pub fn lists_resources(&self) -> bool {
        !self.resources.is_empty()
    }

    fn prefix_of(&self, uri: &str) -> Option<&str> {
        self.resources
            .iter()
            .find(|prefix| uri.starts_with(prefix.as_str()))
            .map(String::as_str)
    }

    fn spend(&self) -> Result<(), Failure> {
        let mut window = self.window.lock().expect("window");
        let now = Instant::now();
        if now.duration_since(window.0) >= WINDOW {
            *window = (now, 0);
        }
        if window.1 >= self.max_calls {
            return Err(Failure::denied("rate limit"));
        }
        window.1 += 1;
        Ok(())
    }
}

/// The external servers of one Run and the credentials the API last issued for it.
#[derive(Debug)]
pub struct McpGate {
    run_id: String,
    servers: Vec<Upstream>,
    credentials: Mutex<BTreeMap<String, RunCredential>>,
}

impl McpGate {
    pub fn from_snapshot(run_id: &str, document: Option<&Value>) -> Result<Self, AgentError> {
        Self::build(run_id, document, "https", &|run_id, policy| {
            NetworkGate::from_snapshot(run_id, Some(policy))
        })
    }

    fn build(
        run_id: &str,
        document: Option<&Value>,
        scheme: &str,
        gate: &dyn Fn(&str, &Value) -> Result<NetworkGate, AgentError>,
    ) -> Result<Self, AgentError> {
        let servers = match document {
            Some(value) => {
                let doc: PolicyDoc = serde_json::from_value(value.clone())
                    .map_err(|err| invalid(&err.to_string()))?;
                doc.servers
                    .into_iter()
                    .map(|server| Upstream::parse(run_id, server, scheme, gate))
                    .collect::<Result<_, _>>()?
            }
            None => vec![],
        };
        Ok(Self {
            run_id: run_id.to_owned(),
            servers,
            credentials: Mutex::new(BTreeMap::new()),
        })
    }

    pub fn refresh(&self, credentials: &BTreeMap<String, RunCredential>) {
        let mut held = self.credentials.lock().expect("credentials");
        if !held.keys().eq(credentials.keys()) {
            let names: Vec<&str> = credentials.keys().map(String::as_str).collect();
            audit::mcp_credentials_updated(&self.run_id, &names.join(","));
        }
        *held = credentials.clone();
    }

    pub fn servers(&self) -> &[Upstream] {
        &self.servers
    }

    pub fn server(&self, name: &str) -> Option<&Upstream> {
        self.servers.iter().find(|server| server.name == name)
    }

    pub fn has_resources(&self) -> bool {
        self.servers.iter().any(Upstream::lists_resources)
    }

    /// The API refuses overlapping prefixes, so at most one server can match.
    pub fn resource(&self, uri: &str) -> Option<(&Upstream, &str)> {
        self.servers
            .iter()
            .find_map(|server| server.prefix_of(uri).map(|prefix| (server, prefix)))
    }

    pub async fn list_tools(&self, upstream: &Upstream) -> Result<Vec<Value>, Failure> {
        let listed = self.list(upstream, "tools/list", "tools").await?;
        Ok(listed
            .into_iter()
            .filter_map(|mut tool| {
                let name = tool.get("name")?.as_str()?.to_owned();
                upstream.allows_tool(&name).then(|| {
                    tool["name"] = json!(format!("{}__{name}", upstream.name));
                    tool
                })
            })
            .collect())
    }

    pub async fn call_tool(
        &self,
        upstream: &Upstream,
        tool: &str,
        arguments: Value,
    ) -> Result<Value, Failure> {
        if !upstream.allows_tool(tool) {
            return Err(Failure::denied("tool not allowed"));
        }
        upstream.spend()?;
        let result = self
            .rpc(
                upstream,
                "tools/call",
                json!({ "name": tool, "arguments": arguments }),
            )
            .await?;
        if !result.is_object() {
            return Err(Failure::provider(
                "server returned a malformed result".into(),
            ));
        }
        Ok(result)
    }

    pub async fn list_resources(&self, upstream: &Upstream) -> Result<Vec<Value>, Failure> {
        let listed = self.list(upstream, "resources/list", "resources").await?;
        Ok(listed
            .into_iter()
            .filter(|resource| {
                resource
                    .get("uri")
                    .and_then(Value::as_str)
                    .and_then(|uri| upstream.prefix_of(uri))
                    .is_some()
            })
            .collect())
    }

    pub async fn read_resource(&self, upstream: &Upstream, uri: &str) -> Result<Value, Failure> {
        if upstream.prefix_of(uri).is_none() {
            return Err(Failure::denied("resource not allowed"));
        }
        upstream.spend()?;
        self.rpc(upstream, "resources/read", json!({ "uri": uri }))
            .await
    }

    async fn list(
        &self,
        upstream: &Upstream,
        method: &str,
        field: &str,
    ) -> Result<Vec<Value>, Failure> {
        let mut items = Vec::new();
        let mut cursor: Option<String> = None;
        for _ in 0..MAX_PAGES {
            let params = cursor.map_or_else(|| json!({}), |cursor| json!({ "cursor": cursor }));
            let page = self.rpc(upstream, method, params).await?;
            let Some(listed) = page.get(field).and_then(Value::as_array) else {
                return Err(Failure::provider(format!("server returned no {field}")));
            };
            items.extend(listed.iter().cloned());
            cursor = page
                .get("nextCursor")
                .and_then(Value::as_str)
                .map(str::to_owned);
            if cursor.is_none() {
                break;
            }
        }
        Ok(items)
    }

    /// Every answer is redacted here, so no path to the guest can carry the credential back.
    async fn rpc(
        &self,
        upstream: &Upstream,
        method: &str,
        params: Value,
    ) -> Result<Value, Failure> {
        let bearer = self.bearer(upstream)?;
        let outcome = self
            .exchange(upstream, bearer.as_deref(), method, params)
            .await;
        match (outcome, bearer) {
            (Ok(mut value), Some(secret)) => {
                redact(&mut value, &secret);
                Ok(value)
            }
            (Err(mut failure), Some(secret)) => {
                failure.reason = failure.reason.replace(&secret, REDACTED);
                Err(failure)
            }
            (outcome, None) => outcome,
        }
    }

    fn bearer(&self, upstream: &Upstream) -> Result<Option<String>, Failure> {
        let Some(name) = &upstream.credential else {
            return Ok(None);
        };
        let credentials = self.credentials.lock().expect("credentials");
        let credential = credentials
            .get(name)
            .ok_or_else(|| Failure::credential("credential unavailable"))?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(u64::MAX, |elapsed| elapsed.as_secs());
        if credential.expires_at <= now {
            return Err(Failure::credential("credential expired"));
        }
        Ok(Some(credential.value.clone()))
    }

    async fn exchange(
        &self,
        upstream: &Upstream,
        bearer: Option<&str>,
        method: &str,
        params: Value,
    ) -> Result<Value, Failure> {
        let handshake = self.handshake(upstream, bearer).await?;
        match self
            .request(upstream, bearer, Some(&handshake), method, params.clone())
            .await
        {
            Err(Refusal::Expired) => {
                *upstream.handshake.lock().expect("handshake") = None;
                let handshake = self.handshake(upstream, bearer).await?;
                self.request(upstream, bearer, Some(&handshake), method, params)
                    .await
                    .map_err(Failure::from)
            }
            outcome => outcome.map_err(Failure::from),
        }
    }

    async fn handshake(
        &self,
        upstream: &Upstream,
        bearer: Option<&str>,
    ) -> Result<Handshake, Failure> {
        let cached = upstream.handshake.lock().expect("handshake").clone();
        if let Some(handshake) = cached {
            return Ok(handshake);
        }
        let params = json!({
            "protocolVersion": PROTOCOL_VERSIONS[0],
            "capabilities": {},
            "clientInfo": { "name": "naos", "version": env!("CARGO_PKG_VERSION") },
        });
        let (result, session) = self
            .post(upstream, bearer, None, "initialize", Some(params))
            .await
            .map_err(Failure::from)?;
        let version = result
            .and_then(|result| result.get("protocolVersion").cloned())
            .and_then(|version| version.as_str().map(str::to_owned))
            .filter(|version| PROTOCOL_VERSIONS.contains(&version.as_str()))
            .ok_or_else(|| Failure::provider("server negotiated no supported protocol".into()))?;
        let handshake = Handshake { session, version };
        self.post(
            upstream,
            bearer,
            Some(&handshake),
            "notifications/initialized",
            None,
        )
        .await
        .map_err(Failure::from)?;
        *upstream.handshake.lock().expect("handshake") = Some(handshake.clone());
        Ok(handshake)
    }

    async fn request(
        &self,
        upstream: &Upstream,
        bearer: Option<&str>,
        handshake: Option<&Handshake>,
        method: &str,
        params: Value,
    ) -> Result<Value, Refusal> {
        let (result, _) = self
            .post(upstream, bearer, handshake, method, Some(params))
            .await?;
        result.ok_or_else(|| Refusal::Failed(Failure::provider("server sent no result".into())))
    }

    /// One Streamable HTTP POST; a message without params is a notification and expects no body.
    async fn post(
        &self,
        upstream: &Upstream,
        bearer: Option<&str>,
        handshake: Option<&Handshake>,
        method: &str,
        params: Option<Value>,
    ) -> Result<(Option<Value>, Option<String>), Refusal> {
        let id = params
            .is_some()
            .then(|| upstream.next_id.fetch_add(1, Ordering::Relaxed));
        let mut message = json!({ "jsonrpc": "2.0", "method": method });
        if let (Some(id), Some(params)) = (id, params) {
            message["id"] = json!(id);
            message["params"] = params;
        }
        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers.insert(
            ACCEPT,
            HeaderValue::from_static("application/json, text/event-stream"),
        );
        if let Some(secret) = bearer {
            let mut value = HeaderValue::from_str(&format!("Bearer {secret}"))
                .map_err(|_| Refusal::Failed(Failure::credential("credential is malformed")))?;
            value.set_sensitive(true);
            headers.insert(AUTHORIZATION, value);
        }
        if let Some(handshake) = handshake {
            if let Some(session) = handshake
                .session
                .as_deref()
                .and_then(|session| HeaderValue::from_str(session).ok())
            {
                headers.insert(SESSION_HEADER, session);
            }
            if let Ok(version) = HeaderValue::from_str(&handshake.version) {
                headers.insert(VERSION_HEADER, version);
            }
        }
        let response = upstream
            .network
            .send(GateRequest {
                method: Method::POST,
                url: upstream.url.clone(),
                headers,
                body: Some(message.to_string().into_bytes()),
            })
            .await
            .map_err(|err| Refusal::Failed(Failure::provider(gate_reason(err))))?;

        let session = response
            .headers
            .get(SESSION_HEADER)
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        if response.status == StatusCode::NOT_FOUND
            && handshake.is_some_and(|handshake| handshake.session.is_some())
        {
            return Err(Refusal::Expired);
        }
        if matches!(
            response.status,
            StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN
        ) {
            return Err(Refusal::Failed(Failure::credential(
                "server refused the credential",
            )));
        }
        if !response.status.is_success() {
            return Err(Refusal::Failed(Failure::provider(format!(
                "server answered {}",
                response.status.as_u16()
            ))));
        }
        let Some(id) = id else {
            return Ok((None, session));
        };
        let event_stream = response
            .headers
            .get(CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| value.starts_with("text/event-stream"));
        let reply = if event_stream {
            from_events(&response.body, id)
        } else {
            from_json(&response.body, id)
        }
        .ok_or_else(|| Refusal::Failed(Failure::provider("server sent no response".into())))?;
        if let Some(error) = reply.get("error") {
            let message = error
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("unknown error");
            let message: String = message.chars().take(MAX_REASON).collect();
            return Err(Refusal::Failed(Failure::provider(format!(
                "server error: {message}"
            ))));
        }
        Ok((reply.get("result").cloned(), session))
    }
}

enum Refusal {
    Expired,
    Failed(Failure),
}

impl From<Refusal> for Failure {
    fn from(refusal: Refusal) -> Self {
        match refusal {
            Refusal::Expired => Failure::provider("server session expired".into()),
            Refusal::Failed(failure) => failure,
        }
    }
}

#[cfg(test)]
impl McpGate {
    /// Test-only: plain http to a local server, answered through `NetworkGate::local`.
    pub(crate) fn local(document: &Value, ips: Vec<std::net::IpAddr>) -> Result<Self, AgentError> {
        Self::build("run_a", Some(document), "http", &|_, policy| {
            NetworkGate::local(policy, ips.clone())
        })
    }

    pub(crate) fn credential_value(&self, name: &str) -> Option<String> {
        let credentials = self.credentials.lock().expect("credentials");
        credentials
            .get(name)
            .map(|credential| credential.value.clone())
    }
}

fn from_json(body: &[u8], id: u64) -> Option<Value> {
    let value: Value = serde_json::from_slice(body).ok()?;
    let candidates = match value {
        Value::Array(items) => items,
        single => vec![single],
    };
    candidates.into_iter().find(|reply| answers(reply, id))
}

fn from_events(body: &[u8], id: u64) -> Option<Value> {
    let text = std::str::from_utf8(body).ok()?.replace("\r\n", "\n");
    text.split("\n\n").find_map(|event| {
        let data: Vec<&str> = event
            .lines()
            .filter_map(|line| line.strip_prefix("data:"))
            .map(|line| line.strip_prefix(' ').unwrap_or(line))
            .collect();
        serde_json::from_str::<Value>(&data.join("\n"))
            .ok()
            .filter(|reply| answers(reply, id))
    })
}

fn answers(reply: &Value, id: u64) -> bool {
    reply.get("id") == Some(&json!(id))
        && (reply.get("result").is_some() || reply.get("error").is_some())
}

fn redact(value: &mut Value, secret: &str) {
    match value {
        Value::String(text) if text.contains(secret) => *text = text.replace(secret, REDACTED),
        Value::Array(items) => items.iter_mut().for_each(|item| redact(item, secret)),
        Value::Object(fields) => fields.values_mut().for_each(|item| redact(item, secret)),
        _ => {}
    }
}

fn gate_reason(err: AgentError) -> String {
    match err {
        AgentError::Runtime(reason) => reason,
        _ => "gate failure".into(),
    }
}

fn invalid(reason: &str) -> AgentError {
    AgentError::Runtime(format!("invalid mcp policy: {reason}"))
}
