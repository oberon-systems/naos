//! The MCP endpoint a guest reaches through its virtio-serial port. No tool is offered yet.
use crate::libs::audit;
use crate::libs::error::AgentError;
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};

const MAX_LINE: usize = 1024 * 1024;
const PROTOCOL_VERSIONS: &[&str] = &["2025-06-18", "2025-03-26", "2024-11-05"];

/// Serves newline-delimited JSON-RPC until the guest side closes or breaks the framing.
pub async fn serve<R, W>(run_id: &str, read: R, mut write: W) -> Result<(), AgentError>
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
            audit::mcp_rejected(run_id, "line too long");
            return Err(AgentError::Runtime("mcp: line too long".into()));
        }
        let message = line.trim_ascii();
        if message.is_empty() {
            continue;
        }
        if let Some(reply) = respond(run_id, message) {
            let mut out = reply.to_string().into_bytes();
            out.push(b'\n');
            write.write_all(&out).await?;
            write.flush().await?;
        }
    }
}

fn respond(run_id: &str, raw: &[u8]) -> Option<Value> {
    let Ok(message) = serde_json::from_slice::<Value>(raw) else {
        audit::mcp_rejected(run_id, "unparseable message");
        return Some(error(Value::Null, -32700, "parse error"));
    };
    if !message.is_object() || message.get("jsonrpc") != Some(&json!("2.0")) {
        audit::mcp_rejected(run_id, "not a JSON-RPC 2.0 object");
        return Some(error(Value::Null, -32600, "invalid request"));
    }
    // Without an id this is a notification, or a response to nothing the host asked, and gets no reply.
    let id = message.get("id")?.clone();
    let Some(method) = message.get("method").and_then(Value::as_str) else {
        audit::mcp_rejected(run_id, "request without a method");
        return Some(error(id, -32600, "invalid request"));
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
        "tools/list" => json!({ "tools": [] }),
        _ => return Some(error(id, -32601, "method not found")),
    };
    Some(json!({ "jsonrpc": "2.0", "id": id, "result": result }))
}

fn error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

#[cfg(test)]
mod tests;
