use std::os::unix::fs::symlink;

use tempfile::TempDir;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::*;
use crate::libs::testing::{digest_of, FakeSource};

const IMAGE: &[u8] = b"qcow2-alpha";
const URL: &str = "https://images.example.com/naos-agents-1.0.0.qcow2";

fn cache(dir: &TempDir, limit: u64) -> ImageCache {
    let root = dir.path().join("images");
    fs::create_dir(&root).expect("mkdir");
    ImageCache::new(root, limit)
}

fn entries(dir: &TempDir) -> Vec<String> {
    let mut names: Vec<String> = fs::read_dir(dir.path().join("images"))
        .expect("read dir")
        .map(|entry| {
            entry
                .expect("entry")
                .file_name()
                .to_string_lossy()
                .into_owned()
        })
        .collect();
    names.sort();
    names
}

#[tokio::test]
async fn fetch_stores_a_verified_read_only_image() {
    let dir = tempfile::tempdir().expect("tempdir");
    let cache = cache(&dir, 1024);
    let digest = digest_of(IMAGE);
    let source = FakeSource::new(IMAGE);

    cache.fetch(&source, URL, &digest).await.expect("fetched");
    cache.fetch(&source, URL, &digest).await.expect("cached");

    assert_eq!(source.calls(), 1);
    let path = cache.path(&digest).expect("path");
    assert_eq!(fs::read(&path).expect("read"), IMAGE);
    assert_eq!(fs::metadata(&path).expect("meta").mode() & 0o777, 0o444);
    let mut verified = cache.open_verified(&digest).expect("verified");
    let mut body = Vec::new();
    verified.read_to_end(&mut body).expect("read");
}

#[tokio::test]
async fn mismatched_download_is_discarded() {
    let dir = tempfile::tempdir().expect("tempdir");
    let cache = cache(&dir, 1024);
    let digest = digest_of(b"something else");

    let outcome = cache.fetch(&FakeSource::new(IMAGE), URL, &digest).await;

    assert!(matches!(outcome, Err(AgentError::Image(_))));
    assert!(entries(&dir).is_empty());
}

#[tokio::test]
async fn oversized_download_is_discarded() {
    let dir = tempfile::tempdir().expect("tempdir");
    let cache = cache(&dir, 4);

    let outcome = cache
        .fetch(&FakeSource::new(IMAGE), URL, &digest_of(IMAGE))
        .await;

    assert!(outcome.is_err());
    assert!(entries(&dir).is_empty());
}

#[tokio::test]
async fn tampered_cache_is_detected_and_removed() {
    let dir = tempfile::tempdir().expect("tempdir");
    let cache = cache(&dir, 1024);
    let digest = digest_of(IMAGE);
    cache
        .fetch(&FakeSource::new(IMAGE), URL, &digest)
        .await
        .expect("fetched");
    let path = cache.path(&digest).expect("path");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).expect("chmod");
    fs::write(&path, b"qcow2-beta").expect("tamper");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o444)).expect("chmod");

    assert!(matches!(
        cache.open_verified(&digest),
        Err(AgentError::Image(_))
    ));
    assert!(!path.exists());
}

#[test]
fn symlinked_or_shared_images_are_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let cache = cache(&dir, 1024);
    let digest = digest_of(IMAGE);
    let real = dir.path().join("real.qcow2");
    fs::write(&real, IMAGE).expect("write");
    symlink(&real, cache.path(&digest).expect("path")).expect("symlink");

    assert!(matches!(
        cache.open_verified(&digest),
        Err(AgentError::Image(_))
    ));

    fs::remove_file(cache.path(&digest).expect("path")).expect("unlink");
    fs::copy(&real, cache.path(&digest).expect("path")).expect("copy");
    fs::set_permissions(
        cache.path(&digest).expect("path"),
        fs::Permissions::from_mode(0o666),
    )
    .expect("chmod");

    assert!(matches!(
        cache.open_verified(&digest),
        Err(AgentError::Image(_))
    ));
}

#[test]
fn names_that_are_not_digests_are_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let cache = cache(&dir, 1024);

    for bad in ["sha256:../../etc/passwd", "md5:abc", "sha256:ABC"] {
        assert!(
            matches!(cache.path(bad), Err(AgentError::Image(_))),
            "{bad}"
        );
    }
}

#[tokio::test]
async fn http_source_follows_redirects_without_credentials() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/releases/naos-agents.qcow2"))
        .respond_with(
            ResponseTemplate::new(302)
                .insert_header("location", format!("{}/objects/blob", server.uri())),
        )
        .expect(1)
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/objects/blob"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(IMAGE.to_vec()))
        .expect(1)
        .mount(&server)
        .await;
    let url = format!("{}/releases/naos-agents.qcow2", server.uri());
    let mut sink = Vec::new();

    let written = HttpImages::new()
        .expect("client")
        .fetch(&url, &mut sink, 1024)
        .await
        .expect("downloaded");

    assert_eq!(written, IMAGE.len() as u64);
    assert_eq!(sink, IMAGE);
    let requests = server.received_requests().await.expect("recorded");
    assert!(requests
        .iter()
        .all(|request| !request.headers.contains_key("authorization")));
}

#[tokio::test]
async fn http_source_enforces_the_limit_and_status() {
    let server = MockServer::start().await;
    Mock::given(path("/large"))
        .respond_with(ResponseTemplate::new(200).set_body_bytes(vec![0u8; 64]))
        .mount(&server)
        .await;
    Mock::given(path("/missing"))
        .respond_with(ResponseTemplate::new(404))
        .mount(&server)
        .await;
    let images = HttpImages::new().expect("client");

    let oversized = images
        .fetch(&format!("{}/large", server.uri()), &mut Vec::new(), 16)
        .await;
    let missing = images
        .fetch(&format!("{}/missing", server.uri()), &mut Vec::new(), 1024)
        .await;

    assert!(matches!(oversized, Err(AgentError::Image(_))));
    assert!(matches!(missing, Err(AgentError::Image(message)) if message.contains("404")));
}
