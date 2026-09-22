use std::io::Write;
use std::os::unix::net::UnixStream;
use std::path::Path;

use crate::libs::error::AgentError;

/// Tells the guest how big the viewer's grid is. The serial console carries no
/// resize signal, so the size travels over its own virtio port instead.
pub fn resize(socket: &Path, cols: u16, rows: u16) -> Result<(), AgentError> {
    let mut stream = UnixStream::connect(socket)?;
    writeln!(stream, "{{\"cols\":{cols},\"rows\":{rows}}}")?;
    stream.flush()?;
    Ok(())
}
