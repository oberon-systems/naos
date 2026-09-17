use std::fs;
use std::net::SocketAddr;
use std::path::Path;

use tempfile::TempDir;
use wiremock::matchers::{body_partial_json, method, path};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

use super::*;
use crate::libs::api::RunCredential;
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

fn no_gates() -> RunGates {
    RunGates {
        network: NetworkGate::from_snapshot("run_a", None).expect("policy"),
        shell: no_shell(),
        mcp: no_mcp(),
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
        mcp: no_mcp(),
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
        mcp: no_mcp(),
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
    let mut document = json!({
        "name": "alpha",
        "url": format!("http://example.com:{}/mcp", address.port()),
        "tools": ["search"],
        "resources": ["docs://alpha/"],
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
        mcp: McpGate::local(&json!({ "servers": [document] }), vec![address.ip()]).expect("policy"),
    };
    grant(&gates, u64::MAX);
    gates
}

fn grant(gates: &RunGates, expires_at: u64) {
    gates.mcp.refresh(&BTreeMap::from([(
        "alpha-token".to_owned(),
        RunCredential {
            value: SECRET.into(),
            expires_at,
        },
    )]));
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
        (true, "tool not allowed".to_owned())
    );
    assert_eq!(error_code(&replies[1]), -32602);
    assert_eq!(error_code(&replies[2]), -32602);
    assert!(methods(&server).await.is_empty());
}

#[tokio::test]
async fn a_missing_or_expired_credential_denies_without_a_request() {
    let server = upstream(answers, false).await;
    let gates = upstream_gates(*server.address(), json!({}));
    gates.mcp.refresh(&BTreeMap::new());

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
