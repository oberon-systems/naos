//! The model gateway a guest reaches over vsock: plain HTTP in, the provider's HTTPS out.
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use reqwest::header::{HeaderMap, HeaderName, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use reqwest::{Method, Response, Url};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::libs::api::RunCredential;
use crate::libs::audit;
use crate::libs::error::AgentError;
use crate::libs::network::{GateRequest, NetworkGate};

mod http;
pub use http::serve;

const WINDOW: Duration = Duration::from_secs(60);
const REDACTED: &[u8] = b"<redacted>";
const MAX_ANSWER: usize = 64 * 1024 * 1024;
const MAX_USAGE_LINE: usize = 1024 * 1024;
const MAX_QUERY: usize = 256;
const MAX_MODEL: usize = 128;
const GATEWAY: &str = "naos";
const UNKNOWN: &str = "unknown";
const FORWARDED: &[&str] = &[
    "content-type",
    "accept",
    "anthropic-version",
    "anthropic-beta",
];
const RETURNED: &[&str] = &["content-type", "request-id", "x-request-id"];
const CHAT: &str = "/v1/chat/completions";
const MODELS: &str = "/v1/models";
const MESSAGES: &str = "/v1/messages";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Dialect {
    Openai,
    Anthropic,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyDoc {
    providers: Vec<Value>,
    max_input_tokens: u64,
    max_output_tokens: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProviderDoc {
    name: String,
    api: Dialect,
    url: String,
    credential: String,
    models: Vec<String>,
    timeout_seconds: u64,
    max_requests_per_minute: u32,
}

#[derive(Debug)]
struct Provider {
    source: Value,
    name: String,
    api: Dialect,
    url: String,
    credential: String,
    models: BTreeSet<String>,
    timeout: Duration,
    max_requests: u32,
    window: Mutex<(Instant, u32)>,
    network: NetworkGate,
}

impl Provider {
    fn spend(&self) -> bool {
        let mut window = self.window.lock().expect("window");
        let now = Instant::now();
        if now.duration_since(window.0) >= WINDOW {
            *window = (now, 0);
        }
        if window.1 >= self.max_requests {
            return false;
        }
        window.1 += 1;
        true
    }
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Usage {
    pub input: u64,
    pub output: u64,
}

/// One request as the guest sent it, after framing.
#[derive(Debug)]
pub struct Request {
    pub method: String,
    pub path: String,
    pub query: Option<String>,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

/// What the gate answers: a body it built itself, or the provider's answer to relay.
pub enum Reply {
    Local { status: u16, body: Vec<u8> },
    Relay(Box<Relay>),
}

/// A provider answer in flight; every chunk handed out is already redacted.
pub struct Relay {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    response: Response,
    redactor: Redactor,
    meter: Meter,
    call: Call,
    event_stream: bool,
    read: usize,
    done: bool,
}

struct Call {
    provider: String,
    model: String,
    dialect: Dialect,
    started: Instant,
}

impl Relay {
    /// The next redacted bytes, `None` once the answer is complete.
    pub async fn chunk(&mut self) -> Result<Option<Vec<u8>>, String> {
        match self.response.chunk().await {
            Ok(Some(chunk)) => {
                self.read += chunk.len();
                if self.read > MAX_ANSWER {
                    return Err("provider answer too large".into());
                }
                self.meter.feed(&chunk);
                Ok(Some(self.redactor.feed(&chunk)))
            }
            Ok(None) if self.done => Ok(None),
            Ok(None) => {
                self.done = true;
                Ok(Some(self.redactor.finish()).filter(|rest| !rest.is_empty()))
            }
            Err(err) if err.is_timeout() => Err("provider timed out".into()),
            Err(_) => Err("provider answer failed".into()),
        }
    }

    /// An error in the dialect's stream shape, so a client reading events sees why the stream ended.
    pub fn stream_error(&self, message: &str) -> Option<Vec<u8>> {
        if !self.event_stream {
            return None;
        }
        let body = error_body(self.call.dialect, 502, message);
        Some(match self.call.dialect {
            Dialect::Anthropic => format!("event: error\ndata: {body}\n\n").into_bytes(),
            Dialect::Openai => format!("data: {body}\n\n").into_bytes(),
        })
    }
}

type Connect<'a> = &'a dyn Fn(&str, &Value) -> Result<NetworkGate, AgentError>;

/// One model policy as the gate holds it: the providers it serves and the Run's token budget.
#[derive(Debug, Default)]
struct Policy {
    document: Option<Value>,
    providers: Vec<Arc<Provider>>,
    budget: Usage,
}

impl Policy {
    /// A provider the document names unchanged is taken over from `kept` with its rate window.
    fn parse(
        run_id: &str,
        document: Option<&Value>,
        scheme: &str,
        gate: Connect<'_>,
        kept: &[Arc<Provider>],
    ) -> Result<Self, AgentError> {
        let Some(value) = document else {
            return Ok(Self::default());
        };
        let doc: PolicyDoc =
            serde_json::from_value(value.clone()).map_err(|err| invalid(&err.to_string()))?;
        let mut providers: Vec<Arc<Provider>> = Vec::new();
        for source in doc.providers {
            let provider: ProviderDoc =
                serde_json::from_value(source.clone()).map_err(|err| invalid(&err.to_string()))?;
            let url = Url::parse(&provider.url)
                .map_err(|err| invalid(&format!("{}: {err}", provider.name)))?;
            let host = url
                .host_str()
                .filter(|_| {
                    url.scheme() == scheme && url.username().is_empty() && url.password().is_none()
                })
                .ok_or_else(|| {
                    invalid(&format!("{} must be reached over {scheme}", provider.name))
                })?;
            if provider.models.is_empty()
                || provider.timeout_seconds == 0
                || provider.max_requests_per_minute == 0
                || providers
                    .iter()
                    .any(|other| provider.models.iter().any(|m| other.models.contains(m)))
            {
                return Err(invalid(&format!(
                    "provider {:?} is malformed",
                    provider.name
                )));
            }
            if let Some(same) = kept.iter().find(|old| old.source == source) {
                providers.push(Arc::clone(same));
                continue;
            }
            let network = gate(
                run_id,
                &json!({ "allow": [{ "protocol": scheme, "host": host }] }),
            )?;
            providers.push(Arc::new(Provider {
                source,
                name: provider.name,
                api: provider.api,
                url: provider.url.trim_end_matches('/').to_owned(),
                credential: provider.credential,
                models: provider.models.into_iter().collect(),
                timeout: Duration::from_secs(provider.timeout_seconds),
                max_requests: provider.max_requests_per_minute,
                window: Mutex::new((Instant::now(), 0)),
                network,
            }));
        }
        Ok(Self {
            document: Some(value.clone()),
            providers,
            budget: Usage {
                input: doc.max_input_tokens,
                output: doc.max_output_tokens,
            },
        })
    }

    fn credentials(&self) -> BTreeSet<&str> {
        self.providers
            .iter()
            .map(|provider| provider.credential.as_str())
            .collect()
    }
}

/// The providers of one Run, its token budget and the credentials the API last issued for it;
/// the policy can be replaced under a running VM, what the Run spent cannot.
#[derive(Debug)]
pub struct ModelGate {
    run_id: String,
    policy: Mutex<Arc<Policy>>,
    spent: Mutex<Usage>,
    credentials: Mutex<BTreeMap<String, RunCredential>>,
}

impl ModelGate {
    pub fn from_snapshot(run_id: &str, document: Option<&Value>) -> Result<Self, AgentError> {
        Self::build(run_id, document, "https", &gate)
    }

    fn build(
        run_id: &str,
        document: Option<&Value>,
        scheme: &str,
        gate: Connect<'_>,
    ) -> Result<Self, AgentError> {
        Ok(Self {
            run_id: run_id.to_owned(),
            policy: Mutex::new(Arc::new(Policy::parse(
                run_id,
                document,
                scheme,
                gate,
                &[],
            )?)),
            spent: Mutex::new(Usage::default()),
            credentials: Mutex::new(BTreeMap::new()),
        })
    }

    fn policy(&self) -> Arc<Policy> {
        Arc::clone(&self.policy.lock().expect("policy"))
    }

    /// Swaps in a changed document between requests; one that cannot be read grants nothing.
    pub fn replace(&self, document: Option<&Value>) {
        self.swap(document, "https", &gate);
    }

    fn swap(&self, document: Option<&Value>, scheme: &str, gate: Connect<'_>) {
        let current = self.policy();
        if current.document.as_ref() == document {
            return;
        }
        let next = match Policy::parse(&self.run_id, document, scheme, gate, &current.providers) {
            Ok(next) => {
                audit::model_policy_configured(&self.run_id);
                next
            }
            Err(err) => {
                tracing::warn!(run_id = %self.run_id, error = %err, "the new model policy grants nothing");
                Policy {
                    document: document.cloned(),
                    ..Policy::default()
                }
            }
        };
        let names = next.credentials();
        self.credentials
            .lock()
            .expect("credentials")
            .retain(|name, _| names.contains(name.as_str()));
        *self.policy.lock().expect("policy") = Arc::new(next);
    }

    /// Keeps only the credentials of this gate's providers; the MCP gate holds its own.
    pub fn refresh(&self, credentials: &BTreeMap<String, RunCredential>) {
        let policy = self.policy();
        let names = policy.credentials();
        *self.credentials.lock().expect("credentials") = credentials
            .iter()
            .filter(|(name, _)| names.contains(name.as_str()))
            .map(|(name, credential)| (name.clone(), credential.clone()))
            .collect();
    }

    #[cfg(test)]
    pub fn spent(&self) -> Usage {
        *self.spent.lock().expect("spent")
    }

    /// Routes one request; a refusal is answered here and audited, a forwarded call is audited by `finish`.
    pub async fn handle(&self, request: Request) -> Reply {
        let started = Instant::now();
        let dialect = if request.path == MESSAGES {
            Dialect::Anthropic
        } else {
            Dialect::Openai
        };
        let listing = request.method == "GET" && request.path == MODELS;
        let forwarded =
            request.method == "POST" && matches!(request.path.as_str(), CHAT | MESSAGES);
        match (listing, forwarded) {
            (true, _) => {
                self.audit(GATEWAY, "none", Usage::default(), started, "allow", "none");
                Reply::Local {
                    status: 200,
                    body: self.listing(),
                }
            }
            (_, true) => match self.forward(request, dialect).await {
                Ok(relay) => Reply::Relay(Box::new(relay)),
                Err(refusal) => {
                    self.audit(
                        &refusal.provider,
                        &refusal.model,
                        Usage::default(),
                        started,
                        "deny",
                        refusal.category,
                    );
                    Reply::Local {
                        status: refusal.status,
                        body: error_body(dialect, refusal.status, &refusal.message).into_bytes(),
                    }
                }
            },
            _ => {
                self.audit(
                    UNKNOWN,
                    "none",
                    Usage::default(),
                    started,
                    "deny",
                    "invalid",
                );
                Reply::Local {
                    status: 404,
                    body: error_body(dialect, 404, "not found").into_bytes(),
                }
            }
        }
    }

    /// A malformed request never reaches `handle`; the framing layer answers it in the dialect of its path.
    pub fn refuse(&self, path: &str, status: u16, message: &str) -> Vec<u8> {
        let dialect = if path == MESSAGES {
            Dialect::Anthropic
        } else {
            Dialect::Openai
        };
        audit::model_rejected(&self.run_id, message);
        error_body(dialect, status, message).into_bytes()
    }

    /// The guest closed the connection before the provider answered, so the call was dropped.
    pub fn abandoned(&self) {
        audit::model_call(&self.run_id, UNKNOWN, "none", 0, 0, "allow", 0, "aborted");
    }

    /// Books what the provider reported against the Run and writes the call's audit line.
    pub fn finish(&self, relay: &Relay, outcome: Result<(), &str>) {
        let usage = relay.meter.usage();
        {
            let mut spent = self.spent.lock().expect("spent");
            spent.input = spent.input.saturating_add(usage.input);
            spent.output = spent.output.saturating_add(usage.output);
        }
        let (decision, category) = match (outcome, relay.status) {
            (Err("aborted"), _) => ("allow", "aborted"),
            (Err(reason), _) if reason.contains("timed out") => ("deny", "timeout"),
            (Err(_), _) => ("deny", "provider"),
            (Ok(()), 200..=299) => ("allow", "none"),
            (Ok(()), 401 | 403) => ("deny", "credential"),
            (Ok(()), 429) => ("deny", "rate"),
            (Ok(()), _) => ("deny", "provider"),
        };
        self.audit(
            &relay.call.provider,
            &relay.call.model,
            usage,
            relay.call.started,
            decision,
            category,
        );
    }

    fn listing(&self) -> Vec<u8> {
        let data: Vec<Value> = self
            .policy()
            .providers
            .iter()
            .filter(|provider| provider.api == Dialect::Openai)
            .flat_map(|provider| {
                provider.models.iter().map(|model| {
                    json!({ "id": model, "object": "model", "created": 0, "owned_by": provider.name })
                })
            })
            .collect();
        json!({ "object": "list", "data": data })
            .to_string()
            .into_bytes()
    }

    async fn forward(&self, request: Request, dialect: Dialect) -> Result<Relay, Refusal> {
        let mut body: Value = serde_json::from_slice(&request.body)
            .ok()
            .filter(Value::is_object)
            .ok_or_else(|| Refusal::invalid(400, "body must be a JSON object"))?;
        let model = body
            .get("model")
            .and_then(Value::as_str)
            .ok_or_else(|| Refusal::invalid(400, "body has no model"))?
            .to_owned();
        let named = model_name(&model);
        let policy = self.policy();
        let provider = policy
            .providers
            .iter()
            .find(|provider| provider.api == dialect && provider.models.contains(&model))
            .ok_or_else(|| Refusal {
                status: 404,
                message: format!("model {named} is not served here"),
                category: "denied",
                provider: UNKNOWN.into(),
                model: named.clone(),
            })?;
        let refuse = |status: u16, message: &str, category: &'static str| Refusal {
            status,
            message: message.to_owned(),
            category,
            provider: provider.name.clone(),
            model: model.clone(),
        };
        if request
            .query
            .as_deref()
            .is_some_and(|query| query.len() > MAX_QUERY || !query.bytes().all(query_byte))
        {
            return Err(refuse(400, "query is not allowed", "invalid"));
        }
        {
            let spent = self.spent.lock().expect("spent");
            if spent.input >= policy.budget.input || spent.output >= policy.budget.output {
                return Err(refuse(429, "the Run's token budget is spent", "budget"));
            }
        }
        if !provider.spend() {
            return Err(refuse(429, "rate limit", "rate"));
        }
        let secret = self
            .secret(&provider.credential)
            .map_err(|reason| refuse(502, reason, "credential"))?;

        let stream = body.get("stream") == Some(&Value::Bool(true));
        // Without it an OpenAI stream carries no usage, and the budget could not be booked.
        let forwarded = if dialect == Dialect::Openai && stream {
            if !body.get("stream_options").is_some_and(Value::is_object) {
                body["stream_options"] = json!({});
            }
            body["stream_options"]["include_usage"] = json!(true);
            body.to_string().into_bytes()
        } else {
            request.body
        };
        let mut headers = HeaderMap::new();
        for (name, value) in &request.headers {
            let name = name.to_ascii_lowercase();
            if !FORWARDED.contains(&name.as_str()) {
                continue;
            }
            if let (Ok(name), Ok(value)) = (
                HeaderName::from_bytes(name.as_bytes()),
                HeaderValue::from_str(value),
            ) {
                headers.insert(name, value);
            }
        }
        headers
            .entry(CONTENT_TYPE)
            .or_insert(HeaderValue::from_static("application/json"));
        let (key, value) = match dialect {
            Dialect::Openai => (AUTHORIZATION, format!("Bearer {secret}")),
            Dialect::Anthropic => (HeaderName::from_static("x-api-key"), secret.clone()),
        };
        let mut value = HeaderValue::from_str(&value)
            .map_err(|_| refuse(502, "credential is malformed", "credential"))?;
        value.set_sensitive(true);
        headers.insert(key, value);

        let mut url = format!("{}{}", provider.url, request.path);
        if let Some(query) = &request.query {
            url = format!("{url}?{query}");
        }
        let started = Instant::now();
        let response = provider
            .network
            .open(
                GateRequest {
                    method: Method::POST,
                    url,
                    headers,
                    body: Some(forwarded),
                },
                provider.timeout,
            )
            .await
            .map_err(|err| match err.to_string() {
                reason if reason.contains("timed out") => {
                    refuse(504, "provider timed out", "timeout")
                }
                reason if reason.contains("network deny") && !reason.contains("request failed") => {
                    refuse(502, "provider refused by the network gate", "denied")
                }
                _ => refuse(502, "provider unreachable", "provider"),
            })?;
        let status = response.status();
        let returned: Vec<(String, String)> = response
            .headers()
            .iter()
            .filter(|(name, _)| RETURNED.contains(&name.as_str()))
            .filter_map(|(name, value)| {
                value
                    .to_str()
                    .ok()
                    .map(|value| (name.as_str().to_owned(), value.to_owned()))
            })
            .collect();
        let event_stream = response
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| value.starts_with("text/event-stream"));
        Ok(Relay {
            status: status.as_u16(),
            headers: returned,
            response,
            redactor: Redactor::new(secret.into_bytes()),
            meter: Meter::new(event_stream),
            call: Call {
                provider: provider.name.clone(),
                model,
                dialect,
                started,
            },
            event_stream,
            read: 0,
            done: false,
        })
    }

    fn secret(&self, name: &str) -> Result<String, &'static str> {
        let credentials = self.credentials.lock().expect("credentials");
        let credential = credentials.get(name).ok_or("credential unavailable")?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(u64::MAX, |elapsed| elapsed.as_secs());
        if credential.expires_at <= now {
            return Err("credential expired");
        }
        Ok(credential.value.clone())
    }

    fn audit(
        &self,
        provider: &str,
        model: &str,
        usage: Usage,
        started: Instant,
        decision: &str,
        category: &str,
    ) {
        let duration_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
        audit::model_call(
            &self.run_id,
            provider,
            model,
            usage.input,
            usage.output,
            decision,
            duration_ms,
            category,
        );
    }
}

#[cfg(feature = "smoke-stubs")]
fn gate(run_id: &str, policy: &Value) -> Result<NetworkGate, AgentError> {
    let roots = match std::env::var_os("NAOS_AGENT_SMOKE_CA_FILE") {
        Some(path) => {
            let pem = std::fs::read(path)?;
            reqwest::Certificate::from_pem_bundle(&pem)
                .map_err(|err| invalid(&format!("smoke ca: {err}")))?
        }
        None => Vec::new(),
    };
    NetworkGate::stub(
        run_id,
        policy,
        vec![std::net::IpAddr::from([127, 0, 0, 1])],
        roots,
    )
}

#[cfg(not(feature = "smoke-stubs"))]
fn gate(run_id: &str, policy: &Value) -> Result<NetworkGate, AgentError> {
    NetworkGate::from_snapshot(run_id, Some(policy))
}

#[cfg(test)]
impl ModelGate {
    /// Test-only: plain http to a local server, answered through `NetworkGate::local`.
    pub(crate) fn local(document: &Value, ips: Vec<std::net::IpAddr>) -> Result<Self, AgentError> {
        Self::build("run_a", Some(document), "http", &|_, policy| {
            NetworkGate::local(policy, ips.clone())
        })
    }

    pub(crate) fn replace_local(&self, document: &Value, ips: Vec<std::net::IpAddr>) {
        self.swap(Some(document), "http", &|_, policy| {
            NetworkGate::local(policy, ips.clone())
        });
    }

    pub(crate) fn served(&self) -> Vec<String> {
        self.policy()
            .providers
            .iter()
            .flat_map(|provider| provider.models.iter().cloned())
            .collect()
    }

    pub(crate) fn holds(&self, credential: &str) -> bool {
        self.credentials
            .lock()
            .expect("credentials")
            .contains_key(credential)
    }
}

struct Refusal {
    status: u16,
    message: String,
    category: &'static str,
    provider: String,
    model: String,
}

impl Refusal {
    fn invalid(status: u16, message: &str) -> Self {
        Self {
            status,
            message: message.to_owned(),
            category: "invalid",
            provider: UNKNOWN.into(),
            model: "none".into(),
        }
    }
}

/// The error body a client of that dialect expects for `status`.
pub fn error_body(dialect: Dialect, status: u16, message: &str) -> String {
    let kind = match (dialect, status) {
        (Dialect::Anthropic, 404) => "not_found_error",
        (Dialect::Anthropic, 413) => "request_too_large",
        (Dialect::Anthropic, 429) => "rate_limit_error",
        (Dialect::Anthropic, 400..=499) => "invalid_request_error",
        (Dialect::Anthropic, _) => "api_error",
        (Dialect::Openai, 429) => "rate_limit_exceeded",
        (Dialect::Openai, 400..=499) => "invalid_request_error",
        (Dialect::Openai, _) => "server_error",
    };
    match dialect {
        Dialect::Anthropic => {
            json!({ "type": "error", "error": { "type": kind, "message": message } }).to_string()
        }
        Dialect::Openai => {
            let code = if status == 404 {
                json!("model_not_found")
            } else {
                Value::Null
            };
            json!({ "error": { "message": message, "type": kind, "code": code } }).to_string()
        }
    }
}

fn model_name(raw: &str) -> String {
    let valid = !raw.is_empty()
        && raw.len() <= MAX_MODEL
        && raw.bytes().enumerate().all(|(at, b)| {
            b.is_ascii_alphanumeric()
                || (at > 0 && matches!(b, b'.' | b'_' | b':' | b'/' | b'@' | b'-'))
        });
    if valid {
        raw.to_owned()
    } else {
        "invalid".into()
    }
}

fn query_byte(b: u8) -> bool {
    b.is_ascii_alphanumeric() || matches!(b, b'_' | b'=' | b'&' | b'.' | b'-')
}

fn invalid(reason: &str) -> AgentError {
    AgentError::Runtime(format!("invalid model policy: {reason}"))
}

/// Replaces the key in a stream even when a chunk boundary splits it: the last `len - 1` bytes wait.
struct Redactor {
    secret: Vec<u8>,
    held: Vec<u8>,
}

impl Redactor {
    fn new(secret: Vec<u8>) -> Self {
        Self {
            secret,
            held: Vec::new(),
        }
    }

    fn feed(&mut self, chunk: &[u8]) -> Vec<u8> {
        self.held.extend_from_slice(chunk);
        let mut text = replace(&self.held, &self.secret);
        let keep = self.secret.len().saturating_sub(1).min(text.len());
        self.held = text.split_off(text.len() - keep);
        text
    }

    fn finish(&mut self) -> Vec<u8> {
        replace(&std::mem::take(&mut self.held), &self.secret)
    }
}

fn replace(text: &[u8], secret: &[u8]) -> Vec<u8> {
    if secret.is_empty() || text.len() < secret.len() {
        return text.to_vec();
    }
    let mut out = Vec::with_capacity(text.len());
    let mut at = 0;
    while at < text.len() {
        if text[at..].starts_with(secret) {
            out.extend_from_slice(REDACTED);
            at += secret.len();
        } else {
            out.push(text[at]);
            at += 1;
        }
    }
    out
}

/// Reads the token usage a provider reports, from its JSON answer or from its event stream.
struct Meter {
    events: bool,
    line: Vec<u8>,
    json: Vec<u8>,
    usage: Usage,
}

impl Meter {
    fn new(events: bool) -> Self {
        Self {
            events,
            line: Vec::new(),
            json: Vec::new(),
            usage: Usage::default(),
        }
    }

    fn feed(&mut self, chunk: &[u8]) {
        if !self.events {
            if self.json.len() + chunk.len() <= MAX_ANSWER {
                self.json.extend_from_slice(chunk);
            }
            return;
        }
        for byte in chunk {
            if *byte == b'\n' {
                let line = std::mem::take(&mut self.line);
                self.line_done(&line);
            } else if self.line.len() < MAX_USAGE_LINE {
                self.line.push(*byte);
            }
        }
    }

    fn line_done(&mut self, line: &[u8]) {
        let Some(data) = line.strip_prefix(b"data:") else {
            return;
        };
        if let Ok(event) = serde_json::from_slice::<Value>(data.trim_ascii()) {
            self.observe(&event);
        }
    }

    fn usage(&self) -> Usage {
        let mut usage = self.usage;
        if !self.events {
            if let Ok(answer) = serde_json::from_slice::<Value>(&self.json) {
                usage = usage_of(&answer).unwrap_or_default();
            }
        }
        usage
    }

    fn observe(&mut self, event: &Value) {
        if let Some(found) = usage_of(event) {
            self.usage.input = self.usage.input.max(found.input);
            self.usage.output = self.usage.output.max(found.output);
        }
    }
}

// Anthropic reports usage on the answer or on message_start's message; cache tokens are input too.
fn usage_of(value: &Value) -> Option<Usage> {
    let usage = value
        .get("usage")
        .or_else(|| value.pointer("/message/usage"))
        .filter(|usage| usage.is_object())?;
    let count = |key: &str| usage.get(key).and_then(Value::as_u64).unwrap_or(0);
    Some(Usage {
        input: count("prompt_tokens")
            + count("input_tokens")
            + count("cache_creation_input_tokens")
            + count("cache_read_input_tokens"),
        output: count("completion_tokens") + count("output_tokens"),
    })
}

#[cfg(test)]
mod tests;
