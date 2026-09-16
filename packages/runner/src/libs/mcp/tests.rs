use super::*;

async fn exchange(input: &[u8]) -> (Result<(), AgentError>, Vec<Value>) {
    let mut out = Vec::new();
    let outcome = serve("run_a", input, &mut out).await;
    let replies = out
        .split(|byte| *byte == b'\n')
        .filter(|line| !line.is_empty())
        .map(|line| serde_json::from_slice(line).expect("json"))
        .collect();
    (outcome, replies)
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

    let (outcome, replies) = exchange(input.as_bytes()).await;

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

    let (_, replies) = exchange(input).await;

    assert_eq!(
        replies[0]["result"]["protocolVersion"],
        PROTOCOL_VERSIONS[0]
    );
}

#[tokio::test]
async fn anything_but_the_handshake_is_refused() {
    let input = concat!(
        r#"{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"beta"}}"#,
        "\n",
        "not json\n",
        r#"[{"jsonrpc":"2.0","id":2,"method":"ping"}]"#,
        "\n",
        r#"{"id":3,"method":"ping"}"#,
        "\n",
        r#"{"jsonrpc":"2.0","id":4}"#,
        "\n",
    );

    let (outcome, replies) = exchange(input.as_bytes()).await;

    assert!(outcome.is_ok());
    let codes: Vec<(Value, i64)> = replies
        .iter()
        .map(|reply| {
            (
                reply["id"].clone(),
                reply["error"]["code"].as_i64().expect("code"),
            )
        })
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

    let (outcome, replies) = exchange(input).await;

    assert!(outcome.is_ok());
    assert_eq!(replies.len(), 1);
}

#[tokio::test]
async fn an_oversized_line_ends_the_session() {
    let mut input = vec![b'x'; MAX_LINE + 1];
    input.extend_from_slice(b"\n{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"ping\"}\n");

    let (outcome, replies) = exchange(&input).await;

    assert!(outcome.is_err());
    assert!(replies.is_empty());
}
