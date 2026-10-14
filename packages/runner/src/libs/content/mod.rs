use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::json;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::header::AUTHORIZATION;
use tokio_tungstenite::tungstenite::Message;
use url::Url;

use crate::libs::console::live::socket_base;
use crate::libs::credentials::{CredentialStore, Credentials};
use crate::libs::error::AgentError;
use crate::libs::overlay::content::{self, Piece, Side};
use crate::libs::overlay::Diff;
use crate::libs::qemu::VmPaths;
use crate::libs::runtime::changes_of;

/// How long a dropped socket waits before it is dialled again.
const RETRY: Duration = Duration::from_secs(5);

/// One range of one entry, as the API forwards an operator's ask.
#[derive(Debug, Deserialize)]
struct Ask {
    id: u64,
    run_id: String,
    index: usize,
    side: Side,
    offset: u64,
    length: u64,
}

/// The collected changes of the Run asked last; a viewer asks many ranges of the same Run.
struct Changes {
    run_id: String,
    paths: VmPaths,
    workspace: Option<PathBuf>,
    diff: Arc<Diff>,
}

fn runtime(err: impl std::fmt::Display) -> AgentError {
    AgentError::Runtime(format!("content socket: {err}"))
}

/// Keeps one socket open to the API for as long as the runner runs. The API sends the ranges
/// operators ask for down it; the runner never listens on a port of its own.
pub async fn serve(api_url: Url, store: CredentialStore, vm_dir: PathBuf) {
    loop {
        match store.load() {
            Ok(Some(credentials)) => match session(&api_url, &credentials, &vm_dir).await {
                Ok(()) => tracing::info!("content socket closed"),
                Err(err) => tracing::warn!(error = %err, "content socket failed"),
            },
            Ok(None) => {}
            Err(err) => tracing::warn!(error = %err, "content socket has no credentials"),
        }
        tokio::time::sleep(RETRY).await;
    }
}

async fn session(
    api_url: &Url,
    credentials: &Credentials,
    vm_dir: &Path,
) -> Result<(), AgentError> {
    let url = format!(
        "{}/api/v1/runners/{}/content",
        socket_base(api_url),
        credentials.runner_id
    );
    let mut request = url.as_str().into_client_request().map_err(runtime)?;
    request.headers_mut().insert(
        AUTHORIZATION,
        format!("Bearer {}", credentials.token)
            .parse()
            .map_err(runtime)?,
    );
    let (mut socket, _) = tokio_tungstenite::connect_async(request)
        .await
        .map_err(runtime)?;
    tracing::info!(url, "content socket open");
    let mut kept: Option<Arc<Changes>> = None;
    while let Some(message) = socket.next().await {
        let text = match message.map_err(runtime)? {
            Message::Text(text) => text,
            Message::Close(_) => return Ok(()),
            _ => continue,
        };
        let Ok(ask) = serde_json::from_str::<Ask>(&text) else {
            tracing::warn!("content socket got a frame it does not read");
            continue;
        };
        let reply = match answer(vm_dir, &mut kept, &ask).await {
            Ok(Some(piece)) => {
                let mut frame = ask.id.to_be_bytes().to_vec();
                frame.extend_from_slice(&piece.size.to_be_bytes());
                frame.extend_from_slice(&piece.data);
                Message::Binary(frame)
            }
            Ok(None) => Message::Text(json!({"id": ask.id, "absent": true}).to_string()),
            Err(err) => {
                // the VM may have moved to the archive since its changes were found
                kept = None;
                Message::Text(json!({"id": ask.id, "error": err.to_string()}).to_string())
            }
        };
        socket.send(reply).await.map_err(runtime)?;
    }
    Ok(())
}

async fn answer(
    vm_dir: &Path,
    kept: &mut Option<Arc<Changes>>,
    ask: &Ask,
) -> Result<Option<Piece>, AgentError> {
    let changes = match kept {
        Some(changes) if changes.run_id == ask.run_id => changes.clone(),
        _ => {
            let (vm_dir, run_id) = (vm_dir.to_path_buf(), ask.run_id.clone());
            let found = tokio::task::spawn_blocking(move || locate(&vm_dir, &run_id))
                .await
                .map_err(runtime)??;
            kept.insert(Arc::new(found)).clone()
        }
    };
    let (side, offset, length, index) = (ask.side, ask.offset, ask.length, ask.index);
    tokio::task::spawn_blocking(move || {
        let entry = changes
            .diff
            .entries
            .get(index)
            .ok_or_else(|| AgentError::Runtime(format!("the diff has no entry {index}")))?;
        content::read(
            &changes.paths.upper(),
            changes.workspace.as_deref(),
            entry,
            side,
            offset,
            length,
        )
    })
    .await
    .map_err(runtime)?
}

fn locate(vm_dir: &Path, run_id: &str) -> Result<Changes, AgentError> {
    let (paths, workspace) = changes_of(vm_dir, run_id).ok_or_else(|| {
        AgentError::Runtime(format!(
            "run {run_id} has no collected changes on this runner"
        ))
    })?;
    let diff = serde_json::from_slice(&fs::read(paths.diff())?).map_err(runtime)?;
    Ok(Changes {
        run_id: run_id.to_owned(),
        paths,
        workspace,
        diff: Arc::new(diff),
    })
}
