//! HTTP/1.1 over the model port, one connection at a time. The port is one byte stream, so the
//! guest's open and close events on the QMP monitor are what mark where a connection ends.
use std::time::Duration;

use reqwest::StatusCode;
use tokio::io::{AsyncBufRead, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, Lines};

use super::{ModelGate, Relay, Reply, Request};
use crate::libs::error::AgentError;
use crate::libs::qemu;

const MAX_HEAD: usize = 64 * 1024;
const MAX_BODY: usize = 32 * 1024 * 1024;
const MAX_HEADERS: usize = 64;
const READ_CHUNK: usize = 64 * 1024;
const CONTINUE: &[u8] = b"HTTP/1.1 100 Continue\r\n\r\n";
const LAST_CHUNK: &[u8] = b"0\r\n\r\n";

/// Why a session over the port ended.
#[derive(Debug, PartialEq, Eq)]
pub enum Ending {
    /// The port or its monitor is gone.
    Closed,
    /// The guest closed a connection after the host wrote to it. Whatever the guest never read
    /// still sits in the socket and would reach the next connection, so the socket is reopened;
    /// `carry` holds what the next connection had already sent.
    Reconnect(Vec<u8>),
}

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

/// Serves the port until the guest closes a connection that was written to, or the port goes away.
pub async fn serve<R, W, E>(
    gate: &ModelGate,
    read: R,
    write: W,
    events: &mut Lines<E>,
    carry: Vec<u8>,
) -> Result<Ending, AgentError>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
    E: AsyncBufRead + Unpin,
{
    Session {
        gate,
        read,
        write,
        buffer: carry,
        wrote: false,
        answered: false,
        continued: false,
    }
    .run(events)
    .await
}

struct Session<'a, R, W> {
    gate: &'a ModelGate,
    read: R,
    write: W,
    buffer: Vec<u8>,
    wrote: bool,
    // After an answer the connection is over: anything more it sends is dropped until it closes.
    answered: bool,
    continued: bool,
}

impl<R, W> Session<'_, R, W>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    async fn run<E: AsyncBufRead + Unpin>(
        &mut self,
        events: &mut Lines<E>,
    ) -> Result<Ending, AgentError> {
        if !self.buffer.is_empty() {
            if let Some(ending) = self.process(events).await? {
                return Ok(ending);
            }
        }
        let mut chunk = vec![0; READ_CHUNK];
        loop {
            // Events first: a close the guest made before the next connection wrote must be seen first.
            tokio::select! {
                biased;
                line = events.next_line() => {
                    let Some(line) = line? else {
                        return Ok(Ending::Closed);
                    };
                    if qemu::port_closed(&line, qemu::MODEL_DEVICE) {
                        if self.wrote {
                            return Ok(Ending::Reconnect(drain(&mut self.read).await));
                        }
                        self.reset();
                    }
                }
                read = self.read.read(&mut chunk) => {
                    let read = read?;
                    if read == 0 {
                        return Ok(Ending::Closed);
                    }
                    if self.answered {
                        continue;
                    }
                    self.buffer.extend_from_slice(&chunk[..read]);
                    if let Some(ending) = self.process(events).await? {
                        return Ok(ending);
                    }
                }
            }
        }
    }

    fn reset(&mut self) {
        self.buffer.clear();
        self.answered = false;
        self.continued = false;
    }

    async fn process<E: AsyncBufRead + Unpin>(
        &mut self,
        events: &mut Lines<E>,
    ) -> Result<Option<Ending>, AgentError> {
        match parse(&self.buffer) {
            Parsed::Partial { expects_continue } => {
                if expects_continue && !self.continued {
                    self.continued = true;
                    if !self.put(events, CONTINUE).await? {
                        return Ok(Some(Ending::Reconnect(drain(&mut self.read).await)));
                    }
                }
                Ok(None)
            }
            Parsed::Refused {
                path,
                status,
                message,
            } => {
                self.buffer.clear();
                self.answered = true;
                let body = self.gate.refuse(&path, status, message);
                self.local(events, status, &body).await
            }
            Parsed::Complete(request) => {
                self.buffer.clear();
                self.answered = true;
                let gate = self.gate;
                let handling = gate.handle(request);
                tokio::pin!(handling);
                // A guest that gives up while the provider thinks drops the call before any answer.
                let reply = loop {
                    tokio::select! {
                        biased;
                        line = events.next_line() => {
                            let Some(line) = line? else {
                                return Ok(Some(Ending::Closed));
                            };
                            if qemu::port_closed(&line, qemu::MODEL_DEVICE) {
                                gate.abandoned();
                                if self.wrote {
                                    return Ok(Some(Ending::Reconnect(drain(&mut self.read).await)));
                                }
                                self.reset();
                                return Ok(None);
                            }
                        }
                        reply = &mut handling => break reply,
                    }
                };
                match reply {
                    Reply::Local { status, body } => self.local(events, status, &body).await,
                    Reply::Relay(relay) => self.relay(events, *relay).await,
                }
            }
        }
    }

    async fn local<E: AsyncBufRead + Unpin>(
        &mut self,
        events: &mut Lines<E>,
        status: u16,
        body: &[u8],
    ) -> Result<Option<Ending>, AgentError> {
        let mut answer = head(
            status,
            &[
                ("content-type".into(), "application/json".into()),
                ("content-length".into(), body.len().to_string()),
            ],
        );
        answer.extend_from_slice(body);
        if self.put(events, &answer).await? {
            Ok(None)
        } else {
            Ok(Some(Ending::Reconnect(drain(&mut self.read).await)))
        }
    }

    async fn relay<E: AsyncBufRead + Unpin>(
        &mut self,
        events: &mut Lines<E>,
        mut relay: Relay,
    ) -> Result<Option<Ending>, AgentError> {
        let mut headers = relay.headers.clone();
        headers.push(("transfer-encoding".into(), "chunked".into()));
        if !self.put(events, &head(relay.status, &headers)).await? {
            return self.aborted(&relay).await;
        }
        loop {
            let next = tokio::select! {
                biased;
                line = events.next_line() => {
                    let Some(line) = line? else {
                        self.gate.finish(&relay, Err("aborted"));
                        return Ok(Some(Ending::Closed));
                    };
                    if qemu::port_closed(&line, qemu::MODEL_DEVICE) {
                        return self.aborted(&relay).await;
                    }
                    continue;
                }
                next = relay.chunk() => next,
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
            if !self.put(events, &bytes).await? {
                return self.aborted(&relay).await;
            }
            if let Some(outcome) = outcome {
                self.gate
                    .finish(&relay, outcome.as_ref().map_err(String::as_str).copied());
                return Ok(None);
            }
        }
    }

    async fn aborted(&mut self, relay: &Relay) -> Result<Option<Ending>, AgentError> {
        self.gate.finish(relay, Err("aborted"));
        Ok(Some(Ending::Reconnect(drain(&mut self.read).await)))
    }

    /// Writes all of `bytes` unless the guest closes the port first, which answers `false`: a
    /// guest that stopped reading would otherwise block the write forever.
    async fn put<E: AsyncBufRead + Unpin>(
        &mut self,
        events: &mut Lines<E>,
        mut bytes: &[u8],
    ) -> Result<bool, AgentError> {
        while !bytes.is_empty() {
            tokio::select! {
                biased;
                line = events.next_line() => {
                    let Some(line) = line? else {
                        return Err(AgentError::Runtime("model port monitor closed".into()));
                    };
                    if qemu::port_closed(&line, qemu::MODEL_DEVICE) {
                        return Ok(false);
                    }
                }
                written = self.write.write(bytes) => {
                    let written = written?;
                    if written == 0 {
                        return Err(AgentError::Runtime("model port closed".into()));
                    }
                    self.wrote = true;
                    bytes = &bytes[written..];
                }
            }
        }
        self.write.flush().await?;
        Ok(true)
    }
}

/// What the next connection already sent: everything readable right now, without waiting.
async fn drain<R: AsyncRead + Unpin>(read: &mut R) -> Vec<u8> {
    let mut carry = Vec::new();
    let mut chunk = vec![0; READ_CHUNK];
    while let Ok(Ok(read)) = tokio::time::timeout(Duration::ZERO, read.read(&mut chunk)).await {
        if read == 0 || carry.len() > MAX_HEAD + MAX_BODY {
            break;
        }
        carry.extend_from_slice(&chunk[..read]);
    }
    carry
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
