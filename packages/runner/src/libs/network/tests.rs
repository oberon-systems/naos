use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

use serde_json::json;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::*;

// 192.0.2.x sits inside the reserved 192.0.0.0/16 the gate refuses, so the routable
// placeholder has to come from TEST-NET-3.
const PUBLIC: IpAddr = IpAddr::V4(Ipv4Addr::new(203, 0, 113, 10));

fn gate(document: Value) -> NetworkGate {
    NetworkGate::from_snapshot("run_a", Some(&document)).expect("policy")
}

fn refused(document: Value) -> String {
    let err = NetworkGate::from_snapshot("run_a", Some(&document)).expect_err("refused");
    err.to_string()
}

fn get(url: &str) -> GateRequest {
    GateRequest {
        method: Method::GET,
        url: url.to_owned(),
        headers: HeaderMap::new(),
        body: None,
    }
}

#[test]
fn an_empty_policy_denies_everything() {
    assert!(gate(json!({}))
        .authorize("https", "example.com", &[PUBLIC])
        .is_err());
}

#[test]
fn an_allowed_destination_passes() {
    let gate = gate(json!({"allow": [{"protocol": "https", "host": "example.com"}]}));

    assert!(gate.authorize("https", "EXAMPLE.COM.", &[PUBLIC]).is_ok());
    assert!(gate.authorize("http", "example.com", &[PUBLIC]).is_err());
    assert!(gate
        .authorize("https", "other.example.com", &[PUBLIC])
        .is_err());
}

#[test]
fn explicit_deny_wins() {
    let gate = gate(json!({
        "allow": [{"host": "example.com"}],
        "deny": [{"host": "example.com"}],
    }));

    assert!(gate.authorize("https", "example.com", &[PUBLIC]).is_err());
}

#[test]
fn an_address_rule_matches_the_resolution() {
    let gate = gate(json!({"allow": [{"host": "example.com"}], "deny": [{"ip": "203.0.113.10"}]}));

    assert!(gate.authorize("https", "example.com", &[PUBLIC]).is_err());
}

#[test]
fn direct_private_and_rebinding_are_denied() {
    let gate = gate(json!({"allow": [{"host": "example.com"}]}));

    assert!(gate
        .authorize("https", "127.0.0.1", &[IpAddr::V4(Ipv4Addr::LOCALHOST)])
        .is_err());
    assert!(gate.authorize("https", "localhost", &[PUBLIC]).is_err());
    assert!(gate
        .authorize(
            "https",
            "example.com",
            &[IpAddr::V4(Ipv4Addr::new(10, 0, 0, 1))]
        )
        .is_err());
    assert!(gate
        .authorize(
            "https",
            "example.com",
            &[PUBLIC, IpAddr::V6(Ipv6Addr::LOCALHOST)]
        )
        .is_err());
    assert!(gate
        .authorize(
            "https",
            "example.com",
            &[IpAddr::V4(Ipv4Addr::new(169, 254, 169, 254))]
        )
        .is_err());
    assert!(gate.authorize("https", "example.com", &[]).is_err());
}

#[test]
fn a_non_http_scheme_is_denied() {
    let gate = gate(json!({"allow": [{"host": "example.com"}]}));

    assert!(gate.authorize("ftp", "example.com", &[PUBLIC]).is_err());
    assert!(gate.authorize("file", "example.com", &[PUBLIC]).is_err());
}

#[test]
fn a_rule_that_constrains_nothing_is_refused() {
    assert!(refused(json!({"allow": [{}]})).contains("must constrain"));
}

#[test]
fn a_rule_with_an_unusable_host_is_refused() {
    // Left to match time this would collapse to "unconstrained" and allow every host.
    for host in [
        "bad_host",
        "-leading.example.com",
        "trailing-.example.com",
        "",
        "203.0.113.10",
        "::1",
    ] {
        assert!(refused(json!({"allow": [{"host": host}]})).contains("usable hostname"));
    }
}

#[test]
fn a_malformed_policy_fails_closed() {
    assert!(refused(json!({"allow": "all"})).contains("invalid network policy"));
    assert!(refused(json!({"allow": [{"protocol": "ftp"}]})).contains("http or https"));
    assert!(refused(json!({"beta": []})).contains("invalid network policy"));
}

#[test]
fn no_policy_is_the_same_as_an_empty_one() {
    let gate = NetworkGate::from_snapshot("run_a", None).expect("policy");

    assert!(gate.authorize("https", "example.com", &[PUBLIC]).is_err());
}

#[tokio::test]
async fn an_allowed_destination_is_reachable() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/ok"))
        .respond_with(ResponseTemplate::new(200).set_body_string("beta"))
        .mount(&server)
        .await;
    let address = server.address();
    let gate = NetworkGate::local(
        &json!({"allow": [{"protocol": "http", "host": "example.com"}]}),
        vec![address.ip()],
    )
    .expect("policy");

    let response = gate
        .send(get(&format!("http://example.com:{}/ok", address.port())))
        .await
        .expect("response");

    assert_eq!(response.status, 200);
    assert_eq!(response.body, b"beta");
}

// A hostname with an unreachable address next to a usable one must still reach the usable one.
#[tokio::test]
async fn every_resolved_address_is_pinned() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/ok"))
        .respond_with(ResponseTemplate::new(200).set_body_string("beta"))
        .mount(&server)
        .await;
    let address = server.address();
    let gate = NetworkGate::local(
        &json!({"allow": [{"protocol": "http", "host": "example.com"}]}),
        vec![IpAddr::V4(Ipv4Addr::new(127, 0, 0, 2)), address.ip()],
    )
    .expect("policy");

    let response = gate
        .send(get(&format!("http://example.com:{}/ok", address.port())))
        .await
        .expect("response");

    assert_eq!(response.body, b"beta");
}

#[tokio::test]
async fn a_forbidden_destination_is_not_reached() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(200))
        .mount(&server)
        .await;
    let address = server.address();
    let gate = NetworkGate::local(
        &json!({"allow": [{"host": "allowed.example.com"}]}),
        vec![address.ip()],
    )
    .expect("policy");

    for raw in [
        format!("http://denied.example.com:{}/ok", address.port()),
        format!("http://{address}/ok"),
        format!("ftp://allowed.example.com:{}/ok", address.port()),
    ] {
        assert!(gate.send(get(&raw)).await.is_err(), "{raw} was allowed");
    }
    assert!(server
        .received_requests()
        .await
        .expect("requests")
        .is_empty());
}

#[tokio::test]
async fn a_redirect_is_not_followed() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/go"))
        .respond_with(
            ResponseTemplate::new(302).insert_header("location", "http://denied.example.com/"),
        )
        .mount(&server)
        .await;
    let address = server.address();
    let gate = NetworkGate::local(
        &json!({"allow": [{"host": "allowed.example.com"}]}),
        vec![address.ip()],
    )
    .expect("policy");

    let response = gate
        .send(get(&format!(
            "http://allowed.example.com:{}/go",
            address.port()
        )))
        .await
        .expect("response");

    assert_eq!(response.status, 302);
    assert!(gate.send(get("http://denied.example.com/")).await.is_err());
}

#[tokio::test]
async fn an_oversized_response_is_refused() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![b'x'; MAX_BODY + 1]))
        .mount(&server)
        .await;
    let address = server.address();
    let gate = NetworkGate::local(
        &json!({"allow": [{"host": "allowed.example.com"}]}),
        vec![address.ip()],
    )
    .expect("policy");

    let err = gate
        .send(get(&format!(
            "http://allowed.example.com:{}/big",
            address.port()
        )))
        .await
        .expect_err("refused");

    assert!(err.to_string().contains("response too large"));
}

#[tokio::test]
async fn a_run_cannot_exceed_its_request_budget() {
    let gate = NetworkGate::local(
        &json!({"allow": [{"host": "allowed.example.com"}]}),
        vec![PUBLIC],
    )
    .expect("policy");

    for _ in 0..MAX_REQUESTS {
        let err = gate
            .send(get("http://denied.example.com/"))
            .await
            .expect_err("denied");
        assert!(err.to_string().contains("no matching allow rule"));
    }
    let err = gate
        .send(get("http://allowed.example.com/"))
        .await
        .expect_err("throttled");

    assert!(err.to_string().contains("rate limit"));
}
