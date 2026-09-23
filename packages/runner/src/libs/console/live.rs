use std::collections::HashMap;
use std::path::Path;
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use tokio::io::AsyncWriteExt;
use tokio::net::unix::OwnedWriteHalf;
use tokio::net::UnixStream;
use tokio::task::JoinHandle;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::header::AUTHORIZATION;
use tokio_tungstenite::tungstenite::Message;
use url::Url;

use crate::libs::console::ship::read_at;
use crate::libs::credentials::Credentials;
use crate::libs::error::AgentError;
use crate::libs::qemu::VmPaths;
use crate::libs::runtime::LocalVm;

/// How often the log is looked at while the socket is up. The echo of a keystroke
/// travels back over this, so it is short.
const POLL: Duration = Duration::from_millis(50);
const CHUNK: usize = 64 * 1024;
/// How long a VM waits before its socket is dialled again.
const RETRY: Duration = Duration::from_secs(5);
/// How long the guest's console is held after the last key. Letting go gives
/// `naos-runner console` on the host its turn, since the chardev serves one.
const IDLE: Duration = Duration::from_secs(30);

fn runtime(err: impl std::fmt::Display) -> AgentError {
    AgentError::Runtime(format!("console socket: {err}"))
}

/// What the API answers with: how far it holds the log, or how big the viewer's
/// grid is. Neither field arrives on every frame.
#[derive(Debug, Deserialize)]
struct Reply {
    offset: Option<u64>,
    cols: Option<u16>,
    rows: Option<u16>,
}

/// One socket per running VM, kept while the VM runs. The console report in
/// `ship.rs` is the fallback and stands down for a VM this holds.
#[derive(Default)]
pub struct Sessions {
    live: HashMap<String, JoinHandle<()>>,
    failed: HashMap<String, Instant>,
}

impl Sessions {
    pub fn holds(&self, vm_id: &str) -> bool {
        self.live.get(vm_id).is_some_and(|task| !task.is_finished())
    }

    /// A socket that died is not opened again on the next tick, or a broken one
    /// would be dialled once a second forever.
    fn waiting(&self, vm_id: &str) -> bool {
        self.failed
            .get(vm_id)
            .is_some_and(|at| at.elapsed() < RETRY)
    }

    pub fn keep(
        &mut self,
        api_url: &Url,
        credentials: &Credentials,
        vm_dir: &Path,
        running: &[LocalVm],
    ) {
        self.live.retain(|vm_id, task| {
            !task.is_finished() && running.iter().any(|vm| &vm.vm_id == vm_id)
        });
        self.failed
            .retain(|vm_id, _| running.iter().any(|vm| &vm.vm_id == vm_id));
        for vm in running {
            if self.live.contains_key(&vm.vm_id) {
                continue;
            }
            if self.waiting(&vm.vm_id) {
                continue;
            }
            let url = socket_url(api_url, &credentials.runner_id, &vm.run_id);
            let paths = VmPaths::new(vm_dir.join(&vm.vm_id));
            let credentials = credentials.clone();
            let run_id = vm.run_id.clone();
            let vm_id = vm.vm_id.clone();
            self.failed.insert(vm_id.clone(), Instant::now());
            self.live.insert(
                vm_id,
                tokio::spawn(async move {
                    match session(&url, credentials, &run_id, paths).await {
                        Ok(()) => tracing::info!(run_id, "console socket closed"),
                        Err(err) => tracing::warn!(error = %err, run_id, "console socket failed"),
                    }
                }),
            );
        }
    }
}

/// The same host with a websocket scheme. Built by hand because `Url::set_scheme`
/// refuses some of these swaps outright.
fn socket_url(api_url: &Url, runner_id: &str, run_id: &str) -> String {
    let base = api_url.as_str().trim_end_matches('/');
    let base = match base.strip_prefix("https") {
        Some(rest) => format!("wss{rest}"),
        None => match base.strip_prefix("http") {
            Some(rest) => format!("ws{rest}"),
            None => base.to_string(),
        },
    };
    format!("{base}/api/v1/runners/{runner_id}/runs/{run_id}/console")
}

/// Ships the log as it grows and writes back whatever the driver types. The VM's
/// console socket is opened on the first key, so a run nobody types into leaves
/// it free for `naos-runner console`.
async fn session(
    url: &str,
    credentials: Credentials,
    run_id: &str,
    paths: VmPaths,
) -> Result<(), AgentError> {
    let mut request = url.into_client_request().map_err(runtime)?;
    request.headers_mut().insert(
        AUTHORIZATION,
        format!("Bearer {}", credentials.token)
            .parse()
            .map_err(runtime)?,
    );
    let (mut socket, _) = tokio_tungstenite::connect_async(request)
        .await
        .map_err(runtime)?;
    tracing::info!(run_id, url, "console socket open");

    let mut offset: u64 = 0;
    let mut sent = false;
    // held open while the keys keep coming: connecting for every one of them loses
    // bytes and trips over the chardev's backlog
    let mut to_vm: Option<Keyboard> = None;
    let mut typed_at = Instant::now();
    // the grid changes rarely, and its port is held the same way
    let mut control: Option<UnixStream> = None;
    let mut size: Option<(u16, u16)> = None;
    let mut buffer = vec![0u8; CHUNK];
    let mut ticker = tokio::time::interval(POLL);
    loop {
        tokio::select! {
            message = socket.next() => {
                let Some(message) = message else { return Ok(()) };
                match message.map_err(runtime)? {
                    Message::Text(text) => {
                        let reply: Reply = serde_json::from_str(text.as_str()).map_err(runtime)?;
                        if let Some(held) = reply.offset {
                            offset = held;
                            sent = false;
                        }
                        if let (Some(cols), Some(rows)) = (reply.cols, reply.rows) {
                            if size == Some((cols, rows)) {
                                tracing::debug!(run_id, cols, rows, "guest already has this size");
                            } else {
                                match resize_in(&mut control, &paths, cols, rows).await {
                                    Ok(()) => {
                                        tracing::info!(run_id, cols, rows, "guest told its size");
                                        size = Some((cols, rows));
                                    }
                                    Err(err) => {
                                        tracing::warn!(error = %err, run_id, "console stays its size");
                                        control = None;
                                    }
                                }
                            }
                        }
                    }
                    Message::Binary(keys) => {
                        typed_at = Instant::now();
                        if let Err(err) = type_in(&mut to_vm, &paths, &keys).await {
                            // The guest's console is one socket and someone else may hold
                            // it; that loses the keys, and never the session.
                            tracing::warn!(error = %err, run_id, "the keys did not reach the guest");
                            to_vm = None;
                        }
                    }
                    Message::Close(_) => return Ok(()),
                    _ => {}
                }
            }
            _ = ticker.tick(), if !sent => {
                if to_vm.is_some() && typed_at.elapsed() > IDLE {
                    to_vm = None;
                }
                let read = read_at(&paths.console_log(), offset, &mut buffer)?;
                if read > 0 {
                    let mut frame = offset.to_be_bytes().to_vec();
                    frame.extend_from_slice(&buffer[..read]);
                    socket.send(Message::Binary(frame)).await.map_err(runtime)?;
                    sent = true;
                }
            }
        }
    }
}

/// The guest's console socket held for typing. QEMU sends the console to every
/// client, so what comes back is drained: left unread it fills the socket and
/// QEMU stalls the guest's serial output. The log already carries all of it.
struct Keyboard {
    keys: OwnedWriteHalf,
    drain: JoinHandle<()>,
}

impl Keyboard {
    async fn open(paths: &VmPaths) -> Result<Self, AgentError> {
        let (mut echo, keys) = UnixStream::connect(paths.console()).await?.into_split();
        let drain = tokio::spawn(async move {
            let _ = tokio::io::copy(&mut echo, &mut tokio::io::sink()).await;
        });
        Ok(Self { keys, drain })
    }
}

impl Drop for Keyboard {
    fn drop(&mut self) {
        self.drain.abort();
    }
}

/// Writes the keys into the VM's console socket, opening it on the first one.
async fn type_in(
    to_vm: &mut Option<Keyboard>,
    paths: &VmPaths,
    keys: &[u8],
) -> Result<(), AgentError> {
    let keyboard = match to_vm {
        Some(keyboard) => keyboard,
        None => to_vm.insert(Keyboard::open(paths).await?),
    };
    keyboard.keys.write_all(keys).await?;
    keyboard.keys.flush().await?;
    Ok(())
}

/// Tells the guest the viewer's grid over its control port, held open the same way.
async fn resize_in(
    control: &mut Option<UnixStream>,
    paths: &VmPaths,
    cols: u16,
    rows: u16,
) -> Result<(), AgentError> {
    let stream = match control {
        Some(stream) => stream,
        None => control.insert(UnixStream::connect(paths.control()).await?),
    };
    stream
        .write_all(format!("{{\"cols\":{cols},\"rows\":{rows}}}\n").as_bytes())
        .await?;
    stream.flush().await?;
    Ok(())
}
