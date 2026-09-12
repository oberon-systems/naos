use std::io::{self, IsTerminal, Read, Write};
use std::net::Shutdown;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::thread;

use nix::sys::termios::{cfmakeraw, tcgetattr, tcsetattr, SetArg, Termios};

use crate::libs::audit;
use crate::libs::error::AgentError;
use crate::libs::qemu::VmPaths;
use crate::libs::runtime::scan;

const DETACH: u8 = 0x1d;
const BUFFER: usize = 4096;

struct RawMode(Termios);

impl RawMode {
    fn enter() -> Result<Option<Self>, AgentError> {
        let stdin = io::stdin();
        if !stdin.is_terminal() {
            return Ok(None);
        }
        let terminal = |err: nix::Error| AgentError::Runtime(format!("terminal: {err}"));
        let saved = tcgetattr(&stdin).map_err(terminal)?;
        let mut raw = saved.clone();
        cfmakeraw(&mut raw);
        tcsetattr(&stdin, SetArg::TCSANOW, &raw).map_err(terminal)?;
        Ok(Some(Self(saved)))
    }
}

impl Drop for RawMode {
    fn drop(&mut self) {
        let _ = tcsetattr(io::stdin(), SetArg::TCSANOW, &self.0);
    }
}

pub fn attach(vm_dir: &Path, run_id: &str) -> Result<(), AgentError> {
    let vm = scan(vm_dir)?
        .into_iter()
        .find(|vm| vm.run_id == run_id && vm.running)
        .ok_or_else(|| {
            AgentError::Runtime(format!("run {run_id} has no running VM on this runner"))
        })?;
    let socket = UnixStream::connect(VmPaths::new(vm_dir.join(&vm.vm_id)).console())?;
    audit::console_attached(&vm.vm_id, run_id);
    eprintln!("attached to {run_id} ({}), detach with Ctrl-]", vm.vm_id);

    let _raw = RawMode::enter()?;
    let mut from_vm = socket.try_clone()?;
    thread::spawn(move || {
        let mut stdout = io::stdout();
        let mut buffer = [0u8; BUFFER];
        while let Ok(read) = from_vm.read(&mut buffer) {
            if read == 0
                || stdout
                    .write_all(&buffer[..read])
                    .and_then(|()| stdout.flush())
                    .is_err()
            {
                break;
            }
        }
    });
    pump(io::stdin().lock(), &socket)
}

fn pump(mut input: impl Read, socket: &UnixStream) -> Result<(), AgentError> {
    let mut to_vm = socket;
    let mut buffer = [0u8; BUFFER];
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        let chunk = &buffer[..read];
        if let Some(at) = chunk.iter().position(|byte| *byte == DETACH) {
            to_vm.write_all(&chunk[..at])?;
            break;
        }
        to_vm.write_all(chunk)?;
    }
    let _ = socket.shutdown(Shutdown::Both);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detach_key_ends_the_session_without_forwarding_the_rest() {
        let (near, mut far) = UnixStream::pair().expect("pair");

        pump(&b"uptime\r\x1dpoweroff\r"[..], &near).expect("pump");

        let mut received = Vec::new();
        far.read_to_end(&mut received).expect("read");
        assert_eq!(received, b"uptime\r");
    }

    #[test]
    fn attaching_to_a_run_without_a_vm_fails() {
        let dir = tempfile::tempdir().expect("tempdir");

        let outcome = attach(dir.path(), "run_a");

        assert!(matches!(outcome, Err(AgentError::Runtime(_))));
    }
}
