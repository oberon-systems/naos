use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::Duration;

use url::Url;

use crate::libs::api::Api;
use crate::libs::console::control;
use crate::libs::console::live::Sessions;
use crate::libs::credentials::{CredentialStore, Credentials};
use crate::libs::error::AgentError;
use crate::libs::qemu::VmPaths;
use crate::libs::runtime::scan;

pub const SHIP_INTERVAL: Duration = Duration::from_secs(1);
const CHUNK: usize = 64 * 1024;
const CHUNKS_PER_TICK: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shipped {
    At(u64),
    Stopped,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Report {
    pub shipped: Shipped,
    pub size: Option<(u16, u16)>,
}

pub struct Shipper<A> {
    api: A,
    api_url: Url,
    store: CredentialStore,
    vm_dir: PathBuf,
    shipped: HashMap<String, Shipped>,
    sized: HashMap<String, (u16, u16)>,
    sessions: Sessions,
}

impl<A: Api> Shipper<A> {
    pub fn new(api: A, api_url: Url, store: CredentialStore, vm_dir: PathBuf) -> Self {
        Self {
            api,
            api_url,
            store,
            vm_dir,
            shipped: HashMap::new(),
            sized: HashMap::new(),
            sessions: Sessions::default(),
        }
    }

    pub async fn tick(&mut self) -> Result<(), AgentError> {
        let Some(credentials) = self.store.load()? else {
            return Ok(());
        };
        let running: Vec<_> = scan(&self.vm_dir)?
            .into_iter()
            .filter(|vm| vm.running)
            .collect();
        self.shipped
            .retain(|vm_id, _| running.iter().any(|vm| &vm.vm_id == vm_id));
        self.sized
            .retain(|vm_id, _| running.iter().any(|vm| &vm.vm_id == vm_id));
        // The live socket carries the console for the VMs it holds; this is the fallback.
        self.sessions
            .keep(&self.api_url, &credentials, &self.vm_dir, &running);
        for vm in running {
            if self.sessions.holds(&vm.vm_id) {
                continue;
            }
            let paths = VmPaths::new(self.vm_dir.join(&vm.vm_id));
            let state = self
                .shipped
                .get(&vm.vm_id)
                .copied()
                .unwrap_or(Shipped::At(0));
            let report = ship(
                &self.api,
                &credentials,
                &vm.run_id,
                &paths.console_log(),
                state,
            )
            .await;
            if let Some(size) = report.size {
                if self.sized.get(&vm.vm_id) != Some(&size) {
                    match control::resize(&paths.control(), size.0, size.1) {
                        Ok(()) => {
                            self.sized.insert(vm.vm_id.clone(), size);
                        }
                        Err(err) => {
                            tracing::debug!(error = %err, run_id = vm.run_id, "console stays its size")
                        }
                    }
                }
            }
            self.shipped.insert(vm.vm_id, report.shipped);
        }
        Ok(())
    }
}

/// Sends what the API does not hold yet; its reply moves the offset, backwards
/// too after a restart, and carries the size the viewer wants. A quiet VM still
/// reports once a tick, because the reply is the only way that size arrives.
pub async fn ship<A: Api>(
    api: &A,
    credentials: &Credentials,
    run_id: &str,
    log: &Path,
    state: Shipped,
) -> Report {
    let Shipped::At(mut offset) = state else {
        return Report {
            shipped: state,
            size: None,
        };
    };
    let mut size = None;
    let mut buffer = vec![0u8; CHUNK];
    for _ in 0..CHUNKS_PER_TICK {
        let read = match read_at(log, offset, &mut buffer) {
            Ok(read) => read,
            Err(err) => {
                tracing::warn!(error = %err, run_id, "cannot read the console log");
                break;
            }
        };
        match api
            .report_console(credentials, run_id, offset, &buffer[..read])
            .await
        {
            Ok(reply) => {
                size = reply.cols.zip(reply.rows);
                if reply.offset == offset {
                    break;
                }
                offset = reply.offset;
            }
            Err(AgentError::Api { status: 413, .. }) => {
                tracing::warn!(
                    run_id,
                    offset,
                    "the API holds no more console output for this run"
                );
                return Report {
                    shipped: Shipped::Stopped,
                    size,
                };
            }
            Err(AgentError::Conflict(detail)) => {
                tracing::warn!(
                    run_id,
                    detail,
                    "the API refused console output for this run"
                );
                return Report {
                    shipped: Shipped::Stopped,
                    size,
                };
            }
            Err(err) => {
                tracing::debug!(error = %err, run_id, "console output stays unshipped");
                break;
            }
        }
        if read == 0 {
            break;
        }
    }
    Report {
        shipped: Shipped::At(offset),
        size,
    }
}

pub(crate) fn read_at(log: &Path, offset: u64, buffer: &mut [u8]) -> io::Result<usize> {
    let mut file = match File::open(log) {
        Ok(file) => file,
        Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(0),
        Err(err) => return Err(err),
    };
    file.seek(SeekFrom::Start(offset))?;
    file.read(buffer)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::libs::testing::FakeApi;

    fn log(content: &[u8]) -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("console.log");
        std::fs::write(&path, content).expect("log");
        (dir, path)
    }

    #[tokio::test]
    async fn the_whole_log_is_shipped_in_chunks() {
        let content: Vec<u8> = (0..CHUNK * 2 + 10).map(|at| (at % 251) as u8).collect();
        let (_dir, path) = log(&content);
        let api = FakeApi::default();

        let report = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(report.shipped, Shipped::At(content.len() as u64));
        assert_eq!(api.console("run_a"), content);
    }

    #[tokio::test]
    async fn a_restarted_runner_resumes_where_the_api_stands() {
        let (_dir, path) = log(b"login: naos\r\n$ ");
        let api = FakeApi::default();
        api.hold_console("run_a", b"login: ");

        let report = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(report.shipped, Shipped::At(15));
        assert_eq!(api.console("run_a"), b"login: naos\r\n$ ");
    }

    #[tokio::test]
    async fn an_api_behind_the_runner_gets_the_gap_again() {
        let (_dir, path) = log(b"login: naos\r\n");
        let api = FakeApi::default();

        let report = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(7)).await;

        assert_eq!(report.shipped, Shipped::At(13));
        assert_eq!(api.console("run_a"), b"login: naos\r\n");
    }

    #[tokio::test]
    async fn a_full_log_stops_shipping_the_run() {
        let (_dir, path) = log(b"login: naos\r\n");
        let api = FakeApi::default();
        api.limit_console(5);

        let report = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(report.shipped, Shipped::Stopped);
        assert_eq!(api.console("run_a"), b"login");
    }

    #[tokio::test]
    async fn the_size_the_viewer_wants_rides_back_on_the_reply() {
        let (_dir, path) = log(b"login: naos\r\n");
        let api = FakeApi::default();
        api.want_console_size(190, 44);

        let report = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(report.size, Some((190, 44)));
    }

    #[tokio::test]
    async fn a_quiet_vm_still_hears_the_size() {
        let (_dir, path) = log(b"");
        let api = FakeApi::default();
        api.want_console_size(120, 30);

        let report = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(report.shipped, Shipped::At(0));
        assert_eq!(report.size, Some((120, 30)));
    }

    #[tokio::test]
    async fn a_vm_without_a_log_ships_nothing() {
        let dir = tempfile::tempdir().expect("tempdir");
        let api = FakeApi::default();

        let report = ship(
            &api,
            &api.credentials(),
            "run_a",
            &dir.path().join("console.log"),
            Shipped::At(0),
        )
        .await;

        assert_eq!(report.shipped, Shipped::At(0));
        assert!(api.console("run_a").is_empty());
    }
}
