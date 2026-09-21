use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::libs::api::Api;
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

pub struct Shipper<A> {
    api: A,
    store: CredentialStore,
    vm_dir: PathBuf,
    shipped: HashMap<String, Shipped>,
}

impl<A: Api> Shipper<A> {
    pub fn new(api: A, store: CredentialStore, vm_dir: PathBuf) -> Self {
        Self {
            api,
            store,
            vm_dir,
            shipped: HashMap::new(),
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
        for vm in running {
            let log = VmPaths::new(self.vm_dir.join(&vm.vm_id)).console_log();
            let state = self
                .shipped
                .get(&vm.vm_id)
                .copied()
                .unwrap_or(Shipped::At(0));
            let next = ship(&self.api, &credentials, &vm.run_id, &log, state).await;
            self.shipped.insert(vm.vm_id, next);
        }
        Ok(())
    }
}

/// Sends what the API does not hold yet; its reply moves the offset, backwards too after a restart.
pub async fn ship<A: Api>(
    api: &A,
    credentials: &Credentials,
    run_id: &str,
    log: &Path,
    state: Shipped,
) -> Shipped {
    let Shipped::At(mut offset) = state else {
        return state;
    };
    let mut buffer = vec![0u8; CHUNK];
    for _ in 0..CHUNKS_PER_TICK {
        let read = match read_at(log, offset, &mut buffer) {
            Ok(0) => break,
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
            Ok(reply) if reply.offset == offset => break,
            Ok(reply) => offset = reply.offset,
            Err(AgentError::Api { status: 413, .. }) => {
                tracing::warn!(
                    run_id,
                    offset,
                    "the API holds no more console output for this run"
                );
                return Shipped::Stopped;
            }
            Err(AgentError::Conflict(detail)) => {
                tracing::warn!(
                    run_id,
                    detail,
                    "the API refused console output for this run"
                );
                return Shipped::Stopped;
            }
            Err(err) => {
                tracing::debug!(error = %err, run_id, "console output stays unshipped");
                break;
            }
        }
    }
    Shipped::At(offset)
}

fn read_at(log: &Path, offset: u64, buffer: &mut [u8]) -> io::Result<usize> {
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

        let state = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(state, Shipped::At(content.len() as u64));
        assert_eq!(api.console("run_a"), content);
    }

    #[tokio::test]
    async fn a_restarted_runner_resumes_where_the_api_stands() {
        let (_dir, path) = log(b"login: naos\r\n$ ");
        let api = FakeApi::default();
        api.hold_console("run_a", b"login: ");

        let state = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(state, Shipped::At(15));
        assert_eq!(api.console("run_a"), b"login: naos\r\n$ ");
    }

    #[tokio::test]
    async fn an_api_behind_the_runner_gets_the_gap_again() {
        let (_dir, path) = log(b"login: naos\r\n");
        let api = FakeApi::default();

        let state = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(7)).await;

        assert_eq!(state, Shipped::At(13));
        assert_eq!(api.console("run_a"), b"login: naos\r\n");
    }

    #[tokio::test]
    async fn a_full_log_stops_shipping_the_run() {
        let (_dir, path) = log(b"login: naos\r\n");
        let api = FakeApi::default();
        api.limit_console(5);

        let state = ship(&api, &api.credentials(), "run_a", &path, Shipped::At(0)).await;

        assert_eq!(state, Shipped::Stopped);
        assert_eq!(api.console("run_a"), b"login");
    }

    #[tokio::test]
    async fn a_vm_without_a_log_ships_nothing() {
        let dir = tempfile::tempdir().expect("tempdir");
        let api = FakeApi::default();

        let state = ship(
            &api,
            &api.credentials(),
            "run_a",
            &dir.path().join("console.log"),
            Shipped::At(0),
        )
        .await;

        assert_eq!(state, Shipped::At(0));
        assert!(api.console("run_a").is_empty());
    }
}
