//! The MCP endpoint a guest reaches through its virtio-serial port: the gates of its Run as tools.
use std::collections::{BTreeMap, HashSet};
use std::future::Future;
use std::time::{Duration, Instant};

use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::Method;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};

use crate::libs::audit;
use crate::libs::error::AgentError;
use crate::libs::network::{GateRequest, GateResponse};
use crate::libs::runtime::RunGates;
use crate::libs::shell::{ShellRequest, ShellResponse};

mod rules;
mod upstream;
use rules::{Rules, Verdict};
pub use upstream::McpGate;
use upstream::{Failure, Policy, Upstream};

const MAX_LINE: usize = 1024 * 1024;
const MAX_IDS: usize = 65536;
const CALL_TIMEOUT: Duration = Duration::from_secs(45);
const PROTOCOL_VERSIONS: &[&str] = &["2025-06-18", "2025-03-26", "2024-11-05"];
const METHODS: &[&str] = &["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"];
// The address is pinned to the authorized host, so a Host header would reach another vhost behind it.
const FORBIDDEN_HEADERS: &[&str] = &["host", "connection", "transfer-encoding", "content-length"];

const INVALID_REQUEST: i64 = -32600;
const METHOD_NOT_FOUND: i64 = -32601;
const INVALID_PARAMS: i64 = -32602;
const INTERNAL_ERROR: i64 = -32603;
const PARSE_ERROR: i64 = -32700;
const RESOURCE_NOT_FOUND: i64 = -32002;
const SERVER: &str = "naos";
const NO_RESOURCE: &str = "none";
const UNKNOWN: &str = "unknown";
const SECRETS: &str = "secrets";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PathArgs {
    path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GrepArgs {
    path: String,
    pattern: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HttpArgs {
    method: String,
    url: String,
    #[serde(default)]
    headers: BTreeMap<String, String>,
    body: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NoArgs {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SecretArgs {
    name: String,
}

enum Call {
    Shell(ShellRequest),
    Http(GateRequest),
    Secrets,
    Secret(String),
}

/// Serves newline-delimited JSON-RPC until the guest side closes or breaks the framing.
pub async fn serve<R, W>(
    run_id: &str,
    gates: &RunGates,
    read: R,
    write: W,
) -> Result<(), AgentError>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    Session::new(run_id, gates, CALL_TIMEOUT)
        .run(read, write)
        .await
}

struct Session<'a> {
    run_id: &'a str,
    gates: &'a RunGates,
    timeout: Duration,
    seen: HashSet<String>,
    initialized: bool,
    resources: bool,
}

impl<'a> Session<'a> {
    fn new(run_id: &'a str, gates: &'a RunGates, timeout: Duration) -> Self {
        Self {
            run_id,
            gates,
            timeout,
            seen: HashSet::new(),
            initialized: false,
            resources: gates.mcp.policy().has_resources(),
        }
    }

    async fn run<R, W>(&mut self, read: R, mut write: W) -> Result<(), AgentError>
    where
        R: AsyncRead + Unpin,
        W: AsyncWrite + Unpin,
    {
        let mut reader = BufReader::new(read);
        let mut line = Vec::new();
        let mut changes = self.gates.mcp.changes();
        let mut shell = self.gates.shell.changes();
        let mut network = self.gates.network.changes();
        loop {
            line.clear();
            let read = {
                let mut limited = (&mut reader).take(MAX_LINE as u64 + 1);
                let next = limited.read_until(b'\n', &mut line);
                tokio::pin!(next);
                // Requests are answered one at a time, so a change is announced between calls.
                loop {
                    tokio::select! {
                        read = &mut next => break read?,
                        Ok(()) = changes.changed() => self.announce(&mut write).await?,
                        Ok(()) = shell.changed() => self.announce(&mut write).await?,
                        Ok(()) = network.changed() => self.announce(&mut write).await?,
                    }
                }
            };
            if read == 0 {
                return Ok(());
            }
            // The guest is untrusted: an unbounded line would buffer without limit, so it ends the session.
            if line.len() > MAX_LINE && line.last() != Some(&b'\n') {
                audit::mcp_rejected(self.run_id, "line too long");
                return Err(AgentError::Runtime("mcp: line too long".into()));
            }
            let message = line.trim_ascii();
            if message.is_empty() {
                continue;
            }
            if let Some(reply) = self.respond(message).await? {
                let mut out = reply.to_string().into_bytes();
                out.push(b'\n');
                write.write_all(&out).await?;
                write.flush().await?;
            }
        }
    }

    /// Tells an initialized agent that what it listed is stale.
    async fn announce<W: AsyncWrite + Unpin>(&mut self, write: &mut W) -> Result<(), AgentError> {
        let resources = self.gates.mcp.policy().has_resources();
        let listed = std::mem::replace(&mut self.resources, resources) || resources;
        if !self.initialized {
            return Ok(());
        }
        let mut out = notification("notifications/tools/list_changed");
        if listed {
            out.extend(notification("notifications/resources/list_changed"));
        }
        write.write_all(&out).await?;
        write.flush().await?;
        Ok(())
    }

    async fn respond(&mut self, raw: &[u8]) -> Result<Option<Value>, AgentError> {
        let Ok(message) = serde_json::from_slice::<Value>(raw) else {
            audit::mcp_rejected(self.run_id, "unparseable message");
            return Ok(Some(error(Value::Null, PARSE_ERROR, "parse error")));
        };
        if !message.is_object() || message.get("jsonrpc") != Some(&json!("2.0")) {
            audit::mcp_rejected(self.run_id, "not a JSON-RPC 2.0 object");
            return Ok(Some(error(Value::Null, INVALID_REQUEST, "invalid request")));
        }
        // Without an id this is a notification, or a response to nothing the host asked, and gets no reply.
        let Some(id) = message.get("id").cloned() else {
            return Ok(None);
        };
        if self.seen.len() >= MAX_IDS {
            audit::mcp_rejected(self.run_id, "too many requests");
            return Err(AgentError::Runtime("mcp: too many requests".into()));
        }
        if !self.seen.insert(id.to_string()) {
            audit::mcp_rejected(self.run_id, "duplicate request id");
            return Ok(Some(error(id, INVALID_REQUEST, "duplicate request id")));
        }
        let Some(method) = message.get("method").and_then(Value::as_str) else {
            audit::mcp_rejected(self.run_id, "request without a method");
            return Ok(Some(error(id, INVALID_REQUEST, "invalid request")));
        };
        let policy = self.gates.mcp.policy();
        let policy = policy.as_ref();
        let result = match method {
            "initialize" => {
                self.initialized = true;
                let requested = message
                    .pointer("/params/protocolVersion")
                    .and_then(Value::as_str);
                let version = requested
                    .filter(|version| PROTOCOL_VERSIONS.contains(version))
                    .unwrap_or(PROTOCOL_VERSIONS[0]);
                let mut capabilities = json!({ "tools": { "listChanged": true } });
                if policy.has_resources() {
                    capabilities["resources"] = json!({ "listChanged": true });
                }
                json!({
                    "protocolVersion": version,
                    "capabilities": capabilities,
                    "serverInfo": { "name": SERVER, "version": env!("CARGO_PKG_VERSION") },
                })
            }
            "ping" => json!({}),
            "tools/list" => json!({ "tools": self.tools(policy).await }),
            "tools/call" => match self.call(policy, message.get("params")).await {
                Ok(result) => result,
                Err(message) => return Ok(Some(error(id, INVALID_PARAMS, &message))),
            },
            "resources/list" if policy.has_resources() => {
                json!({ "resources": self.resources(policy).await })
            }
            "resources/read" if policy.has_resources() => {
                match self.read(policy, message.get("params")).await {
                    Ok(result) => result,
                    Err((code, message)) => return Ok(Some(error(id, code, &message))),
                }
            }
            _ => return Ok(Some(error(id, METHOD_NOT_FOUND, "method not found"))),
        };
        Ok(Some(
            json!({ "jsonrpc": "2.0", "id": id, "result": result }),
        ))
    }

    /// A malformed call is a JSON-RPC error; a gate refusal is a tool result the agent can read.
    async fn call(&self, policy: &Policy, params: Option<&Value>) -> Result<Value, String> {
        let started = Instant::now();
        let name = params
            .and_then(|params| params.get("name"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        let arguments = params
            .and_then(|params| params.get("arguments"))
            .cloned()
            .unwrap_or_else(|| json!({}));
        let (server, tool) = match name.split_once("__") {
            Some((SECRETS, tool)) if matches!(tool, "list" | "get") => (SECRETS, tool),
            Some((server, tool)) => {
                return self
                    .call_upstream(policy, server, tool, arguments, started)
                    .await;
            }
            None if server_of(name) == UNKNOWN => (UNKNOWN, UNKNOWN),
            None => (server_of(name), name),
        };
        let call = match parse_call(name, arguments.clone()) {
            Ok(call) => call,
            Err(reason) => {
                self.audit(server, tool, NO_RESOURCE, started, "invalid", None);
                return Err(reason);
            }
        };
        let rule = match policy.rules().check(server, tool, &arguments) {
            Verdict::Allow(rule) => Some(rule),
            Verdict::Deny { rule, reason } => {
                self.audit(server, tool, NO_RESOURCE, started, "denied", rule);
                if let Call::Secret(name) = &call {
                    audit::secret_read(self.run_id, logged_name(name), "deny");
                }
                return Ok(tool_result(reason, true));
            }
        };
        let outcome = tokio::time::timeout(self.timeout, self.dispatch(policy, call)).await;
        let (text, category) = match outcome {
            Ok(Ok(text)) => (Ok(text), "none"),
            Ok(Err(reason)) => (Err(reason), "denied"),
            Err(_) => (Err("call timed out".to_owned()), "timeout"),
        };
        self.audit(server, tool, NO_RESOURCE, started, category, rule);
        Ok(match text {
            Ok(text) => tool_result(text, false),
            Err(reason) => tool_result(reason, true),
        })
    }

    async fn call_upstream(
        &self,
        policy: &Policy,
        server: &str,
        tool: &str,
        arguments: Value,
        started: Instant,
    ) -> Result<Value, String> {
        let Some(upstream) = policy.server(server) else {
            self.audit(UNKNOWN, UNKNOWN, NO_RESOURCE, started, "invalid", None);
            return Err("unknown tool".into());
        };
        let rules = policy.rules();
        let logged = if rules.could_allow(&upstream.name, tool) {
            tool
        } else {
            UNKNOWN
        };
        if !arguments.is_object() {
            self.audit(
                &upstream.name,
                logged,
                NO_RESOURCE,
                started,
                "invalid",
                None,
            );
            return Err("invalid arguments: expected an object".into());
        }
        let rule = match rules.check(&upstream.name, tool, &arguments) {
            Verdict::Allow(rule) => rule,
            Verdict::Deny { rule, reason } => {
                self.audit(&upstream.name, logged, NO_RESOURCE, started, "denied", rule);
                return Ok(tool_result(reason, true));
            }
        };
        let work = self.gates.mcp.call_tool(upstream, tool, arguments);
        Ok(self
            .guarded(upstream, logged, NO_RESOURCE, Some(rule), work)
            .await
            .unwrap_or_else(|reason| tool_result(reason, true)))
    }

    async fn tools(&self, policy: &Policy) -> Vec<Value> {
        let rules = policy.rules();
        let mut tools = builtin_tools(self.gates, rules);
        let servers = policy.servers().iter();
        for upstream in servers.filter(|server| rules.lists_tools(&server.name)) {
            let work = self.gates.mcp.list_tools(policy, upstream);
            if let Ok(listed) = self
                .guarded(upstream, "tools/list", NO_RESOURCE, None, work)
                .await
            {
                tools.extend(listed);
            }
        }
        tools
    }

    async fn resources(&self, policy: &Policy) -> Vec<Value> {
        let rules = policy.rules();
        let mut resources = Vec::new();
        let servers = policy.servers().iter();
        for upstream in servers.filter(|server| rules.lists_resources(&server.name)) {
            let work = self.gates.mcp.list_resources(policy, upstream);
            if let Ok(listed) = self
                .guarded(upstream, "resources/list", NO_RESOURCE, None, work)
                .await
            {
                resources.extend(listed);
            }
        }
        resources
    }

    async fn read(&self, policy: &Policy, params: Option<&Value>) -> Result<Value, (i64, String)> {
        let started = Instant::now();
        let Some(uri) = params
            .and_then(|params| params.get("uri"))
            .and_then(Value::as_str)
        else {
            self.audit(
                UNKNOWN,
                "resources/read",
                NO_RESOURCE,
                started,
                "invalid",
                None,
            );
            return Err((INVALID_PARAMS, "invalid arguments: uri is required".into()));
        };
        let (upstream, prefix, rule) = match policy.resource(uri) {
            Ok(found) => found,
            Err(rule) => {
                self.audit(
                    UNKNOWN,
                    "resources/read",
                    NO_RESOURCE,
                    started,
                    "denied",
                    rule,
                );
                let reason = rule.map_or_else(
                    || "resource not allowed".to_owned(),
                    |index| format!("resource denied by rule {index}"),
                );
                return Err((RESOURCE_NOT_FOUND, reason));
            }
        };
        let work = self.gates.mcp.read_resource(upstream, uri);
        self.guarded(upstream, "resources/read", prefix, Some(rule), work)
            .await
            .map_err(|reason| (INTERNAL_ERROR, reason))
    }

    /// Bounds one upstream operation by the tighter of the broker and server timeouts, and audits it.
    async fn guarded<T>(
        &self,
        upstream: &Upstream,
        tool: &str,
        resource: &str,
        rule: Option<usize>,
        work: impl Future<Output = Result<T, Failure>>,
    ) -> Result<T, String> {
        let started = Instant::now();
        let outcome = tokio::time::timeout(self.timeout.min(upstream.timeout), work).await;
        let (result, category) = match outcome {
            Ok(Ok(value)) => (Ok(value), "none"),
            Ok(Err(failure)) => (Err(failure.reason), failure.category),
            Err(_) => (Err("call timed out".to_owned()), "timeout"),
        };
        self.audit(&upstream.name, tool, resource, started, category, rule);
        result
    }

    fn audit(
        &self,
        server: &str,
        tool: &str,
        resource: &str,
        started: Instant,
        category: &str,
        rule: Option<usize>,
    ) {
        let decision = if category == "none" { "allow" } else { "deny" };
        let rule = rule.map_or_else(|| "none".to_owned(), |index| index.to_string());
        audit::mcp_call(
            self.run_id,
            server,
            tool,
            resource,
            decision,
            elapsed_ms(started),
            category,
            &rule,
        );
    }

    async fn dispatch(&self, policy: &Policy, call: Call) -> Result<String, String> {
        match call {
            Call::Shell(request) => {
                render_shell(self.gates.shell.call(request).await.map_err(reason)?)
            }
            Call::Http(request) => {
                render_http(self.gates.network.send(request).await.map_err(reason)?)
            }
            Call::Secrets => Ok(json!(policy.granted()).to_string()),
            Call::Secret(name) => {
                let value = self.gates.mcp.secret(policy, &name);
                let decision = if value.is_some() { "allow" } else { "deny" };
                audit::secret_read(self.run_id, logged_name(&name), decision);
                value.ok_or_else(|| "secret is not available".to_owned())
            }
        }
    }
}

/// A name the guest sent is logged only when it has the shape of a secret name.
fn logged_name(name: &str) -> &str {
    let plain = |byte: u8| byte.is_ascii_lowercase() || byte.is_ascii_digit();
    let shaped = name.len() <= 64
        && name.bytes().next().is_some_and(plain)
        && name
            .bytes()
            .all(|byte| plain(byte) || matches!(byte, b'.' | b'_' | b'-'));
    if shaped {
        name
    } else {
        "invalid"
    }
}

/// The built-in server a rule names for this tool.
fn server_of(name: &str) -> &'static str {
    match name {
        "read_file" | "list_dir" | "grep" | "git_status" | "git_diff" => "shell",
        "http_request" => "network",
        _ => UNKNOWN,
    }
}

/// Only what the gate granted and some rule could allow is listed; a call still meets both.
fn builtin_tools(gates: &RunGates, rules: &Rules) -> Vec<Value> {
    let mut tools: Vec<Value> = gates
        .shell
        .granted()
        .into_iter()
        .filter(|name| rules.could_allow("shell", name))
        .map(shell_tool)
        .collect();
    if rules.could_allow(SECRETS, "list") {
        tools.push(json!({
            "name": "secrets__list",
            "description": "The names of the secrets this Run was granted. Never a value.",
            "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false },
        }));
    }
    if rules.could_allow(SECRETS, "get") {
        tools.push(json!({
            "name": "secrets__get",
            "description": "The value of one granted secret. It is yours from then on: keep it out of files and prompts.",
            "inputSchema": {
                "type": "object",
                "properties": { "name": { "type": "string" } },
                "required": ["name"],
                "additionalProperties": false,
            },
        }));
    }
    if gates.network.allows_any() && rules.could_allow("network", "http_request") {
        tools.push(json!({
            "name": "http_request",
            "description": "Send one HTTP(S) request the Run's network policy allows. Redirects are not followed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "method": { "type": "string", "enum": METHODS },
                    "url": { "type": "string" },
                    "headers": { "type": "object", "additionalProperties": { "type": "string" } },
                    "body": { "type": "string" },
                },
                "required": ["method", "url"],
                "additionalProperties": false,
            },
        }));
    }
    tools
}

fn shell_tool(name: &'static str) -> Value {
    let description = match name {
        "read_file" => "Read one UTF-8 text file under a mounted host directory.",
        "list_dir" => "List one directory under a mounted host directory.",
        "grep" => "Find lines containing a literal substring under a mounted path.",
        "git_status" => "git status --porcelain=v1 of a mounted worktree.",
        _ => "The unstaged git diff of a mounted worktree.",
    };
    let mut properties =
        json!({ "path": { "type": "string", "description": "An absolute guest path." } });
    let mut required = vec!["path"];
    if name == "grep" {
        properties["pattern"] = json!({ "type": "string" });
        required.push("pattern");
    }
    json!({
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": false,
        },
    })
}

fn parse_call(name: &str, arguments: Value) -> Result<Call, String> {
    let request = match name {
        "read_file" => ShellRequest::ReadFile {
            path: args::<PathArgs>(arguments)?.path,
        },
        "list_dir" => ShellRequest::ListDir {
            path: args::<PathArgs>(arguments)?.path,
        },
        "git_status" => ShellRequest::GitStatus {
            path: args::<PathArgs>(arguments)?.path,
        },
        "git_diff" => ShellRequest::GitDiff {
            path: args::<PathArgs>(arguments)?.path,
        },
        "grep" => {
            let GrepArgs { path, pattern } = args(arguments)?;
            ShellRequest::Grep { path, pattern }
        }
        "http_request" => return http_request(args(arguments)?).map(Call::Http),
        "secrets__list" => return args::<NoArgs>(arguments).map(|_| Call::Secrets),
        "secrets__get" => return Ok(Call::Secret(args::<SecretArgs>(arguments)?.name)),
        _ => return Err("unknown tool".into()),
    };
    Ok(Call::Shell(request))
}

fn args<T: DeserializeOwned>(arguments: Value) -> Result<T, String> {
    serde_json::from_value(arguments).map_err(|err| format!("invalid arguments: {err}"))
}

fn http_request(args: HttpArgs) -> Result<GateRequest, String> {
    let method = args.method.to_ascii_uppercase();
    if !METHODS.contains(&method.as_str()) {
        return Err(format!("method {:?} is not allowed", args.method));
    }
    let mut headers = HeaderMap::new();
    for (name, value) in args.headers {
        let lower = name.to_ascii_lowercase();
        if FORBIDDEN_HEADERS.contains(&lower.as_str()) || lower.starts_with("proxy-") {
            return Err(format!("header {name:?} is not allowed"));
        }
        let name = HeaderName::from_bytes(lower.as_bytes())
            .map_err(|_| format!("header name {name:?} is invalid"))?;
        let value = HeaderValue::from_str(&value)
            .map_err(|_| format!("header {name:?} has an invalid value"))?;
        headers.append(name, value);
    }
    Ok(GateRequest {
        method: Method::from_bytes(method.as_bytes()).map_err(|_| "invalid method".to_owned())?,
        url: args.url,
        headers,
        body: args.body.map(String::into_bytes),
    })
}

fn render_shell(response: ShellResponse) -> Result<String, String> {
    match response {
        ShellResponse::File(bytes) => {
            String::from_utf8(bytes).map_err(|_| "file is not UTF-8 text".to_owned())
        }
        ShellResponse::Entries(entries) => {
            Ok(Value::from_iter(entries.into_iter().map(
                |entry| json!({ "name": entry.name, "kind": entry.kind, "size": entry.size }),
            ))
            .to_string())
        }
        ShellResponse::Matches(matches) => {
            Ok(Value::from_iter(matches.into_iter().map(
                |found| json!({ "path": found.path, "line": found.line, "text": found.text }),
            ))
            .to_string())
        }
        ShellResponse::Text(text) => Ok(text),
    }
}

fn render_http(response: GateResponse) -> Result<String, String> {
    let body = String::from_utf8(response.body)
        .map_err(|_| "response body is not UTF-8 text".to_owned())?;
    let mut headers = BTreeMap::new();
    for (name, value) in &response.headers {
        headers.insert(
            name.as_str().to_owned(),
            String::from_utf8_lossy(value.as_bytes()).into_owned(),
        );
    }
    Ok(json!({ "status": response.status.as_u16(), "headers": headers, "body": body }).to_string())
}

fn tool_result(text: String, is_error: bool) -> Value {
    json!({ "content": [{ "type": "text", "text": text }], "isError": is_error })
}

fn reason(err: AgentError) -> String {
    match err {
        AgentError::Runtime(reason) => reason,
        _ => "gate failure".into(),
    }
}

fn elapsed_ms(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
}

fn notification(method: &str) -> Vec<u8> {
    let mut line = json!({ "jsonrpc": "2.0", "method": method })
        .to_string()
        .into_bytes();
    line.push(b'\n');
    line
}

fn error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

#[cfg(test)]
mod tests;
