use std::os::unix::fs::FileExt;
use std::path::Path;

use serde::{Deserialize, Serialize};

use super::ext4::{corrupt, S_IFREG};
use super::host::Host;
use super::merge::{lookup, parts};
use super::{open_upper, Change, Entry, Kind};
use crate::libs::error::AgentError;

/// The most one range carries; the viewer asks again for the next one.
pub const MAX_RANGE: u64 = 256 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Side {
    Host,
    Agent,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Piece {
    /// The side's whole size, so the viewer knows when it has read all of it.
    pub size: u64,
    pub data: Vec<u8>,
}

/// Bytes of one side of a collected file entry: the agent's from the upper disk, the host's
/// from the workspace. `None` when that side has no such file, as a created entry on the host.
pub fn read(
    upper: &Path,
    workspace: Option<&Path>,
    entry: &Entry,
    side: Side,
    offset: u64,
    length: u64,
) -> Result<Option<Piece>, AgentError> {
    if entry.kind != Kind::File || entry.change == Change::Rejected {
        return Err(AgentError::Runtime(format!(
            "{} has no content",
            entry.path
        )));
    }
    let path = match (side, entry.change) {
        (Side::Agent, Change::Deleted) | (Side::Host, Change::Created) => return Ok(None),
        (Side::Host, Change::Renamed) => entry.from.as_deref(),
        _ => Some(entry.path.as_str()),
    };
    let path = path
        .and_then(parts)
        .ok_or_else(|| corrupt("an entry path that is not plainly relative"))?;
    let length = length.min(MAX_RANGE);
    match side {
        Side::Agent => {
            let (fs, data) = open_upper(upper)?;
            let inode = lookup(&fs, &data, &path)?;
            if inode.kind() != S_IFREG || inode.links > 1 {
                return Err(corrupt(format!(
                    "{} is not a file on the upper disk",
                    entry.path
                )));
            }
            Ok(Some(Piece {
                size: inode.size,
                data: fs.read_range(&inode, offset, length)?,
            }))
        }
        Side::Host => {
            let workspace =
                workspace.ok_or_else(|| AgentError::Runtime("the run has no workspace".into()))?;
            let Some(file) = Host::open(workspace)?.file(&path)? else {
                return Ok(None);
            };
            let size = file.metadata()?.len();
            let wanted = size.saturating_sub(offset).min(length);
            let mut data = vec![0u8; usize::try_from(wanted).map_err(corrupt)?];
            let mut read = 0;
            while read < data.len() {
                match file.read_at(&mut data[read..], offset + read as u64)? {
                    0 => break,
                    count => read += count,
                }
            }
            data.truncate(read);
            Ok(Some(Piece { size, data }))
        }
    }
}
