use crate::libs::api::{Api, HttpApi, RunStatus, Transition};
use crate::libs::config::parse_api_url;
use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;
use serde_json::json;
use wiremock::matchers::{body_json, header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

const RUNNER: &str = "/api/v1/runners/rnr_alpha";
const IMAGE_URL: &str = "https://images.example.com/naos-agents-1.0.0.qcow2";

fn api(server: &MockServer) -> HttpApi {
    HttpApi::new(parse_api_url(&server.uri()).expect("loopback url")).expect("client")
}

fn credentials() -> Credentials {
    Credentials {
        runner_id: "rnr_alpha".into(),
        token: "token-alpha".into(),
    }
}

#[tokio::test]
async fn register_sends_the_enrollment_bearer() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/api/v1/runners/register"))
        .and(header("authorization", "Bearer enroll-alpha"))
        .and(body_json(json!({ "name": "alpha" })))
        .respond_with(ResponseTemplate::new(201).set_body_json(json!({
            "runner_id": "rnr_alpha",
            "token": { "value": "token-alpha", "expires_at": 1767312000 },
            "lease": { "id": "lease_alpha", "expires_at": 1767225660, "ttl_seconds": 60 },
        })))
        .expect(1)
        .mount(&server)
        .await;

    let registration = api(&server)
        .register("alpha", "enroll-alpha")
        .await
        .expect("registered");

    assert_eq!(registration.runner_id, "rnr_alpha");
    assert_eq!(registration.token.value, "token-alpha");
    assert_eq!(registration.lease.ttl_seconds, 60);
}

#[tokio::test]
async fn heartbeat_and_transition_use_the_runner_token() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path(format!("{RUNNER}/heartbeat")))
        .and(header("authorization", "Bearer token-alpha"))
        .and(body_json(json!({ "capacity": 2 })))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "lease": { "id": "lease_alpha", "expires_at": 1767225660, "ttl_seconds": 60 },
            "token": null,
        })))
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path(format!("{RUNNER}/tasks/run_a/transition")))
        .and(body_json(json!({
            "lease_id": "lease_alpha", "expected": "PENDING", "target": "STARTING",
        })))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({ "status": "STARTING" })))
        .expect(1)
        .mount(&server)
        .await;
    let api = api(&server);

    let reply = api.heartbeat(&credentials(), 2).await.expect("heartbeat");
    assert!(reply.token.is_none());
    let claim = Transition {
        lease_id: "lease_alpha",
        expected: RunStatus::Pending,
        target: RunStatus::Starting,
        reason: None,
    };
    api.transition(&credentials(), "run_a", &claim)
        .await
        .expect("claimed");
}

#[tokio::test]
async fn statuses_map_to_typed_errors() {
    let server = MockServer::start().await;
    for (code, run) in [(401, "run_a"), (409, "run_b"), (500, "run_c")] {
        Mock::given(path(format!("{RUNNER}/tasks/{run}/transition")))
            .respond_with(ResponseTemplate::new(code).set_body_string("nope"))
            .mount(&server)
            .await;
    }
    let api = api(&server);

    assert!(matches!(
        claim(&api, "run_a").await,
        Err(AgentError::Unauthorized)
    ));
    assert!(matches!(
        claim(&api, "run_b").await,
        Err(AgentError::Conflict(_))
    ));
    assert!(matches!(
        claim(&api, "run_c").await,
        Err(AgentError::Api { status: 500, .. })
    ));
}

async fn claim(api: &HttpApi, run_id: &str) -> Result<(), AgentError> {
    let transition = Transition {
        lease_id: "lease_alpha",
        expected: RunStatus::Pending,
        target: RunStatus::Starting,
        reason: None,
    };
    api.transition(&credentials(), run_id, &transition).await
}

#[tokio::test]
async fn redirects_are_not_followed() {
    let server = MockServer::start().await;
    Mock::given(path(format!("{RUNNER}/tasks")))
        .respond_with(
            ResponseTemplate::new(307).insert_header("location", "http://192.0.2.10/steal"),
        )
        .mount(&server)
        .await;

    let outcome = api(&server).desired(&credentials()).await;

    assert!(matches!(outcome, Err(AgentError::Api { status: 307, .. })));
}

#[tokio::test]
async fn unknown_run_status_fails_closed() {
    let server = MockServer::start().await;
    Mock::given(path(format!("{RUNNER}/tasks")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "lease_id": "lease_alpha",
            "tasks": [{ "id": "run_a", "status": "EXPLODED", "spec": {}, "policies": {} }],
        })))
        .mount(&server)
        .await;

    let outcome = api(&server).desired(&credentials()).await;

    assert!(matches!(outcome, Err(AgentError::Transport(_))));
}

fn digest() -> String {
    format!("sha256:{}", "a".repeat(64))
}

#[tokio::test]
async fn desired_runs_carry_spec_image_url_and_policies() {
    let server = MockServer::start().await;
    Mock::given(path(format!("{RUNNER}/tasks")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "lease_id": "lease_alpha",
            "tasks": [{
                "id": "run_a",
                "status": "PENDING",
                "spec": {
                    "image": { "id": "image_alpha", "digest": digest() },
                    "runtime": { "cpu": 2, "memory_mib": 1024, "disk_gib": 4 },
                    "timeout": 3600,
                    "mounts": { "policy": null },
                },
                "image_url": IMAGE_URL,
                "policies": { "mount": { "workdir": "/naos/alpha" }, "network": null },
            }],
        })))
        .mount(&server)
        .await;

    let state = api(&server).desired(&credentials()).await.expect("desired");

    let [run] = state.runs.as_slice() else {
        panic!("one run expected");
    };
    assert_eq!(run.spec.runtime.disk_gib, 4);
    assert_eq!(run.image_url, IMAGE_URL);
    assert_eq!(run.granted_policies(), vec!["mount"]);
}

#[tokio::test]
async fn unknown_runtime_fields_fail_closed() {
    let server = MockServer::start().await;
    Mock::given(path(format!("{RUNNER}/tasks")))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "lease_id": "lease_alpha",
            "tasks": [{
                "id": "run_a",
                "status": "PENDING",
                "spec": {
                    "image": { "id": "image_alpha", "digest": digest() },
                    "runtime": { "cpu": 2, "memory_mib": 1024, "disk_gib": 4, "gpu": 1 },
                },
                "image_url": IMAGE_URL,
            }],
        })))
        .mount(&server)
        .await;

    let outcome = api(&server).desired(&credentials()).await;

    assert!(matches!(outcome, Err(AgentError::Transport(_))));
}

#[tokio::test]
async fn unsafe_path_segments_never_reach_the_network() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200))
        .expect(0)
        .mount(&server)
        .await;
    let forged = Credentials {
        runner_id: "../register".into(),
        token: "token-alpha".into(),
    };

    let outcome = api(&server).heartbeat(&forged, 1).await;

    assert!(matches!(outcome, Err(AgentError::Api { status: 0, .. })));
}
