use std::future::Future;
use std::time::Duration;

use reqwest::{redirect, Client, RequestBuilder, StatusCode};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use url::Url;

use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_DETAIL: usize = 200;

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

#[derive(Debug, Clone, Deserialize)]
pub struct DesiredRun {
    pub id: String,
    pub status: RunStatus,
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
}

pub struct HttpApi {
    client: Client,
    base: Url,
}

impl HttpApi {
    pub fn new(base: Url) -> Result<Self, AgentError> {
        let client = Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .redirect(redirect::Policy::none())
            .https_only(base.scheme() == "https")
            .user_agent(concat!("naos-agent/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|err| AgentError::Transport(err.to_string()))?;
        Ok(Self { client, base })
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
        let response = request
            .send()
            .await
            .map_err(|err| AgentError::Transport(err.without_url().to_string()))?;
        let status = response.status();
        if status.is_success() {
            return response
                .json()
                .await
                .map_err(|err| AgentError::Transport(err.without_url().to_string()));
        }
        let mut detail = response.text().await.unwrap_or_default();
        detail.truncate(detail.floor_char_boundary(MAX_DETAIL));
        Err(match status {
            StatusCode::UNAUTHORIZED => AgentError::Unauthorized,
            StatusCode::CONFLICT => AgentError::Conflict(detail),
            _ => AgentError::Api {
                status: status.as_u16(),
                detail,
            },
        })
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
}

#[cfg(test)]
mod tests;
