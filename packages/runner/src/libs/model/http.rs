//! HTTP/1.1 over one guest connection: one request, one answer, then the connection is closed.
use std::future::Future;

use reqwest::StatusCode;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

use super::{ModelGate, Relay, Reply, Request};
use crate::libs::error::AgentError;

const MAX_HEAD: usize = 64 * 1024;
const MAX_BODY: usize = 32 * 1024 * 1024;
const MAX_HEADERS: usize = 64;
const READ_CHUNK: usize = 64 * 1024;
const CONTINUE: &[u8] = b"HTTP/1.1 100 Continue\r\n\r\n";
const LAST_CHUNK: &[u8] = b"0\r\n\r\n";

enum Parsed {
    Partial {
        expects_continue: bool,
    },
    Complete(Request),
    Refused {
        path: String,
        status: u16,
        message: &'static str,
    },
}

/// Answers the one request a guest connection carries. A guest that goes away first drops the
/// provider call; one that stops reading fails the write and ends the relay.
pub async fn serve<R, W>(gate: &ModelGate, mut read: R, mut write: W) -> Result<(), AgentError>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut buffer = Vec::new();
    let mut chunk = vec![0; READ_CHUNK];
    let mut continued = false;
    let request = loop {
        match parse(&buffer) {
            Parsed::Complete(request) => break request,
            Parsed::Refused {
                path,
                status,
                message,
            } => {
                let body = gate.refuse(&path, status, message);
                return local(&mut write, status, &body).await;
            }
            Parsed::Partial { expects_continue } => {
                if expects_continue && !continued {
                    continued = true;
                    write.write_all(CONTINUE).await?;
                    write.flush().await?;
                }
            }
        }
        let read = read.read(&mut chunk).await?;
        if read == 0 {
            return Ok(());
        }
        buffer.extend_from_slice(&chunk[..read]);
    };

    let Some(reply) = until_closed(&mut read, gate.handle(request)).await else {
        gate.abandoned();
        return Ok(());
    };
    match reply {
        Reply::Local { status, body } => local(&mut write, status, &body).await,
        Reply::Relay(relay) => relay_to(gate, &mut read, &mut write, *relay).await,
    }
}

/// Runs `work` unless the guest closes its side first, which answers `None`.
async fn until_closed<R, F>(read: &mut R, work: F) -> Option<F::Output>
where
    R: AsyncRead + Unpin,
    F: Future,
{
    tokio::pin!(work);
    let mut sink = vec![0; READ_CHUNK];
    loop {
        tokio::select! {
            biased;
            output = &mut work => return Some(output),
            read = read.read(&mut sink) => {
                // Bytes after the request mean nothing on a one-request connection; only the close counts.
                if matches!(read, Ok(0) | Err(_)) {
                    return None;
                }
            }
        }
    }
}

async fn local<W: AsyncWrite + Unpin>(
    write: &mut W,
    status: u16,
    body: &[u8],
) -> Result<(), AgentError> {
    let mut answer = head(
        status,
        &[
            ("content-type".into(), "application/json".into()),
            ("content-length".into(), body.len().to_string()),
        ],
    );
    answer.extend_from_slice(body);
    write.write_all(&answer).await?;
    write.shutdown().await?;
    Ok(())
}

async fn relay_to<R, W>(
    gate: &ModelGate,
    read: &mut R,
    write: &mut W,
    mut relay: Relay,
) -> Result<(), AgentError>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut headers = relay.headers.clone();
    headers.push(("transfer-encoding".into(), "chunked".into()));
    if write
        .write_all(&head(relay.status, &headers))
        .await
        .is_err()
    {
        gate.finish(&relay, Err("aborted"));
        return Ok(());
    }
    loop {
        let Some(next) = until_closed(read, relay.chunk()).await else {
            gate.finish(&relay, Err("aborted"));
            return Ok(());
        };
        let (bytes, outcome) = match next {
            Ok(Some(bytes)) => (framed(&bytes), None),
            Ok(None) => (LAST_CHUNK.to_vec(), Some(Ok(()))),
            Err(reason) => {
                let mut bytes = relay
                    .stream_error(&reason)
                    .map(|error| framed(&error))
                    .unwrap_or_default();
                bytes.extend_from_slice(LAST_CHUNK);
                (bytes, Some(Err(reason)))
            }
        };
        if write.write_all(&bytes).await.is_err() || write.flush().await.is_err() {
            gate.finish(&relay, Err("aborted"));
            return Ok(());
        }
        if let Some(outcome) = outcome {
            gate.finish(&relay, outcome.as_ref().map_err(String::as_str).copied());
            let _ = write.shutdown().await;
            return Ok(());
        }
    }
}

fn parse(buffer: &[u8]) -> Parsed {
    let mut headers = [httparse::EMPTY_HEADER; MAX_HEADERS];
    let mut request = httparse::Request::new(&mut headers);
    let refused = |status: u16, message: &'static str| Parsed::Refused {
        path: String::new(),
        status,
        message,
    };
    let head = match request.parse(buffer) {
        Ok(httparse::Status::Complete(head)) if head <= MAX_HEAD => head,
        Ok(httparse::Status::Partial) if buffer.len() <= MAX_HEAD => {
            return Parsed::Partial {
                expects_continue: false,
            }
        }
        Ok(_) | Err(httparse::Error::TooManyHeaders) => {
            return refused(431, "request head too large")
        }
        Err(_) => return refused(400, "malformed request"),
    };
    let method = request.method.unwrap_or_default().to_owned();
    let (path, query) = match request.path.unwrap_or("/").split_once('?') {
        Some((path, query)) => (path.to_owned(), Some(query.to_owned())),
        None => (request.path.unwrap_or("/").to_owned(), None),
    };
    let mut fields = Vec::new();
    for header in request.headers.iter() {
        let Ok(value) = std::str::from_utf8(header.value) else {
            continue;
        };
        fields.push((header.name.to_ascii_lowercase(), value.trim().to_owned()));
    }
    let field = |name: &str| {
        fields
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    };
    let refuse = |status: u16, message: &'static str| Parsed::Refused {
        path: path.clone(),
        status,
        message,
    };
    if field("transfer-encoding").is_some() {
        return refuse(411, "a body needs a content-length");
    }
    let length = match field("content-length").map(str::parse::<usize>) {
        Some(Ok(length)) => length,
        Some(Err(_)) => return refuse(400, "content-length is not a number"),
        None if method == "POST" => return refuse(411, "a body needs a content-length"),
        None => 0,
    };
    if length > MAX_BODY {
        return refuse(413, "request body too large");
    }
    if buffer.len() < head + length {
        return Parsed::Partial {
            expects_continue: field("expect")
                .is_some_and(|value| value.eq_ignore_ascii_case("100-continue")),
        };
    }
    let body = buffer[head..head + length].to_vec();
    Parsed::Complete(Request {
        method,
        path,
        query,
        headers: fields,
        body,
    })
}

fn head(status: u16, headers: &[(String, String)]) -> Vec<u8> {
    let reason = StatusCode::from_u16(status)
        .ok()
        .and_then(|status| status.canonical_reason())
        .unwrap_or("");
    let mut out = format!("HTTP/1.1 {status} {reason}\r\n");
    for (name, value) in headers {
        out.push_str(&format!("{name}: {value}\r\n"));
    }
    out.push_str("connection: close\r\n\r\n");
    out.into_bytes()
}

// An empty chunk would end the body, so only bytes are framed.
fn framed(bytes: &[u8]) -> Vec<u8> {
    if bytes.is_empty() {
        return Vec::new();
    }
    let mut out = format!("{:x}\r\n", bytes.len()).into_bytes();
    out.extend_from_slice(bytes);
    out.extend_from_slice(b"\r\n");
    out
}
