use std::collections::BTreeMap;
use std::future::Future;
use std::io::Write;
use std::time::Duration;

use reqwest::{redirect, Client, ClientBuilder, RequestBuilder, Response, StatusCode};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use url::Url;

use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_DETAIL: usize = 200;
pub const DIGEST_PREFIX: &str = "sha256:";

pub fn is_digest(value: &str) -> bool {
    value.strip_prefix(DIGEST_PREFIX).is_some_and(|hex| {
        hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum RunStatus {
    Pending,
    Starting,
    Started,
    Stopping,
    Collecting,
    WaitingMerge,
    Completed,
    Failed,
    Cancelled,
}

#[derive(Deserialize)]
pub struct IssuedToken {
    pub value: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LeaseGrant {
    pub ttl_seconds: u64,
}

#[derive(Deserialize)]
pub struct Registration {
    pub runner_id: String,
    pub token: IssuedToken,
    pub lease: LeaseGrant,
}

#[derive(Deserialize)]
pub struct HeartbeatReply {
    pub lease: LeaseGrant,
    pub token: Option<IssuedToken>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImageRef {
    pub id: String,
    pub digest: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeSpec {
    pub cpu: u32,
    pub memory_mib: u32,
    pub disk_gib: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct RunSpec {
    pub image: ImageRef,
    pub runtime: RuntimeSpec,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DesiredRun {
    pub id: String,
    pub status: RunStatus,
    pub spec: RunSpec,
    #[serde(default)]
    pub policies: BTreeMap<String, Option<serde_json::Value>>,
}

impl DesiredRun {
    pub fn granted_policies(&self) -> Vec<&str> {
        self.policies
            .iter()
            .filter(|(_, document)| document.is_some())
            .map(|(kind, _)| kind.as_str())
            .collect()
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct DesiredState {
    pub lease_id: String,
    pub runs: Vec<DesiredRun>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Transition<'a> {
    pub lease_id: &'a str,
    pub expected: RunStatus,
    pub target: RunStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<&'a str>,
}

pub trait Api {
    fn register(
        &self,
        name: &str,
        enrollment_token: &str,
    ) -> impl Future<Output = Result<Registration, AgentError>> + Send;

    fn heartbeat(
        &self,
        credentials: &Credentials,
        capacity: u32,
    ) -> impl Future<Output = Result<HeartbeatReply, AgentError>> + Send;

    fn desired(
        &self,
        credentials: &Credentials,
    ) -> impl Future<Output = Result<DesiredState, AgentError>> + Send;

    fn transition(
        &self,
        credentials: &Credentials,
        run_id: &str,
        transition: &Transition<'_>,
    ) -> impl Future<Output = Result<(), AgentError>> + Send;

    /// Streams the image into `sink` and fails once more than `limit` bytes arrive.
    fn download_image(
        &self,
        credentials: &Credentials,
        digest: &str,
        sink: &mut (dyn Write + Send),
        limit: u64,
    ) -> impl Future<Output = Result<u64, AgentError>> + Send;
}

pub struct HttpApi {
    client: Client,
    download: Client,
    base: Url,
}

fn transport(err: reqwest::Error) -> AgentError {
    AgentError::Transport(err.without_url().to_string())
}

fn too_large(limit: u64) -> AgentError {
    AgentError::Image(format!("image exceeds {limit} bytes"))
}

impl HttpApi {
    pub fn new(base: Url) -> Result<Self, AgentError> {
        let client = Self::builder(&base)
            .timeout(REQUEST_TIMEOUT)
            .build()
            .map_err(|err| AgentError::Transport(err.to_string()))?;
        let download = Self::builder(&base)
            .connect_timeout(CONNECT_TIMEOUT)
            .read_timeout(READ_TIMEOUT)
            .build()
            .map_err(|err| AgentError::Transport(err.to_string()))?;
        Ok(Self {
            client,
            download,
            base,
        })
    }

    fn builder(base: &Url) -> ClientBuilder {
        Client::builder()
            .redirect(redirect::Policy::none())
            .https_only(base.scheme() == "https")
            .user_agent(concat!("naos-agent/", env!("CARGO_PKG_VERSION")))
    }

    fn url(&self, segments: &[&str]) -> Result<Url, AgentError> {
        for segment in segments {
            if segment.is_empty()
                || !segment
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '_')
            {
                return Err(AgentError::Api {
                    status: 0,
                    detail: format!("refusing unsafe path segment {segment:?}"),
                });
            }
        }
        let path = format!("api/v1/runners/{}", segments.join("/"));
        self.base
            .join(&path)
            .map_err(|err| AgentError::Config(err.to_string()))
    }

    async fn send<T: DeserializeOwned>(request: RequestBuilder) -> Result<T, AgentError> {
        let response = request.send().await.map_err(transport)?;
        if !response.status().is_success() {
            return Err(Self::failure(response).await);
        }
        response.json().await.map_err(transport)
    }

    async fn failure(response: Response) -> AgentError {
        let status = response.status();
        let mut detail = response.text().await.unwrap_or_default();
        detail.truncate(detail.floor_char_boundary(MAX_DETAIL));
        match status {
            StatusCode::UNAUTHORIZED => AgentError::Unauthorized,
            StatusCode::CONFLICT => AgentError::Conflict(detail),
            _ => AgentError::Api {
                status: status.as_u16(),
                detail,
            },
        }
    }
}

impl Api for HttpApi {
    async fn register(
        &self,
        name: &str,
        enrollment_token: &str,
    ) -> Result<Registration, AgentError> {
        let request = self
            .client
            .post(self.url(&["register"])?)
            .bearer_auth(enrollment_token)
            .json(&serde_json::json!({ "name": name }));
        Self::send(request).await
    }

    async fn heartbeat(
        &self,
        credentials: &Credentials,
        capacity: u32,
    ) -> Result<HeartbeatReply, AgentError> {
        let request = self
            .client
            .post(self.url(&[&credentials.runner_id, "heartbeat"])?)
            .bearer_auth(&credentials.token)
            .json(&serde_json::json!({ "capacity": capacity }));
        Self::send(request).await
    }

    async fn desired(&self, credentials: &Credentials) -> Result<DesiredState, AgentError> {
        let request = self
            .client
            .get(self.url(&[&credentials.runner_id, "runs"])?)
            .bearer_auth(&credentials.token);
        Self::send(request).await
    }

    async fn transition(
        &self,
        credentials: &Credentials,
        run_id: &str,
        transition: &Transition<'_>,
    ) -> Result<(), AgentError> {
        let request = self
            .client
            .post(self.url(&[&credentials.runner_id, "runs", run_id, "transition"])?)
            .bearer_auth(&credentials.token)
            .json(transition);
        Self::send::<serde_json::Value>(request).await.map(|_| ())
    }

    async fn download_image(
        &self,
        credentials: &Credentials,
        digest: &str,
        sink: &mut (dyn Write + Send),
        limit: u64,
    ) -> Result<u64, AgentError> {
        if !is_digest(digest) {
            return Err(AgentError::Image(format!(
                "refusing unsafe digest {digest:?}"
            )));
        }
        let mut url = self.url(&[&credentials.runner_id, "images"])?;
        url.path_segments_mut()
            .map_err(|()| AgentError::Config("api url cannot carry a path".into()))?
            .push(digest);
        let mut response = self
            .download
            .get(url)
            .bearer_auth(&credentials.token)
            .send()
            .await
            .map_err(transport)?;
        if !response.status().is_success() {
            return Err(Self::failure(response).await);
        }
        if response
            .content_length()
            .is_some_and(|length| length > limit)
        {
            return Err(too_large(limit));
        }
        let mut written: u64 = 0;
        while let Some(chunk) = response.chunk().await.map_err(transport)? {
            written += chunk.len() as u64;
            if written > limit {
                return Err(too_large(limit));
            }
            sink.write_all(&chunk)?;
        }
        Ok(written)
    }
}

#[cfg(test)]
mod tests;
