//! The MCP endpoint a guest reaches through its virtio-serial port: the gates of its Run as tools.
use std::collections::{BTreeMap, HashSet};
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
const PARSE_ERROR: i64 = -32700;

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

enum Call {
    Shell(ShellRequest),
    Http(GateRequest),
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
}

impl<'a> Session<'a> {
    fn new(run_id: &'a str, gates: &'a RunGates, timeout: Duration) -> Self {
        Self {
            run_id,
            gates,
            timeout,
            seen: HashSet::new(),
        }
    }

    async fn run<R, W>(&mut self, read: R, mut write: W) -> Result<(), AgentError>
    where
        R: AsyncRead + Unpin,
        W: AsyncWrite + Unpin,
    {
        let mut reader = BufReader::new(read);
        let mut line = Vec::new();
        loop {
            line.clear();
            let read = (&mut reader)
                .take(MAX_LINE as u64 + 1)
                .read_until(b'\n', &mut line)
                .await?;
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
        let result = match method {
            "initialize" => {
                let requested = message
                    .pointer("/params/protocolVersion")
                    .and_then(Value::as_str);
                let version = requested
                    .filter(|version| PROTOCOL_VERSIONS.contains(version))
                    .unwrap_or(PROTOCOL_VERSIONS[0]);
                json!({
                    "protocolVersion": version,
                    "capabilities": { "tools": { "listChanged": false } },
                    "serverInfo": { "name": "naos", "version": env!("CARGO_PKG_VERSION") },
                })
            }
            "ping" => json!({}),
            "tools/list" => json!({ "tools": tools(self.gates) }),
            "tools/call" => match self.call(message.get("params")).await {
                Ok(result) => result,
                Err(message) => return Ok(Some(error(id, INVALID_PARAMS, &message))),
            },
            _ => return Ok(Some(error(id, METHOD_NOT_FOUND, "method not found"))),
        };
        Ok(Some(
            json!({ "jsonrpc": "2.0", "id": id, "result": result }),
        ))
    }

    /// A malformed call is a JSON-RPC error; a gate refusal is a tool result the agent can read.
    async fn call(&self, params: Option<&Value>) -> Result<Value, String> {
        let started = Instant::now();
        let name = params
            .and_then(|params| params.get("name"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        let arguments = params
            .and_then(|params| params.get("arguments"))
            .cloned()
            .unwrap_or_else(|| json!({}));
        let tool = if is_tool(name) { name } else { "unknown" };
        let call = match parse_call(name, arguments) {
            Ok(call) => call,
            Err(reason) => {
                audit::mcp_call(self.run_id, tool, "deny", elapsed_ms(started), "invalid");
                return Err(reason);
            }
        };
        let outcome = tokio::time::timeout(self.timeout, self.dispatch(call)).await;
        let (text, is_error) = match outcome {
            Ok(Ok(text)) => {
                audit::mcp_call(self.run_id, tool, "allow", elapsed_ms(started), "none");
                (text, false)
            }
            Ok(Err(reason)) => {
                audit::mcp_call(self.run_id, tool, "deny", elapsed_ms(started), "denied");
                (reason, true)
            }
            Err(_) => {
                audit::mcp_call(self.run_id, tool, "deny", elapsed_ms(started), "timeout");
                ("call timed out".to_owned(), true)
            }
        };
        Ok(json!({ "content": [{ "type": "text", "text": text }], "isError": is_error }))
    }

    async fn dispatch(&self, call: Call) -> Result<String, String> {
        match call {
            Call::Shell(request) => {
                render_shell(self.gates.shell.call(request).await.map_err(reason)?)
            }
            Call::Http(request) => {
                render_http(self.gates.network.send(request).await.map_err(reason)?)
            }
        }
    }
}

fn is_tool(name: &str) -> bool {
    matches!(
        name,
        "read_file" | "list_dir" | "grep" | "git_status" | "git_diff" | "http_request"
    )
}

/// Only what the Run was granted is listed; a call to anything else still meets the gate.
fn tools(gates: &RunGates) -> Vec<Value> {
    let mut tools: Vec<Value> = gates.shell.granted().map(shell_tool).collect();
    if gates.network.allows_any() {
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

fn reason(err: AgentError) -> String {
    match err {
        AgentError::Runtime(reason) => reason,
        _ => "gate failure".into(),
    }
}

fn elapsed_ms(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX)
}

fn error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

#[cfg(test)]
mod tests;
