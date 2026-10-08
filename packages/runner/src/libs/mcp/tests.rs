use std::fs;
use std::net::SocketAddr;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use tempfile::TempDir;
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

use super::*;
use crate::libs::api::RunCredential;
use crate::libs::model::ModelGate;
use crate::libs::network::NetworkGate;
use crate::libs::shell::ShellGate;

const GUEST: &str = "/naos/alpha";
const SECRET: &str = "secret-alpha-value";

fn no_shell() -> ShellGate {
    ShellGate::from_snapshot("run_a", None, None, Path::new("/usr/bin/git")).expect("policy")
}

fn no_mcp() -> McpGate {
    McpGate::from_snapshot("run_a", None).expect("policy")
}

fn ruled_mcp(rules: Value) -> McpGate {
    McpGate::from_snapshot("run_a", Some(&json!({ "servers": [], "rules": rules })))
        .expect("policy")
}

fn open_mcp() -> McpGate {
    ruled_mcp(json!([
        {"server": "shell", "tool": "*", "effect": "allow"},
        {"server": "network", "tool": "*", "effect": "allow"},
    ]))
}

fn no_model() -> ModelGate {
    ModelGate::from_snapshot("run_a", None).expect("policy")
}

fn no_gates() -> RunGates {
    RunGates {
        network: NetworkGate::from_snapshot("run_a", None).expect("policy"),
        shell: no_shell(),
        mcp: no_mcp(),
        model: no_model(),
    }
}

fn shell_gates(dir: &TempDir, allow: &[&str]) -> RunGates {
    let mounts = json!({
        "workdir": GUEST,
        "mounts": [{
            "host_path": dir.path().to_str().expect("utf-8"),
            "guest_path": GUEST,
            "mode": "ro",
        }],
    });
    RunGates {
        network: NetworkGate::from_snapshot("run_a", None).expect("policy"),
        shell: ShellGate::from_snapshot(
            "run_a",
            Some(&json!({ "allow": allow })),
            Some(&mounts),
            Path::new("/usr/bin/git"),
        )
        .expect("policy"),
        mcp: open_mcp(),
        model: no_model(),
    }
}

fn http_gates(address: SocketAddr) -> RunGates {
    RunGates {
        network: NetworkGate::local(
            &json!({"allow": [{"protocol": "http", "host": "example.com"}]}),
            vec![address.ip()],
        )
        .expect("policy"),
        shell: no_shell(),
        mcp: open_mcp(),
        model: no_model(),
    }
}

async fn exchange_within(
    gates: &RunGates,
    timeout: Duration,
    input: &[u8],
) -> (Result<(), AgentError>, Vec<Value>) {
    let mut out = Vec::new();
    let outcome = Session::new("run_a", gates, timeout)
        .run(input, &mut out)
        .await;
    let replies = out
        .split(|byte| *byte == b'\n')
        .filter(|line| !line.is_empty())
        .map(|line| serde_json::from_slice(line).expect("json"))
        .collect();
    (outcome, replies)
}

async fn exchange(gates: &RunGates, input: &[u8]) -> (Result<(), AgentError>, Vec<Value>) {
    exchange_within(gates, CALL_TIMEOUT, input).await
}

fn call(id: u64, name: &str, arguments: Value) -> String {
    let message = json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/call",
        "params": { "name": name, "arguments": arguments },
    });
    format!("{message}\n")
}

fn tool_text(reply: &Value) -> (bool, String) {
    (
        reply["result"]["isError"].as_bool().expect("isError"),
        reply["result"]["content"][0]["text"]
            .as_str()
            .expect("text")
            .to_owned(),
    )
}

fn error_code(reply: &Value) -> i64 {
    reply["error"]["code"].as_i64().expect("code")
}

#[tokio::test]
async fn a_client_can_initialize_and_sees_no_tools() {
    let input = concat!(
        r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"alpha","version":"1"}}}"#,
        "\n",
        r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#,
        "\n",
        r#"{"jsonrpc":"2.0","id":2,"method":"tools/list"}"#,
        "\n",
        r#"{"jsonrpc":"2.0","id":"three","method":"ping"}"#,
        "\n",
    );

    let (outcome, replies) = exchange(&no_gates(), input.as_bytes()).await;

    assert!(outcome.is_ok());
    assert_eq!(replies.len(), 3);
    assert_eq!(replies[0]["id"], 1);
    assert_eq!(replies[0]["result"]["protocolVersion"], "2025-03-26");
    assert_eq!(replies[0]["result"]["serverInfo"]["name"], "naos");
    assert_eq!(replies[1]["result"]["tools"], json!([]));
    assert_eq!(
        replies[2],
        json!({"jsonrpc": "2.0", "id": "three", "result": {}})
    );
}

#[tokio::test]
async fn an_unknown_protocol_version_gets_the_latest() {
    let input = br#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"1999-01-01"}}
"#;

    let (_, replies) = exchange(&no_gates(), input).await;

    assert_eq!(
        replies[0]["result"]["protocolVersion"],
        PROTOCOL_VERSIONS[0]
    );
}

#[tokio::test]
async fn anything_outside_the_protocol_is_refused() {
    let input = concat!(
        r#"{"jsonrpc":"2.0","id":1,"method":"resources/list"}"#,
        "\n",
        "not json\n",
        r#"[{"jsonrpc":"2.0","id":2,"method":"ping"}]"#,
        "\n",
        r#"{"id":3,"method":"ping"}"#,
        "\n",
        r#"{"jsonrpc":"2.0","id":4}"#,
        "\n",
    );

    let (outcome, replies) = exchange(&no_gates(), input.as_bytes()).await;

    assert!(outcome.is_ok());
    let codes: Vec<(Value, i64)> = replies
        .iter()
        .map(|reply| (reply["id"].clone(), error_code(reply)))
        .collect();
    assert_eq!(
        codes,
        vec![
            (json!(1), -32601),
            (Value::Null, -32700),
            (Value::Null, -32600),
            (Value::Null, -32600),
            (json!(4), -32600),
        ]
    );
}

#[tokio::test]
async fn blank_lines_and_a_final_line_without_newline_are_handled() {
    let input = b"\n\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}";

    let (outcome, replies) = exchange(&no_gates(), input).await;

    assert!(outcome.is_ok());
    assert_eq!(replies.len(), 1);
}

#[tokio::test]
async fn an_oversized_line_ends_the_session() {
    let mut input = vec![b'x'; MAX_LINE + 1];
    input.extend_from_slice(b"\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}\n");

    let (outcome, replies) = exchange(&no_gates(), &input).await;

    assert!(outcome.is_err());
    assert!(replies.is_empty());
}

#[tokio::test]
async fn a_reused_request_id_is_refused() {
    let input = concat!(
        r#"{"jsonrpc":"2.0","id":1,"method":"ping"}"#,
        "\n",
        r#"{"jsonrpc":"2.0","id":1,"method":"ping"}"#,
        "\n",
        r#"{"jsonrpc":"2.0","id":"1","method":"ping"}"#,
        "\n",
    );

    let (outcome, replies) = exchange(&no_gates(), input.as_bytes()).await;

    assert!(outcome.is_ok());
    assert!(replies[0].get("result").is_some());
    assert_eq!(error_code(&replies[1]), -32600);
    assert!(replies[2].get("result").is_some());
}

#[tokio::test]
async fn only_granted_tools_are_listed() {
    let dir = TempDir::new().expect("tempdir");
    let list = b"{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}\n";
    let names = |replies: &[Value]| -> Vec<String> {
        replies[0]["result"]["tools"]
            .as_array()
            .expect("tools")
            .iter()
            .map(|tool| tool["name"].as_str().expect("name").to_owned())
            .collect()
    };

    let (_, shell) = exchange(&shell_gates(&dir, &["grep", "read_file"]), list).await;
    let (_, network) = exchange(&http_gates("127.0.0.1:9".parse().expect("address")), list).await;

    assert_eq!(names(&shell), ["read_file", "grep"]);
    assert_eq!(names(&network), ["http_request"]);
}

#[tokio::test]
async fn granted_shell_tools_answer() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("notes.txt"), "alpha\nbeta\n").expect("write");
    let gates = shell_gates(&dir, &["read_file", "list_dir", "grep"]);
    let input = [
        call(
            1,
            "read_file",
            json!({"path": format!("{GUEST}/notes.txt")}),
        ),
        call(2, "list_dir", json!({"path": GUEST})),
        call(3, "grep", json!({"path": GUEST, "pattern": "beta"})),
    ]
    .concat();

    let (outcome, replies) = exchange(&gates, input.as_bytes()).await;

    assert!(outcome.is_ok());
    assert_eq!(tool_text(&replies[0]), (false, "alpha\nbeta\n".to_owned()));
    let (failed, entries) = tool_text(&replies[1]);
    assert!(!failed);
    assert_eq!(
        serde_json::from_str::<Value>(&entries).expect("json"),
        json!([{"name": "notes.txt", "kind": "file", "size": 11}])
    );
    let (failed, matches) = tool_text(&replies[2]);
    assert!(!failed);
    assert_eq!(
        serde_json::from_str::<Value>(&matches).expect("json"),
        json!([{"path": format!("{GUEST}/notes.txt"), "line": 2, "text": "beta"}])
    );
}

#[tokio::test]
async fn a_gate_refusal_is_a_tool_error() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("notes.txt"), "alpha").expect("write");
    fs::write(dir.path().join("blob.bin"), [0xff, 0xfe, 0x00]).expect("write");
    let gates = shell_gates(&dir, &["read_file"]);
    let input = [
        call(1, "list_dir", json!({"path": GUEST})),
        call(2, "read_file", json!({"path": "/etc/hostname"})),
        call(
            3,
            "read_file",
            json!({"path": format!("{GUEST}/../notes.txt")}),
        ),
        call(4, "read_file", json!({"path": format!("{GUEST}/blob.bin")})),
        call(
            5,
            "http_request",
            json!({"method": "GET", "url": "https://192.0.2.1/"}),
        ),
    ]
    .concat();

    let (outcome, replies) = exchange(&gates, input.as_bytes()).await;

    assert!(outcome.is_ok());
    assert_eq!(replies.len(), 5);
    for reply in &replies {
        let (failed, text) = tool_text(reply);
        assert!(failed, "{reply}");
        assert!(
            !text.contains(dir.path().to_str().expect("utf-8")),
            "{text}"
        );
    }
}

#[tokio::test]
async fn a_malformed_call_is_invalid_params() {
    let dir = TempDir::new().expect("tempdir");
    let gates = shell_gates(&dir, &["read_file"]);
    let input = [
        call(1, "beta", json!({})),
        call(2, "read_file", json!({})),
        call(3, "read_file", json!({"path": GUEST, "follow": true})),
        call(4, "read_file", json!({"path": 7})),
        "{\"jsonrpc\":\"2.0\",\"id\":5,\"method\":\"tools/call\"}\n".to_owned(),
    ]
    .concat();

    let (outcome, replies) = exchange(&gates, input.as_bytes()).await;

    assert!(outcome.is_ok());
    let codes: Vec<i64> = replies.iter().map(error_code).collect();
    assert_eq!(codes, [-32602; 5]);
}

#[tokio::test]
async fn an_allowed_request_is_answered() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/ok"))
        .respond_with(ResponseTemplate::new(201).set_body_string("beta"))
        .mount(&server)
        .await;
    let address = *server.address();
    let url = format!("http://example.com:{}/ok", address.port());

    let (_, replies) = exchange(
        &http_gates(address),
        call(
            1,
            "http_request",
            json!({"method": "post", "url": url, "body": "alpha"}),
        )
        .as_bytes(),
    )
    .await;

    let (failed, text) = tool_text(&replies[0]);
    assert!(!failed, "{text}");
    let response: Value = serde_json::from_str(&text).expect("json");
    assert_eq!(response["status"], 201);
    assert_eq!(response["body"], "beta");
}

#[tokio::test]
async fn a_request_the_policy_does_not_allow_never_leaves() {
    let server = MockServer::start().await;
    let address = *server.address();
    let port = address.port();
    let input = [
        call(1, "http_request", json!({"method": "GET", "url": format!("http://beta.example.com:{port}/")})),
        call(2, "http_request", json!({"method": "GET", "url": format!("http://example.com:{port}/"), "headers": {"Host": "beta.example.com"}})),
        call(3, "http_request", json!({"method": "GET", "url": format!("http://example.com:{port}/"), "headers": {"Proxy-Authorization": "alpha"}})),
        call(4, "http_request", json!({"method": "POST", "url": format!("http://example.com:{port}/"), "headers": {"Transfer-Encoding": "chunked"}})),
        call(5, "http_request", json!({"method": "CONNECT", "url": format!("http://example.com:{port}/")})),
    ]
    .concat();

    let (outcome, replies) = exchange(&http_gates(address), input.as_bytes()).await;

    assert!(outcome.is_ok());
    assert!(tool_text(&replies[0]).0);
    let codes: Vec<i64> = replies[1..].iter().map(error_code).collect();
    assert_eq!(codes, [-32602; 4]);
    assert!(server
        .received_requests()
        .await
        .expect("recording")
        .is_empty());
}

#[tokio::test]
async fn a_slow_destination_times_out() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(200).set_delay(Duration::from_secs(5)))
        .mount(&server)
        .await;
    let address = *server.address();
    let url = format!("http://example.com:{}/", address.port());

    let (outcome, replies) = exchange_within(
        &http_gates(address),
        Duration::from_millis(200),
        call(1, "http_request", json!({"method": "GET", "url": url})).as_bytes(),
    )
    .await;

    assert!(outcome.is_ok());
    assert_eq!(tool_text(&replies[0]), (true, "call timed out".to_owned()));
}

#[tokio::test]
async fn an_unreachable_destination_is_a_tool_error() {
    // wiremock pools its servers and a dropped one keeps listening, so a closed port comes from a bare listener.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    drop(listener);
    let url = format!("http://example.com:{}/", address.port());

    let (outcome, replies) = exchange(
        &http_gates(address),
        call(1, "http_request", json!({"method": "GET", "url": url})).as_bytes(),
    )
    .await;

    assert!(outcome.is_ok());
    assert!(tool_text(&replies[0]).0);
}

type Handler = fn(&str, &Value) -> Value;

/// A Streamable HTTP server that opens a session and answers every request through `handler`.
async fn upstream(handler: Handler, events: bool) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/mcp"))
        .respond_with(move |request: &Request| {
            let message: Value = serde_json::from_slice(&request.body).expect("json");
            let Some(id) = message.get("id").cloned() else {
                return ResponseTemplate::new(202);
            };
            let method = message["method"].as_str().expect("method");
            let mut reply = if method == "initialize" {
                json!({"result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "alpha", "version": "1"}}})
            } else {
                handler(method, &message["params"])
            };
            reply["jsonrpc"] = json!("2.0");
            reply["id"] = id;
            let template = ResponseTemplate::new(200).insert_header("mcp-session-id", "session-alpha");
            if events {
                template.set_body_raw(format!("event: message\ndata: {reply}\n\n"), "text/event-stream")
            } else {
                template.set_body_json(reply)
            }
        })
        .mount(&server)
        .await;
    server
}

fn answers(method: &str, params: &Value) -> Value {
    match method {
        "tools/list" => json!({"result": {"tools": [
            {"name": "search", "inputSchema": {"type": "object"}},
            {"name": "delete", "inputSchema": {"type": "object"}},
        ]}}),
        "tools/call" if params["arguments"]["echo"] == true => {
            json!({"result": {"content": [{"type": "text", "text": format!("token {SECRET}")}], "isError": false}})
        }
        "tools/call" if params["arguments"]["fail"] == true => {
            json!({"error": {"code": -32000, "message": format!("rejected {SECRET}")}})
        }
        "tools/call" => {
            json!({"result": {"content": [{"type": "text", "text": "found"}], "isError": false}})
        }
        "resources/list" => json!({"result": {"resources": [
            {"uri": "docs://alpha/guide", "name": "guide"},
            {"uri": "docs://beta/private", "name": "private"},
        ]}}),
        "resources/read" => {
            json!({"result": {"contents": [{"uri": params["uri"], "text": "guide"}]}})
        }
        _ => json!({"error": {"code": -32601, "message": "method not found"}}),
    }
}

fn upstream_gates(address: SocketAddr, server: Value) -> RunGates {
    let rules = json!([
        {"server": "alpha", "tool": "search", "effect": "allow"},
        {"server": "alpha", "resource": "docs://alpha/", "effect": "allow"},
    ]);
    ruled_gates(address, server, rules)
}

fn ruled_gates(address: SocketAddr, server: Value, rules: Value) -> RunGates {
    let mut document = json!({
        "name": "alpha",
        "url": format!("http://example.com:{}/mcp", address.port()),
        "credential": "alpha-token",
        "timeout_seconds": 30,
        "max_calls_per_minute": 60,
    });
    for (key, value) in server.as_object().expect("object") {
        document[key] = value.clone();
    }
    let gates = RunGates {
        network: NetworkGate::from_snapshot("run_a", None).expect("policy"),
        shell: no_shell(),
        mcp: McpGate::local(
            &json!({ "servers": [document], "rules": rules }),
            vec![address.ip()],
        )
        .expect("policy"),
        model: no_model(),
    };
    grant(&gates, u64::MAX);
    gates
}

fn issued(name: &str, value: &str, expires_at: u64) -> BTreeMap<String, RunCredential> {
    BTreeMap::from([(
        name.to_owned(),
        RunCredential {
            value: value.into(),
            expires_at,
        },
    )])
}

fn grant(gates: &RunGates, expires_at: u64) {
    gates
        .mcp
        .refresh(&issued("alpha-token", SECRET, expires_at), &BTreeMap::new());
}

fn request(id: u64, method: &str, params: Value) -> String {
    format!(
        "{}\n",
        json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params})
    )
}

async fn methods(server: &MockServer) -> Vec<String> {
    server
        .received_requests()
        .await
        .expect("recording")
        .iter()
        .map(|request| {
            let message: Value = serde_json::from_slice(&request.body).expect("json");
            message["method"].as_str().expect("method").to_owned()
        })
        .collect()
}

#[tokio::test]
async fn an_allowed_upstream_tool_is_listed_and_answers() {
    for events in [false, true] {
        let server = upstream(answers, events).await;
        let input = [
            request(1, "tools/list", json!({})),
            call(2, "alpha__search", json!({"query": "beta"})),
        ]
        .concat();

        let (outcome, replies) = exchange(
            &upstream_gates(*server.address(), json!({})),
            input.as_bytes(),
        )
        .await;

        assert!(outcome.is_ok());
        let names: Vec<&str> = replies[0]["result"]["tools"]
            .as_array()
            .expect("tools")
            .iter()
            .map(|tool| tool["name"].as_str().expect("name"))
            .collect();
        assert_eq!(names, ["alpha__search"]);
        assert_eq!(tool_text(&replies[1]), (false, "found".to_owned()));
        let received = server.received_requests().await.expect("recording");
        assert!(
            received
                .iter()
                .all(|request| request.headers["authorization"]
                    == format!("Bearer {SECRET}").as_str())
        );
        assert!(received[2..]
            .iter()
            .all(|request| request.headers["mcp-session-id"] == "session-alpha"));
        assert_eq!(
            methods(&server).await,
            [
                "initialize",
                "notifications/initialized",
                "tools/list",
                "tools/call"
            ]
        );
    }
}

#[tokio::test]
async fn a_tool_or_server_outside_the_policy_never_reaches_upstream() {
    let server = upstream(answers, false).await;
    let input = [
        call(1, "alpha__delete", json!({})),
        call(2, "beta__search", json!({})),
        call(3, "alpha__search", json!("not an object")),
    ]
    .concat();

    let (outcome, replies) = exchange(
        &upstream_gates(*server.address(), json!({})),
        input.as_bytes(),
    )
    .await;

    assert!(outcome.is_ok());
    assert_eq!(
        tool_text(&replies[0]),
        (true, "no rule allows this call".to_owned())
    );
    assert_eq!(error_code(&replies[1]), -32602);
    assert_eq!(error_code(&replies[2]), -32602);
    assert!(methods(&server).await.is_empty());
}

#[tokio::test]
async fn a_missing_or_expired_credential_denies_without_a_request() {
    let server = upstream(answers, false).await;
    let gates = upstream_gates(*server.address(), json!({}));
    gates.mcp.refresh(&BTreeMap::new(), &BTreeMap::new());

    let (_, missing) = exchange(&gates, call(1, "alpha__search", json!({})).as_bytes()).await;
    grant(&gates, 1);
    let (_, expired) = exchange(&gates, call(1, "alpha__search", json!({})).as_bytes()).await;

    assert_eq!(
        tool_text(&missing[0]),
        (true, "credential unavailable".to_owned())
    );
    assert_eq!(
        tool_text(&expired[0]),
        (true, "credential expired".to_owned())
    );
    assert!(methods(&server).await.is_empty());
}

#[tokio::test]
async fn an_echoed_credential_is_redacted() {
    let server = upstream(answers, false).await;
    let input = [
        call(1, "alpha__search", json!({"echo": true})),
        call(2, "alpha__search", json!({"fail": true})),
    ]
    .concat();

    let (_, replies) = exchange(
        &upstream_gates(*server.address(), json!({})),
        input.as_bytes(),
    )
    .await;

    assert_eq!(
        tool_text(&replies[0]),
        (false, "token <redacted>".to_owned())
    );
    assert_eq!(
        tool_text(&replies[1]),
        (true, "server error: rejected <redacted>".to_owned())
    );
    assert!(!replies
        .iter()
        .any(|reply| reply.to_string().contains(SECRET)));
}

#[tokio::test]
async fn resources_are_filtered_and_read_by_prefix() {
    let server = upstream(answers, false).await;
    let input = [
        request(1, "initialize", json!({"protocolVersion": "2025-06-18"})),
        request(2, "resources/list", json!({})),
        request(3, "resources/read", json!({"uri": "docs://alpha/guide"})),
        request(4, "resources/read", json!({"uri": "docs://beta/private"})),
        request(5, "resources/read", json!({})),
    ]
    .concat();

    let (outcome, replies) = exchange(
        &upstream_gates(*server.address(), json!({})),
        input.as_bytes(),
    )
    .await;

    assert!(outcome.is_ok());
    assert!(replies[0]["result"]["capabilities"]["resources"].is_object());
    assert_eq!(
        replies[1]["result"]["resources"],
        json!([{"uri": "docs://alpha/guide", "name": "guide"}])
    );
    assert_eq!(replies[2]["result"]["contents"][0]["text"], "guide");
    assert_eq!(error_code(&replies[3]), -32002);
    assert_eq!(error_code(&replies[4]), -32602);
    assert_eq!(
        methods(&server).await,
        [
            "initialize",
            "notifications/initialized",
            "resources/list",
            "resources/read"
        ]
    );
}

#[tokio::test]
async fn resources_are_not_served_without_a_resource_policy() {
    let (_, replies) = exchange(
        &no_gates(),
        request(1, "resources/list", json!({})).as_bytes(),
    )
    .await;

    assert_eq!(error_code(&replies[0]), -32601);
}

#[tokio::test]
async fn provider_failures_are_tool_errors() {
    let server = upstream(answers, false).await;
    Mock::given(body_partial_json(json!({"method": "tools/call"})))
        .respond_with(ResponseTemplate::new(500))
        .with_priority(1)
        .mount(&server)
        .await;
    let input = [
        request(1, "tools/list", json!({})),
        call(2, "alpha__search", json!({})),
    ]
    .concat();

    let (outcome, replies) = exchange(
        &upstream_gates(*server.address(), json!({})),
        input.as_bytes(),
    )
    .await;

    assert!(outcome.is_ok());
    assert_eq!(
        replies[0]["result"]["tools"]
            .as_array()
            .expect("tools")
            .len(),
        1
    );
    assert_eq!(
        tool_text(&replies[1]),
        (true, "server answered 500".to_owned())
    );
}

#[tokio::test]
async fn an_unreachable_upstream_is_left_out_of_the_listing() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    drop(listener);

    let (outcome, replies) = exchange(
        &upstream_gates(address, json!({})),
        request(1, "tools/list", json!({})).as_bytes(),
    )
    .await;

    assert!(outcome.is_ok());
    assert_eq!(replies[0]["result"]["tools"], json!([]));
}

#[tokio::test]
async fn a_slow_upstream_times_out() {
    let server = upstream(answers, false).await;
    Mock::given(body_partial_json(json!({"method": "tools/call"})))
        .respond_with(ResponseTemplate::new(200).set_delay(Duration::from_secs(5)))
        .with_priority(1)
        .mount(&server)
        .await;

    let (outcome, replies) = exchange(
        &upstream_gates(*server.address(), json!({"timeout_seconds": 1})),
        call(1, "alpha__search", json!({})).as_bytes(),
    )
    .await;

    assert!(outcome.is_ok());
    assert_eq!(tool_text(&replies[0]), (true, "call timed out".to_owned()));
}

#[tokio::test]
async fn the_call_budget_is_enforced() {
    let server = upstream(answers, false).await;
    let input = [
        call(1, "alpha__search", json!({})),
        call(2, "alpha__search", json!({})),
    ]
    .concat();

    let (_, replies) = exchange(
        &upstream_gates(*server.address(), json!({"max_calls_per_minute": 1})),
        input.as_bytes(),
    )
    .await;

    assert!(!tool_text(&replies[0]).0);
    assert_eq!(tool_text(&replies[1]), (true, "rate limit".to_owned()));
}

#[tokio::test]
async fn an_expired_session_is_opened_again() {
    let server = upstream(answers, false).await;
    Mock::given(body_partial_json(json!({"method": "tools/call"})))
        .respond_with(ResponseTemplate::new(404))
        .up_to_n_times(1)
        .with_priority(1)
        .mount(&server)
        .await;

    let (_, replies) = exchange(
        &upstream_gates(*server.address(), json!({})),
        call(1, "alpha__search", json!({})).as_bytes(),
    )
    .await;

    assert_eq!(tool_text(&replies[0]), (false, "found".to_owned()));
    assert_eq!(
        methods(&server).await,
        [
            "initialize",
            "notifications/initialized",
            "tools/call",
            "initialize",
            "notifications/initialized",
            "tools/call"
        ]
    );
}

#[tokio::test]
async fn a_run_without_rules_lists_and_calls_nothing() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("a.txt"), "alpha").expect("write");
    let mut gates = shell_gates(&dir, &["read_file"]);
    gates.mcp = no_mcp();
    let input = [
        request(1, "tools/list", json!({})),
        call(2, "read_file", json!({"path": format!("{GUEST}/a.txt")})),
    ]
    .concat();

    let (_, replies) = exchange(&gates, input.as_bytes()).await;

    assert_eq!(replies[0]["result"]["tools"], json!([]));
    assert_eq!(
        tool_text(&replies[1]),
        (true, "no rule allows this call".to_owned())
    );
}

#[tokio::test]
async fn rules_hold_for_a_built_in_tool() {
    let dir = TempDir::new().expect("tempdir");
    fs::create_dir(dir.path().join("src")).expect("mkdir");
    fs::write(dir.path().join("src/a.txt"), "alpha").expect("write");
    fs::write(dir.path().join("b.txt"), "beta").expect("write");
    let mut gates = shell_gates(&dir, &["read_file", "list_dir", "grep"]);
    gates.mcp = ruled_mcp(json!([
        {"server": "shell", "tool": "*", "effect": "allow",
         "arguments": {"path": {"prefix": format!("{GUEST}/src")}}},
        {"server": "shell", "tool": "grep", "effect": "deny"},
        {"server": "shell", "tool": "read_file", "effect": "deny",
         "arguments": {"path": {"regex": ".*\\.key"}}},
    ]));
    let input = [
        request(1, "tools/list", json!({})),
        call(
            2,
            "read_file",
            json!({"path": format!("{GUEST}/src/a.txt")}),
        ),
        call(3, "read_file", json!({"path": format!("{GUEST}/b.txt")})),
        call(
            4,
            "read_file",
            json!({"path": format!("{GUEST}/src/a.key")}),
        ),
        call(
            5,
            "grep",
            json!({"path": format!("{GUEST}/src"), "pattern": "alpha"}),
        ),
        call(6, "list_dir", json!({"path": format!("{GUEST}/src")})),
    ]
    .concat();

    let (_, replies) = exchange(&gates, input.as_bytes()).await;

    let listed: Vec<&str> = replies[0]["result"]["tools"]
        .as_array()
        .expect("tools")
        .iter()
        .map(|tool| tool["name"].as_str().expect("name"))
        .collect();
    assert_eq!(listed, ["read_file", "list_dir"]);
    assert_eq!(tool_text(&replies[1]), (false, "alpha".to_owned()));
    assert_eq!(
        tool_text(&replies[2]),
        (true, "no rule allows this call".to_owned())
    );
    assert_eq!(
        tool_text(&replies[3]),
        (true, "denied by rule 2".to_owned())
    );
    assert_eq!(
        tool_text(&replies[4]),
        (true, "denied by rule 1".to_owned())
    );
    assert!(!tool_text(&replies[5]).0);
}

#[tokio::test]
async fn a_rule_never_lifts_what_the_gate_refuses() {
    let dir = TempDir::new().expect("tempdir");
    let gates = shell_gates(&dir, &["read_file"]);
    let input = call(1, "grep", json!({"path": GUEST, "pattern": "alpha"}));

    let (_, replies) = exchange(&gates, input.as_bytes()).await;

    assert!(tool_text(&replies[0]).0);
}

#[tokio::test]
async fn rules_hold_for_an_upstream_tool() {
    let server = upstream(answers, false).await;
    let rules = json!([
        {"server": "alpha", "tool": "*", "effect": "allow"},
        {"server": "alpha", "tool": "delete", "effect": "deny"},
        {"server": "alpha", "tool": "search", "effect": "deny",
         "arguments": {"scope": {"equals": "admin"}}},
        {"server": "alpha", "tool": "search", "effect": "deny",
         "arguments": {"limit": {"schema": {"type": "integer", "maximum": 0}}}},
    ]);
    let input = [
        call(1, "alpha__search", json!({"query": "naos"})),
        call(2, "alpha__delete", json!({})),
        call(3, "alpha__search", json!({"scope": "admin"})),
        call(4, "alpha__search", json!({"limit": 0})),
        call(5, "alpha__search", json!({"limit": 5, "scope": "user"})),
    ]
    .concat();

    let (_, replies) = exchange(
        &ruled_gates(*server.address(), json!({}), rules),
        input.as_bytes(),
    )
    .await;

    assert!(!tool_text(&replies[0]).0);
    assert_eq!(
        tool_text(&replies[1]),
        (true, "denied by rule 1".to_owned())
    );
    assert_eq!(
        tool_text(&replies[2]),
        (true, "denied by rule 2".to_owned())
    );
    assert_eq!(
        tool_text(&replies[3]),
        (true, "denied by rule 3".to_owned())
    );
    assert!(!tool_text(&replies[4]).0);
    let calls = methods(&server).await;
    assert_eq!(calls.iter().filter(|name| *name == "tools/call").count(), 2);
}

#[tokio::test]
async fn a_rule_budget_is_spent_for_the_run() {
    let server = upstream(answers, false).await;
    let rules = json!([
        {"server": "alpha", "tool": "search", "effect": "allow", "max_calls": 1,
         "arguments": {"query": {"equals": "naos"}}},
        {"server": "alpha", "tool": "search", "effect": "allow", "max_calls_per_minute": 1},
    ]);
    let gates = ruled_gates(*server.address(), json!({}), rules);
    let first = [
        call(1, "alpha__search", json!({"query": "naos"})),
        call(2, "alpha__search", json!({"query": "naos"})),
    ]
    .concat();

    let (_, replies) = exchange(&gates, first.as_bytes()).await;
    let (_, later) = exchange(
        &gates,
        call(1, "alpha__search", json!({"query": "naos"})).as_bytes(),
    )
    .await;

    assert!(!tool_text(&replies[0]).0);
    assert!(!tool_text(&replies[1]).0);
    assert_eq!(
        tool_text(&later[0]),
        (true, "budget of rule 0 is spent".to_owned())
    );
}

#[tokio::test]
async fn a_denied_resource_prefix_is_not_listed_or_read() {
    let server = upstream(answers, false).await;
    let rules = json!([
        {"server": "alpha", "resource": "docs://", "effect": "allow"},
        {"server": "alpha", "resource": "docs://beta/", "effect": "deny"},
    ]);
    let input = [
        request(1, "resources/list", json!({})),
        request(2, "resources/read", json!({"uri": "docs://beta/private"})),
        request(3, "resources/read", json!({"uri": "docs://alpha/guide"})),
    ]
    .concat();

    let (_, replies) = exchange(
        &ruled_gates(*server.address(), json!({}), rules),
        input.as_bytes(),
    )
    .await;

    assert_eq!(
        replies[0]["result"]["resources"],
        json!([{"uri": "docs://alpha/guide", "name": "guide"}])
    );
    assert_eq!(error_code(&replies[1]), -32002);
    assert_eq!(replies[2]["result"]["contents"][0]["text"], "guide");
}

fn secret_gates(expires_at: u64) -> RunGates {
    let document = json!({
        "servers": [],
        "secrets": ["agent-key", "late-key"],
        "rules": [
            {"server": "secrets", "tool": "list", "effect": "allow"},
            {"server": "secrets", "tool": "get", "effect": "allow", "max_calls": 2,
             "arguments": {"name": {"equals": "agent-key"}}},
            {"server": "secrets", "tool": "get", "effect": "allow",
             "arguments": {"name": {"equals": "late-key"}}},
        ],
    });
    let mut gates = no_gates();
    gates.mcp = McpGate::from_snapshot("run_a", Some(&document)).expect("policy");
    let mut secrets = issued("agent-key", "agent-value", u64::MAX);
    secrets.extend(issued("late-key", "late-value", expires_at));
    secrets.extend(issued("alpha-token", SECRET, u64::MAX));
    gates
        .mcp
        .refresh(&issued("alpha-token", SECRET, u64::MAX), &secrets);
    gates
}

#[tokio::test]
async fn a_granted_secret_is_listed_by_name_and_read_by_value() {
    let gates = secret_gates(u64::MAX);
    let input = [
        request(1, "tools/list", json!({})),
        call(2, "secrets__list", json!({})),
        call(3, "secrets__get", json!({"name": "agent-key"})),
        call(4, "secrets__get", json!({"name": "late-key"})),
    ]
    .concat();

    let (_, replies) = exchange(&gates, input.as_bytes()).await;

    let listed: Vec<&str> = replies[0]["result"]["tools"]
        .as_array()
        .expect("tools")
        .iter()
        .map(|tool| tool["name"].as_str().expect("name"))
        .collect();
    assert_eq!(listed, ["secrets__list", "secrets__get"]);
    let names = tool_text(&replies[1]);
    assert_eq!(names, (false, r#"["agent-key","late-key"]"#.to_owned()));
    assert_eq!(tool_text(&replies[2]), (false, "agent-value".to_owned()));
    assert_eq!(tool_text(&replies[3]), (false, "late-value".to_owned()));
}

#[tokio::test]
async fn a_secret_outside_the_grant_is_never_returned() {
    let gates = secret_gates(0);
    let input = [
        call(1, "secrets__get", json!({"name": "alpha-token"})),
        call(2, "secrets__get", json!({"name": "other-key"})),
        call(3, "secrets__get", json!({"name": "late-key"})),
        call(4, "secrets__get", json!({})),
        call(5, "secrets__get", json!({"name": "agent-key", "raw": true})),
        call(6, "secrets__list", json!({"all": true})),
        call(7, "secrets__set", json!({"name": "agent-key"})),
        call(8, "secrets__get", json!({"name": "agent-key"})),
        call(9, "secrets__get", json!({"name": "agent-key"})),
        call(10, "secrets__get", json!({"name": "agent-key"})),
    ]
    .concat();

    let (_, replies) = exchange(&gates, input.as_bytes()).await;

    let refused = (true, "no rule allows this call".to_owned());
    assert_eq!(tool_text(&replies[0]), refused);
    assert_eq!(tool_text(&replies[1]), refused);
    assert_eq!(
        tool_text(&replies[2]),
        (true, "secret is not available".to_owned())
    );
    for reply in &replies[3..7] {
        assert_eq!(error_code(reply), -32602);
    }
    assert!(!tool_text(&replies[7]).0 && !tool_text(&replies[8]).0);
    assert_eq!(
        tool_text(&replies[9]),
        (true, "budget of rule 1 is spent".to_owned())
    );
    for reply in &replies {
        assert!(!reply.to_string().contains(SECRET), "{reply}");
    }
}

#[tokio::test]
async fn a_secret_the_api_did_not_issue_is_not_available() {
    let gates = secret_gates(u64::MAX);
    gates.mcp.refresh(&BTreeMap::new(), &BTreeMap::new());

    let (_, replies) = exchange(
        &gates,
        call(1, "secrets__get", json!({"name": "agent-key"})).as_bytes(),
    )
    .await;

    assert_eq!(
        tool_text(&replies[0]),
        (true, "secret is not available".to_owned())
    );
}

#[test]
fn a_guest_string_is_logged_only_when_it_is_a_secret_name() {
    assert_eq!(logged_name("agent-key.v2_a"), "agent-key.v2_a");
    for name in ["", "Agent", "-key", "a key", "a\"b", &"a".repeat(65)] {
        assert_eq!(logged_name(name), "invalid");
    }
}

/// The guest end of one live session, so a test can act between two requests.
struct Wire {
    lines: tokio::io::Lines<BufReader<tokio::io::ReadHalf<tokio::io::DuplexStream>>>,
    write: tokio::io::WriteHalf<tokio::io::DuplexStream>,
}

impl Wire {
    async fn send(&mut self, line: &str) {
        self.write.write_all(line.as_bytes()).await.expect("send");
    }

    async fn next(&mut self) -> Value {
        let line = self.lines.next_line().await.expect("read").expect("line");
        serde_json::from_str(&line).expect("json")
    }

    async fn ask(&mut self, line: &str) -> Value {
        self.send(line).await;
        self.next().await
    }
}

async fn live<F: Future<Output = ()>>(gates: &RunGates, drive: impl FnOnce(Wire) -> F) {
    let (guest, host) = tokio::io::duplex(MAX_LINE);
    let (read, write) = tokio::io::split(host);
    let (lines, guest) = tokio::io::split(guest);
    let wire = Wire {
        lines: BufReader::new(lines).lines(),
        write: guest,
    };
    let mut session = Session::new("run_a", gates, CALL_TIMEOUT);
    tokio::select! {
        outcome = session.run(read, write) => panic!("the session ended: {outcome:?}"),
        () = drive(wire) => {}
    }
}

fn alpha_document(address: SocketAddr, rules: Value) -> Value {
    json!({
        "servers": [{
            "name": "alpha",
            "url": format!("http://example.com:{}/mcp", address.port()),
            "credential": "alpha-token",
            "timeout_seconds": 30,
            "max_calls_per_minute": 60,
        }],
        "rules": rules,
    })
}

fn alpha_gates(address: SocketAddr, rules: Value) -> RunGates {
    let gates = RunGates {
        mcp: McpGate::local(&alpha_document(address, rules), vec![address.ip()]).expect("policy"),
        ..no_gates()
    };
    grant(&gates, u64::MAX);
    gates
}

const INITIALIZE: &str = "{\"jsonrpc\":\"2.0\",\"id\":0,\"method\":\"initialize\",\"params\":{}}\n";

#[tokio::test]
async fn a_replaced_policy_is_announced_and_decides_the_next_call() {
    let list = json!([{"server": "secrets", "tool": "list", "effect": "allow"}]);
    let gates = RunGates {
        mcp: ruled_mcp(list),
        ..no_gates()
    };
    let gates = &gates;
    let other =
        json!({"servers": [], "rules": [{"server": "network", "tool": "*", "effect": "allow"}]});

    live(gates, |mut wire| async move {
        let started = wire.ask(INITIALIZE).await;
        assert_eq!(
            started["result"]["capabilities"],
            json!({"tools": {"listChanged": true}})
        );
        assert!(!tool_text(&wire.ask(&call(1, "secrets__list", json!({}))).await).0);

        gates.mcp.replace(Some(&other));
        assert_eq!(
            wire.next().await,
            json!({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        );
        assert_eq!(
            tool_text(&wire.ask(&call(2, "secrets__list", json!({}))).await),
            (true, rules::NO_RULE.to_owned())
        );

        gates.mcp.replace(Some(&other));
        gates.mcp.replace(Some(&json!({"rules": "alpha"})));
        assert_eq!(
            wire.next().await["method"],
            "notifications/tools/list_changed"
        );
        let listed = wire.ask(&request(3, "tools/list", json!({}))).await;
        assert_eq!(listed["result"]["tools"], json!([]));
    })
    .await;
}

#[tokio::test]
async fn a_replaced_shell_policy_is_announced_and_decides_the_next_call() {
    let dir = TempDir::new().expect("tempdir");
    fs::write(dir.path().join("notes.txt"), "alpha\n").expect("write");
    let gates = shell_gates(&dir, &["read_file"]);
    let gates = &gates;
    let read = |id| {
        call(
            id,
            "read_file",
            json!({"path": format!("{GUEST}/notes.txt")}),
        )
    };

    live(gates, |mut wire| async move {
        wire.ask(INITIALIZE).await;
        assert!(!tool_text(&wire.ask(&read(1)).await).0);

        gates.shell.replace(Some(&json!({ "allow": ["list_dir"] })));
        assert_eq!(
            wire.next().await,
            json!({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        );
        let listed = wire.ask(&request(2, "tools/list", json!({}))).await;
        assert_eq!(listed["result"]["tools"][0]["name"], "list_dir");
        assert_eq!(listed["result"]["tools"].as_array().map(Vec::len), Some(1));
        assert!(tool_text(&wire.ask(&read(3)).await).0);
        assert!(!tool_text(&wire.ask(&call(4, "list_dir", json!({"path": GUEST}))).await).0);
    })
    .await;
}

#[tokio::test]
async fn a_replaced_network_policy_is_announced() {
    let gates = http_gates("127.0.0.1:9".parse().expect("address"));
    let gates = &gates;

    live(gates, |mut wire| async move {
        wire.ask(INITIALIZE).await;
        let listed = wire.ask(&request(1, "tools/list", json!({}))).await;
        assert_eq!(listed["result"]["tools"][0]["name"], "http_request");

        let closed = json!({"deny": [{"host": "example.com"}]});
        gates.network.replace(Some(&closed));
        assert_eq!(
            wire.next().await["method"],
            "notifications/tools/list_changed"
        );
        let listed = wire.ask(&request(2, "tools/list", json!({}))).await;
        assert_eq!(listed["result"]["tools"], json!([]));
    })
    .await;
}

#[tokio::test]
async fn a_call_in_flight_ends_under_the_policy_it_started_with() {
    let server = upstream(answers, false).await;
    Mock::given(body_partial_json(json!({"method": "tools/call"})))
        .respond_with(move |request: &Request| {
            let message: Value = serde_json::from_slice(&request.body).expect("json");
            let reply = json!({"jsonrpc": "2.0", "id": message["id"], "result": {
                "content": [{"type": "text", "text": "found"}], "isError": false}});
            ResponseTemplate::new(200)
                .set_delay(Duration::from_millis(500))
                .set_body_json(reply)
        })
        .with_priority(1)
        .mount(&server)
        .await;
    let rules = json!([
        {"server": "alpha", "tool": "search", "effect": "allow"},
        {"server": "alpha", "resource": "docs://alpha/", "effect": "allow"},
    ]);
    let gates = alpha_gates(*server.address(), rules);
    let gates = &gates;

    live(gates, |mut wire| async move {
        wire.ask(INITIALIZE).await;
        wire.send(&call(1, "alpha__search", json!({}))).await;
        tokio::time::sleep(Duration::from_millis(100)).await;
        gates.mcp.replace(Some(&json!({
            "servers": [],
            "rules": [{"server": "alpha", "tool": "search", "effect": "allow"}],
        })));
        gates.mcp.refresh(&BTreeMap::new(), &BTreeMap::new());

        assert_eq!(tool_text(&wire.next().await), (false, "found".to_owned()));
        assert_eq!(
            wire.next().await["method"],
            "notifications/tools/list_changed"
        );
        assert_eq!(
            wire.next().await["method"],
            "notifications/resources/list_changed"
        );
        let refused = wire.ask(&call(2, "alpha__search", json!({}))).await;
        assert_eq!(error_code(&refused), INVALID_PARAMS);
        let read = wire
            .ask(&request(
                3,
                "resources/read",
                json!({"uri": "docs://alpha/guide"}),
            ))
            .await;
        assert_eq!(error_code(&read), METHOD_NOT_FOUND);
    })
    .await;

    assert_eq!(gates.mcp.credential_value("alpha-token"), None);
    tokio::time::sleep(Duration::from_millis(200)).await;
    let received = server.received_requests().await.expect("recording");
    let calls = |method: &str| {
        received
            .iter()
            .filter(|request| request.method.as_str() == method)
            .count()
    };
    assert_eq!((calls("POST"), calls("DELETE")), (3, 1));
    let closed = received.last().expect("request");
    assert_eq!(closed.headers["mcp-session-id"], "session-alpha");
}

#[tokio::test]
async fn what_a_new_policy_keeps_stays_spent_and_open() {
    let server = upstream(answers, false).await;
    let kept = json!({"server": "alpha", "tool": "search", "effect": "allow", "max_calls": 1});
    let gates = alpha_gates(*server.address(), json!([kept]));
    let search = call(1, "alpha__search", json!({}));
    let wider = json!([
        {"server": "alpha", "tool": "delete", "effect": "allow", "max_calls": 1},
        kept,
    ]);

    let (_, first) = exchange(&gates, search.as_bytes()).await;
    gates
        .mcp
        .replace(Some(&alpha_document(*server.address(), wider)));
    let input = [search, call(2, "alpha__delete", json!({}))].concat();
    let (_, later) = exchange(&gates, input.as_bytes()).await;

    assert_eq!(tool_text(&first[0]), (false, "found".to_owned()));
    assert_eq!(
        tool_text(&later[0]),
        (true, "budget of rule 1 is spent".to_owned())
    );
    assert_eq!(tool_text(&later[1]), (false, "found".to_owned()));
    let opened = methods(&server).await;
    assert_eq!(
        opened.iter().filter(|name| *name == "initialize").count(),
        1
    );
}

/// A server that gives every `initialize` a session id of its own, so sessions can be told apart.
async fn sessions_upstream() -> MockServer {
    let server = MockServer::start().await;
    let opened = Arc::new(AtomicUsize::new(0));
    Mock::given(method("POST"))
        .and(path("/mcp"))
        .respond_with(move |request: &Request| {
            let message: Value = serde_json::from_slice(&request.body).expect("json");
            let Some(id) = message.get("id").cloned() else {
                return ResponseTemplate::new(202);
            };
            let (result, session) = if message["method"] == "initialize" {
                let number = opened.fetch_add(1, Ordering::SeqCst) + 1;
                let result = json!({"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "alpha", "version": "1"}});
                (result, format!("session-{number}"))
            } else {
                let result = json!({"content": [{"type": "text", "text": "found"}], "isError": false});
                (result, header(request, "mcp-session-id"))
            };
            ResponseTemplate::new(200)
                .insert_header("mcp-session-id", session.as_str())
                .set_body_json(json!({"jsonrpc": "2.0", "id": id, "result": result}))
        })
        .mount(&server)
        .await;
    server
}

fn header(request: &Request, name: &str) -> String {
    request.headers[name].to_str().expect("header").to_owned()
}

/// The session id and the Authorization header of every `tools/call` the server received.
async fn calls_seen(server: &MockServer) -> Vec<(String, String)> {
    let received = server.received_requests().await.expect("recording");
    received
        .iter()
        .filter(|request| request.method.as_str() == "POST")
        .filter(|request| {
            let message: Value = serde_json::from_slice(&request.body).expect("json");
            message["method"] == "tools/call"
        })
        .map(|request| {
            (
                header(request, "mcp-session-id"),
                header(request, "authorization"),
            )
        })
        .collect()
}

/// One Run's gates over the shared server, with its own credential value and rule budget.
fn run_gates(run_id: &str, address: SocketAddr, token: &str, max_calls: u64) -> RunGates {
    let rules = json!([
        {"server": "alpha", "tool": "search", "effect": "allow", "max_calls": max_calls},
    ]);
    let document = alpha_document(address, rules);
    let gates = RunGates {
        mcp: McpGate::local_for(run_id, &document, vec![address.ip()]).expect("policy"),
        ..no_gates()
    };
    gates
        .mcp
        .refresh(&issued("alpha-token", token, u64::MAX), &BTreeMap::new());
    gates
}

/// One Run's gates with a grant for each of the named secrets and the values issued to it.
fn keyed_gates(run_id: &str, secrets: &[(&str, &str)]) -> RunGates {
    let names: Vec<&str> = secrets.iter().map(|(name, _)| *name).collect();
    let mut rules = vec![json!({"server": "secrets", "tool": "list", "effect": "allow"})];
    rules.extend(names.iter().map(|name| {
        json!({"server": "secrets", "tool": "get", "effect": "allow",
               "arguments": {"name": {"equals": name}}})
    }));
    let document = json!({ "servers": [], "secrets": names, "rules": rules });
    let mut values = BTreeMap::new();
    for (name, value) in secrets {
        values.extend(issued(name, value, u64::MAX));
    }
    let mut gates = no_gates();
    gates.mcp = McpGate::from_snapshot(run_id, Some(&document)).expect("policy");
    gates.mcp.refresh(&BTreeMap::new(), &values);
    gates
}

#[tokio::test]
async fn two_runs_naming_one_server_hold_a_session_a_credential_and_a_budget_each() {
    let server = sessions_upstream().await;
    let alpha = run_gates("run_a", *server.address(), "token-alpha", 1);
    let beta = run_gates("run_b", *server.address(), "token-beta", 1);
    let search = call(1, "alpha__search", json!({}));
    let twice = [search.clone(), call(2, "alpha__search", json!({}))].concat();

    let (_, first) = exchange(&alpha, twice.as_bytes()).await;
    let (_, second) = exchange(&beta, search.as_bytes()).await;

    assert_eq!(tool_text(&first[0]), (false, "found".to_owned()));
    assert_eq!(
        tool_text(&first[1]),
        (true, "budget of rule 0 is spent".to_owned())
    );
    assert_eq!(tool_text(&second[0]), (false, "found".to_owned()));
    assert_eq!(
        calls_seen(&server).await,
        [
            ("session-1".to_owned(), "Bearer token-alpha".to_owned()),
            ("session-2".to_owned(), "Bearer token-beta".to_owned()),
        ]
    );
}

#[tokio::test]
async fn a_run_reads_only_the_secrets_issued_to_it() {
    let alpha = keyed_gates(
        "run_a",
        &[
            ("shared-key", "value-alpha"),
            ("alpha-only", "private-alpha"),
        ],
    );
    let beta = keyed_gates("run_b", &[("shared-key", "value-beta")]);
    let input = [
        call(1, "secrets__list", json!({})),
        call(2, "secrets__get", json!({"name": "shared-key"})),
        call(3, "secrets__get", json!({"name": "alpha-only"})),
    ]
    .concat();
    let own = call(1, "secrets__get", json!({"name": "shared-key"}));

    let (_, replies) = exchange(&beta, input.as_bytes()).await;
    let (_, kept) = exchange(&alpha, own.as_bytes()).await;

    assert_eq!(
        tool_text(&replies[0]),
        (false, r#"["shared-key"]"#.to_owned())
    );
    assert_eq!(tool_text(&replies[1]), (false, "value-beta".to_owned()));
    assert_eq!(
        tool_text(&replies[2]),
        (true, "no rule allows this call".to_owned())
    );
    assert_eq!(tool_text(&kept[0]), (false, "value-alpha".to_owned()));
}

#[tokio::test]
async fn a_run_id_the_guest_sends_is_never_read() {
    let beta = keyed_gates("run_b", &[("shared-key", "value-beta")]);
    let forged = |id: u64, arguments: Value| {
        let message = json!({
            "jsonrpc": "2.0",
            "id": id,
            "run_id": "run_a",
            "method": "tools/call",
            "params": {
                "name": "secrets__get",
                "arguments": arguments,
                "run_id": "run_a",
                "_meta": { "run_id": "run_a" },
            },
        });
        format!("{message}\n")
    };
    let input = [
        forged(1, json!({"name": "alpha-only"})),
        forged(2, json!({"name": "shared-key", "run_id": "run_a"})),
        forged(3, json!({"name": "shared-key"})),
    ]
    .concat();

    let (_, replies) = exchange(&beta, input.as_bytes()).await;

    assert_eq!(
        tool_text(&replies[0]),
        (true, "no rule allows this call".to_owned())
    );
    assert_eq!(error_code(&replies[1]), INVALID_PARAMS);
    assert_eq!(tool_text(&replies[2]), (false, "value-beta".to_owned()));
}

#[tokio::test]
async fn a_run_that_ends_lets_go_of_its_own_session_and_values_only() {
    let server = sessions_upstream().await;
    let alpha = run_gates("run_a", *server.address(), "token-alpha", 5);
    let beta = run_gates("run_b", *server.address(), "token-beta", 5);
    let search = call(1, "alpha__search", json!({}));
    let (_, opened) = exchange(&alpha, search.as_bytes()).await;
    let (_, other) = exchange(&beta, search.as_bytes()).await;
    assert!(!tool_text(&opened[0]).0 && !tool_text(&other[0]).0);

    alpha.mcp.close();
    tokio::time::sleep(Duration::from_millis(200)).await;
    let (_, ended) = exchange(&alpha, search.as_bytes()).await;
    let (_, kept) = exchange(&beta, search.as_bytes()).await;

    assert_eq!(error_code(&ended[0]), INVALID_PARAMS);
    assert_eq!(alpha.mcp.credential_value("alpha-token"), None);
    assert_eq!(tool_text(&kept[0]), (false, "found".to_owned()));
    let received = server.received_requests().await.expect("recording");
    let closed: Vec<(String, String)> = received
        .iter()
        .filter(|request| request.method.as_str() == "DELETE")
        .map(|request| {
            (
                header(request, "mcp-session-id"),
                header(request, "authorization"),
            )
        })
        .collect();
    assert_eq!(
        closed,
        [("session-1".to_owned(), "Bearer token-alpha".to_owned())]
    );
    let last = calls_seen(&server).await.pop().expect("call");
    assert_eq!(last.0, "session-2");
}
