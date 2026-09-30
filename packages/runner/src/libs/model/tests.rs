use std::net::SocketAddr;

use tokio::io::{AsyncReadExt, AsyncWriteExt, DuplexStream};
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::*;

const OPENAI_KEY: &str = "secret-alpha-value";
const ANTHROPIC_KEY: &str = "secret-beta-value";

fn document(address: SocketAddr, budget: (u64, u64), rate: u32) -> Value {
    let url = format!("http://example.com:{}", address.port());
    json!({
        "providers": [
            {
                "name": "alpha", "api": "openai", "url": url, "credential": "alpha-key",
                "models": ["alpha-mini"], "timeout_seconds": 5, "max_requests_per_minute": rate,
            },
            {
                "name": "beta", "api": "anthropic", "url": url, "credential": "beta-key",
                "models": ["beta-large"], "timeout_seconds": 5, "max_requests_per_minute": rate,
            },
        ],
        "max_input_tokens": budget.0,
        "max_output_tokens": budget.1,
    })
}

fn gate_for(server: &MockServer, budget: (u64, u64), rate: u32) -> ModelGate {
    let address = *server.address();
    let gate =
        ModelGate::local(&document(address, budget, rate), vec![address.ip()]).expect("policy");
    grant(&gate, u64::MAX);
    gate
}

fn gate(server: &MockServer) -> ModelGate {
    gate_for(server, (1_000_000, 1_000_000), 60)
}

fn grant(gate: &ModelGate, expires_at: u64) {
    gate.refresh(&BTreeMap::from([
        (
            "alpha-key".to_owned(),
            RunCredential {
                value: OPENAI_KEY.into(),
                expires_at,
            },
        ),
        (
            "beta-key".to_owned(),
            RunCredential {
                value: ANTHROPIC_KEY.into(),
                expires_at,
            },
        ),
        (
            "unrelated".to_owned(),
            RunCredential {
                value: "not-for-this-gate".into(),
                expires_at,
            },
        ),
    ]));
}

fn request(path: &str, body: Value) -> Request {
    Request {
        method: "POST".into(),
        path: path.into(),
        query: None,
        headers: vec![
            ("content-type".into(), "application/json".into()),
            ("authorization".into(), "Bearer placeholder".into()),
            ("x-api-key".into(), "placeholder".into()),
            ("cookie".into(), "session=alpha".into()),
            ("anthropic-version".into(), "2023-06-01".into()),
        ],
        body: body.to_string().into_bytes(),
    }
}

fn chat(model: &str) -> Request {
    request(CHAT, json!({ "model": model, "messages": [] }))
}

fn messages(model: &str) -> Request {
    request(
        MESSAGES,
        json!({ "model": model, "max_tokens": 16, "messages": [] }),
    )
}

/// Answers the reply the way the guest would read it: status, then the whole redacted body.
async fn answer(gate: &ModelGate, request: Request) -> (u16, Value) {
    let (status, body) = raw(gate, request).await;
    (status, serde_json::from_slice(&body).unwrap_or(Value::Null))
}

async fn raw(gate: &ModelGate, request: Request) -> (u16, Vec<u8>) {
    match gate.handle(request).await {
        Reply::Local { status, body } => (status, body),
        Reply::Relay(mut relay) => {
            let mut body = Vec::new();
            let mut outcome = Ok(());
            loop {
                match relay.chunk().await {
                    Ok(Some(bytes)) => body.extend_from_slice(&bytes),
                    Ok(None) => break,
                    Err(reason) => {
                        outcome = Err(reason);
                        break;
                    }
                }
            }
            gate.finish(&relay, outcome.as_ref().map_err(String::as_str).copied());
            (relay.status, body)
        }
    }
}

fn openai_answer(input: u64, output: u64) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_json(json!({
        "choices": [{ "message": { "content": "alpha" } }],
        "usage": { "prompt_tokens": input, "completion_tokens": output },
    }))
}

fn anthropic_answer(input: u64, output: u64) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_json(json!({
        "content": [{ "type": "text", "text": "beta" }],
        "usage": { "input_tokens": input, "output_tokens": output },
    }))
}

#[tokio::test]
async fn each_dialect_reaches_its_provider_with_the_providers_key_only() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path(CHAT))
        .and(header(
            "authorization",
            format!("Bearer {OPENAI_KEY}").as_str(),
        ))
        .respond_with(openai_answer(3, 1))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path(MESSAGES))
        .and(header("x-api-key", ANTHROPIC_KEY))
        .and(header("anthropic-version", "2023-06-01"))
        .respond_with(anthropic_answer(4, 2))
        .expect(1)
        .mount(&server)
        .await;
    let gate = gate(&server);

    let (openai, _) = answer(&gate, chat("alpha-mini")).await;
    let (anthropic, _) = answer(&gate, messages("beta-large")).await;

    assert_eq!((openai, anthropic), (200, 200));
    for received in server.received_requests().await.expect("requests") {
        let names: Vec<&str> = received.headers.keys().map(|name| name.as_str()).collect();
        assert!(!names.contains(&"cookie"), "{names:?}");
        let auth = [
            received.headers.get("authorization"),
            received.headers.get("x-api-key"),
        ];
        assert_eq!(auth.iter().flatten().count(), 1, "{names:?}");
        assert!(!format!("{:?}", received.headers).contains("placeholder"));
    }
}

#[tokio::test]
async fn unknown_paths_models_and_dialects_never_reach_a_provider() {
    let server = MockServer::start().await;
    let gate = gate(&server);

    let (unknown_path, _) = answer(
        &gate,
        request("/v1/embeddings", json!({"model": "alpha-mini"})),
    )
    .await;
    let (outside, body) = answer(&gate, chat("gamma")).await;
    let (crossed, crossed_body) = answer(&gate, messages("alpha-mini")).await;
    let (no_model, _) = answer(&gate, request(CHAT, json!({"messages": []}))).await;
    let (not_json, _) = answer(
        &gate,
        Request {
            body: b"alpha".to_vec(),
            ..chat("alpha-mini")
        },
    )
    .await;

    assert_eq!(
        (unknown_path, outside, crossed, no_model, not_json),
        (404, 404, 404, 400, 400)
    );
    assert_eq!(body["error"]["code"], "model_not_found");
    assert_eq!(crossed_body["type"], "error");
    assert_eq!(crossed_body["error"]["type"], "not_found_error");
    assert!(server
        .received_requests()
        .await
        .expect("requests")
        .is_empty());
}

#[tokio::test]
async fn the_model_list_comes_from_the_policy() {
    let server = MockServer::start().await;
    let gate = gate(&server);

    let (status, body) = answer(
        &gate,
        Request {
            method: "GET".into(),
            body: vec![],
            ..request(MODELS, json!({}))
        },
    )
    .await;

    assert_eq!(status, 200);
    assert_eq!(body["data"][0]["id"], "alpha-mini");
    assert_eq!(body["data"].as_array().map(Vec::len), Some(1));
    assert!(server
        .received_requests()
        .await
        .expect("requests")
        .is_empty());
}

#[tokio::test]
async fn a_spent_budget_is_refused_in_each_dialect() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(openai_answer(10, 5))
        .mount(&server)
        .await;
    let gate = gate_for(&server, (1_000, 5), 60);

    let (first, _) = answer(&gate, chat("alpha-mini")).await;
    let (openai, openai_body) = answer(&gate, chat("alpha-mini")).await;
    let (anthropic, anthropic_body) = answer(&gate, messages("beta-large")).await;

    assert_eq!((first, openai, anthropic), (200, 429, 429));
    assert_eq!(
        gate.spent(),
        Usage {
            input: 10,
            output: 5
        }
    );
    assert_eq!(openai_body["error"]["type"], "rate_limit_exceeded");
    assert_eq!(anthropic_body["error"]["type"], "rate_limit_error");
    assert_eq!(server.received_requests().await.expect("requests").len(), 1);
}

#[tokio::test]
async fn one_run_never_spends_another_runs_budget() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(openai_answer(10, 5))
        .mount(&server)
        .await;
    let spent = gate_for(&server, (1_000, 5), 60);
    let fresh = gate_for(&server, (1_000, 5), 60);

    answer(&spent, chat("alpha-mini")).await;

    assert_eq!(answer(&spent, chat("alpha-mini")).await.0, 429);
    assert_eq!(answer(&fresh, chat("alpha-mini")).await.0, 200);
}

#[tokio::test]
async fn the_request_rate_is_capped_per_provider() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(openai_answer(1, 1))
        .mount(&server)
        .await;
    let gate = gate_for(&server, (1_000, 1_000), 1);

    assert_eq!(answer(&gate, chat("alpha-mini")).await.0, 200);
    assert_eq!(answer(&gate, chat("alpha-mini")).await.0, 429);
}

#[tokio::test]
async fn a_missing_or_expired_credential_never_reaches_the_provider() {
    let server = MockServer::start().await;
    let address = *server.address();
    let missing =
        ModelGate::local(&document(address, (10, 10), 60), vec![address.ip()]).expect("policy");
    let expired = gate(&server);
    grant(&expired, 1);

    let (status, body) = answer(&missing, chat("alpha-mini")).await;

    assert_eq!(status, 502);
    assert_eq!(body["error"]["message"], "credential unavailable");
    assert_eq!(answer(&expired, messages("beta-large")).await.0, 502);
    assert!(server
        .received_requests()
        .await
        .expect("requests")
        .is_empty());
}

#[tokio::test]
async fn a_provider_failure_is_passed_on_without_a_retry() {
    let server = MockServer::start().await;
    Mock::given(path(MESSAGES))
        .respond_with(ResponseTemplate::new(500).set_body_json(json!({"error": "down"})))
        .mount(&server)
        .await;
    let gate = gate(&server);

    let (status, _) = answer(&gate, messages("beta-large")).await;

    assert_eq!(status, 500);
    assert_eq!(server.received_requests().await.expect("requests").len(), 1);
}

#[tokio::test]
async fn a_slow_provider_times_out() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(openai_answer(1, 1).set_delay(Duration::from_secs(3)))
        .mount(&server)
        .await;
    let address = *server.address();
    let mut slow = document(address, (10, 10), 60);
    slow["providers"][0]["timeout_seconds"] = json!(1);
    let gate = ModelGate::local(&slow, vec![address.ip()]).expect("policy");
    grant(&gate, u64::MAX);

    let (status, body) = answer(&gate, chat("alpha-mini")).await;

    assert_eq!(status, 504);
    assert_eq!(body["error"]["message"], "provider timed out");
}

#[tokio::test]
async fn an_echoed_key_is_redacted() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(ResponseTemplate::new(401).set_body_string(format!(
            r#"{{"error": {{"message": "key {OPENAI_KEY} is not valid"}}}}"#
        )))
        .mount(&server)
        .await;
    let gate = gate(&server);

    let (status, body) = raw(&gate, chat("alpha-mini")).await;

    assert_eq!(status, 401);
    let text = String::from_utf8(body).expect("utf-8");
    assert!(!text.contains(OPENAI_KEY));
    assert!(text.contains("<redacted>"));
}

#[test]
fn a_key_split_across_chunks_is_still_redacted() {
    let mut redactor = Redactor::new(b"secret".to_vec());

    let mut out = redactor.feed(b"a sec");
    out.extend(redactor.feed(b"ret b se"));
    out.extend(redactor.feed(b"cr"));
    out.extend(redactor.finish());

    assert_eq!(out, b"a <redacted> b secr");
}

#[tokio::test]
async fn usage_is_read_from_both_event_streams() {
    let server = MockServer::start().await;
    let openai = "data: {\"choices\":[{\"delta\":{\"content\":\"a\"}}]}\n\n\
                  data: {\"choices\":[],\"usage\":{\"prompt_tokens\":7,\"completion_tokens\":2}}\n\n\
                  data: [DONE]\n\n";
    let anthropic = "event: message_start\n\
                     data: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":5,\"cache_read_input_tokens\":3,\"output_tokens\":1}}}\n\n\
                     event: message_delta\n\
                     data: {\"type\":\"message_delta\",\"usage\":{\"output_tokens\":9}}\n\n";
    Mock::given(path(CHAT))
        .respond_with(ResponseTemplate::new(200).set_body_raw(openai, "text/event-stream"))
        .mount(&server)
        .await;
    Mock::given(path(MESSAGES))
        .respond_with(ResponseTemplate::new(200).set_body_raw(anthropic, "text/event-stream"))
        .mount(&server)
        .await;
    let gate = gate(&server);

    raw(
        &gate,
        request(CHAT, json!({"model": "alpha-mini", "stream": true})),
    )
    .await;
    raw(
        &gate,
        request(MESSAGES, json!({"model": "beta-large", "stream": true})),
    )
    .await;

    assert_eq!(
        gate.spent(),
        Usage {
            input: 7 + 8,
            output: 2 + 9
        }
    );
    let sent: Value =
        serde_json::from_slice(&server.received_requests().await.expect("requests")[0].body)
            .expect("json");
    assert_eq!(sent["stream_options"]["include_usage"], true);
}

fn wire(request: &str, body: &str) -> Vec<u8> {
    format!("{request}\r\ncontent-length: {}\r\n\r\n{body}", body.len()).into_bytes()
}

/// Reads one answer the way the guest's client does, up to the end of its body.
async fn read_answer(guest: &mut DuplexStream) -> (u16, String) {
    let mut seen = Vec::new();
    let mut byte = [0u8; 1];
    while !seen.ends_with(b"\r\n\r\n") {
        guest.read_exact(&mut byte).await.expect("head");
        seen.push(byte[0]);
    }
    let head = String::from_utf8(seen).expect("utf-8");
    let status: u16 = head[9..12].parse().expect("status");
    assert!(head.contains("connection: close"), "{head}");
    let length = head
        .lines()
        .find_map(|line| line.strip_prefix("content-length: "))
        .map(|value| value.trim().parse::<usize>().expect("length"));
    let body = match length {
        Some(length) => {
            let mut body = vec![0; length];
            guest.read_exact(&mut body).await.expect("body");
            body
        }
        None => {
            let mut body = Vec::new();
            loop {
                let mut size = Vec::new();
                while !size.ends_with(b"\r\n") {
                    guest.read_exact(&mut byte).await.expect("size");
                    size.push(byte[0]);
                }
                let size = usize::from_str_radix(
                    std::str::from_utf8(&size[..size.len() - 2]).expect("utf-8"),
                    16,
                )
                .expect("hex");
                let mut chunk = vec![0; size + 2];
                guest.read_exact(&mut chunk).await.expect("chunk");
                if size == 0 {
                    break;
                }
                body.extend_from_slice(&chunk[..size]);
            }
            body
        }
    };
    (status, String::from_utf8(body).expect("utf-8"))
}

/// Serves one connection while `guest` plays the client on its other end.
async fn connect<F, Fut>(gate: &ModelGate, guest: F)
where
    F: FnOnce(DuplexStream) -> Fut,
    Fut: std::future::Future<Output = ()>,
{
    let (guest_end, host_end) = tokio::io::duplex(1 << 20);
    let (read, write) = tokio::io::split(host_end);
    let (served, ()) = tokio::join!(serve(gate, read, write), guest(guest_end));
    served.expect("connection");
}

#[tokio::test]
async fn a_request_over_a_connection_is_answered_and_the_connection_ends() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(openai_answer(1, 1))
        .mount(&server)
        .await;
    let gate = gate(&server);

    connect(&gate, |mut guest| async move {
        let body = json!({"model": "alpha-mini", "messages": []}).to_string();
        guest
            .write_all(&wire(
                "POST /v1/chat/completions HTTP/1.1\r\nhost: 127.0.0.1",
                &body,
            ))
            .await
            .expect("request");
        let (status, answer) = read_answer(&mut guest).await;
        assert_eq!(status, 200);
        assert!(answer.contains("alpha"), "{answer}");
        let mut rest = Vec::new();
        guest.read_to_end(&mut rest).await.expect("end");
        assert!(rest.is_empty());
    })
    .await;
}

async fn chat_call(gate: &ModelGate) {
    connect(gate, |mut guest| async move {
        let body = json!({"model": "alpha-mini", "messages": []}).to_string();
        guest
            .write_all(&wire("POST /v1/chat/completions HTTP/1.1", &body))
            .await
            .expect("request");
        assert_eq!(read_answer(&mut guest).await.0, 200);
    })
    .await;
}

#[tokio::test]
async fn connections_are_served_side_by_side() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(openai_answer(1, 1).set_delay(Duration::from_millis(300)))
        .mount(&server)
        .await;
    let gate = gate(&server);
    let started = std::time::Instant::now();

    tokio::join!(chat_call(&gate), chat_call(&gate), chat_call(&gate));

    assert!(
        started.elapsed() < Duration::from_millis(800),
        "{:?}",
        started.elapsed()
    );
    assert_eq!(
        gate.spent(),
        Usage {
            input: 3,
            output: 3
        }
    );
}

#[tokio::test]
async fn malformed_requests_are_refused() {
    let server = MockServer::start().await;
    let gate = gate(&server);

    for (request, expected) in [
        (b"POST /v1/chat/completions HTTP/1.1\r\n\r\n".to_vec(), 411),
        (
            b"POST /v1/messages HTTP/1.1\r\ntransfer-encoding: chunked\r\n\r\n".to_vec(),
            411,
        ),
        (
            b"POST /v1/messages HTTP/1.1\r\ncontent-length: 999999999\r\n\r\n".to_vec(),
            413,
        ),
        (b"\x00\x01 not http\r\n\r\n".to_vec(), 400),
    ] {
        connect(&gate, |mut guest| async move {
            guest.write_all(&request).await.expect("request");
            assert_eq!(read_answer(&mut guest).await.0, expected);
        })
        .await;
    }
    assert!(server
        .received_requests()
        .await
        .expect("requests")
        .is_empty());
}

#[tokio::test]
async fn a_client_waiting_for_continue_gets_it() {
    let server = MockServer::start().await;
    let gate = gate(&server);

    connect(&gate, |mut guest| async move {
        guest
            .write_all(b"POST /v1/chat/completions HTTP/1.1\r\nexpect: 100-continue\r\ncontent-length: 2\r\n\r\n")
            .await
            .expect("head");
        let mut interim = [0u8; 25];
        guest.read_exact(&mut interim).await.expect("continue");
        assert_eq!(&interim, b"HTTP/1.1 100 Continue\r\n\r\n");
        guest.write_all(b"{}").await.expect("body");
        assert_eq!(read_answer(&mut guest).await.0, 400);
    })
    .await;
}

#[tokio::test]
async fn a_client_that_leaves_before_the_answer_drops_the_call() {
    let server = MockServer::start().await;
    Mock::given(path(CHAT))
        .respond_with(openai_answer(1, 1).set_delay(Duration::from_secs(2)))
        .mount(&server)
        .await;
    let gate = gate(&server);
    let started = std::time::Instant::now();

    connect(&gate, |mut guest| async move {
        let body = json!({"model": "alpha-mini", "messages": []}).to_string();
        guest
            .write_all(&wire("POST /v1/chat/completions HTTP/1.1", &body))
            .await
            .expect("request");
        tokio::time::sleep(Duration::from_millis(100)).await;
    })
    .await;

    assert!(
        started.elapsed() < Duration::from_secs(1),
        "{:?}",
        started.elapsed()
    );
    assert_eq!(gate.spent(), Usage::default());
}

#[tokio::test]
async fn a_client_that_leaves_during_a_stream_ends_the_relay() {
    let server = MockServer::start().await;
    let long = "data: {\"choices\":[{\"delta\":{\"content\":\"a\"}}]}\n\n".repeat(100_000);
    Mock::given(path(CHAT))
        .respond_with(ResponseTemplate::new(200).set_body_raw(long, "text/event-stream"))
        .mount(&server)
        .await;
    let gate = gate(&server);

    connect(&gate, |mut guest| async move {
        let body = json!({"model": "alpha-mini", "stream": true}).to_string();
        guest
            .write_all(&wire("POST /v1/chat/completions HTTP/1.1", &body))
            .await
            .expect("request");
        let mut first = [0u8; 64];
        guest.read_exact(&mut first).await.expect("first bytes");
    })
    .await;
}
