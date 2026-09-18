use std::ffi::OsString;
use std::fs::File;
use std::io::Read;
use std::os::fd::{AsFd, OwnedFd};
use std::os::unix::ffi::OsStringExt;
use std::os::unix::fs::MetadataExt;
use std::path::Path;

use nix::dir::Dir;
use nix::errno::Errno;
use nix::fcntl::{openat, readlinkat, AtFlags, OFlag};
use nix::sys::stat::{fstatat, Mode, SFlag};
use sha2::{Digest, Sha256};

use crate::libs::error::AgentError;
use crate::libs::ids::hex;

const DIR_FLAGS: OFlag = OFlag::O_RDONLY
    .union(OFlag::O_DIRECTORY)
    .union(OFlag::O_NOFOLLOW)
    .union(OFlag::O_CLOEXEC);

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Node {
    File {
        size: u64,
        mode: u32,
        sha256: String,
    },
    Dir,
    Symlink(Vec<u8>),
    Other,
}

/// The workspace as the host has it, reached one component at a time and never through a symlink.
pub struct Host {
    root: OwnedFd,
}

impl Host {
    pub fn open(root: &Path) -> Result<Self, AgentError> {
        let root = nix::fcntl::open(root, DIR_FLAGS, Mode::empty()).map_err(host_error)?;
        Ok(Self { root })
    }

    pub fn node(&self, path: &[Vec<u8>]) -> Result<Option<Node>, AgentError> {
        let Some((name, parents)) = path.split_last() else {
            return Ok(Some(Node::Dir));
        };
        let Some(parent) = self.dir(parents)? else {
            return Ok(None);
        };
        let name = OsString::from_vec(name.clone());
        let stat = match fstatat(&parent, name.as_os_str(), AtFlags::AT_SYMLINK_NOFOLLOW) {
            Ok(stat) => stat,
            Err(Errno::ENOENT | Errno::ENOTDIR) => return Ok(None),
            Err(err) => return Err(host_error(err)),
        };
        let kind = SFlag::from_bits_truncate(stat.st_mode & SFlag::S_IFMT.bits());
        Ok(Some(match kind {
            SFlag::S_IFDIR => Node::Dir,
            SFlag::S_IFLNK => Node::Symlink(
                readlinkat(&parent, name.as_os_str())
                    .map_err(host_error)?
                    .into_vec(),
            ),
            SFlag::S_IFREG => {
                let flags =
                    OFlag::O_RDONLY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC | OFlag::O_NONBLOCK;
                let mut file = File::from(
                    openat(&parent, name.as_os_str(), flags, Mode::empty()).map_err(host_error)?,
                );
                let meta = file.metadata()?;
                if !meta.is_file() || meta.ino() != stat.st_ino {
                    return Err(AgentError::Runtime(
                        "the host workspace changed while it was collected".into(),
                    ));
                }
                let (size, sha256) = hash(&mut file)?;
                Node::File {
                    size,
                    mode: meta.mode() & 0o777,
                    sha256,
                }
            }
            _ => Node::Other,
        }))
    }

    pub fn root(&self) -> Result<OwnedFd, AgentError> {
        Ok(self.root.try_clone()?)
    }

    /// The directory holding `path`, reached without following a symlink.
    pub fn parent(&self, path: &[Vec<u8>]) -> Result<Option<OwnedFd>, AgentError> {
        match path.split_last() {
            Some((_, parents)) => self.dir(parents),
            None => Ok(None),
        }
    }

    pub fn exists(&self, path: &[Vec<u8>]) -> Result<bool, AgentError> {
        let Some((name, parents)) = path.split_last() else {
            return Ok(true);
        };
        let Some(parent) = self.dir(parents)? else {
            return Ok(false);
        };
        let name = OsString::from_vec(name.clone());
        match fstatat(&parent, name.as_os_str(), AtFlags::AT_SYMLINK_NOFOLLOW) {
            Ok(_) => Ok(true),
            Err(Errno::ENOENT | Errno::ENOTDIR) => Ok(false),
            Err(err) => Err(host_error(err)),
        }
    }

    pub fn children(&self, path: &[Vec<u8>]) -> Result<Vec<Vec<u8>>, AgentError> {
        let Some(dir) = self.dir(path)? else {
            return Ok(Vec::new());
        };
        let mut names: Vec<Vec<u8>> = Dir::from_fd(dir)
            .map_err(host_error)?
            .iter()
            .map(|entry| entry.map(|entry| entry.file_name().to_bytes().to_vec()))
            .collect::<Result<_, _>>()
            .map_err(host_error)?;
        names.retain(|name| name != b"." && name != b"..");
        names.sort();
        Ok(names)
    }

    fn dir(&self, path: &[Vec<u8>]) -> Result<Option<OwnedFd>, AgentError> {
        let mut current = self.root.try_clone()?;
        for name in path {
            let name = OsString::from_vec(name.clone());
            current = match openat(current.as_fd(), name.as_os_str(), DIR_FLAGS, Mode::empty()) {
                Ok(fd) => fd,
                Err(Errno::ENOENT | Errno::ENOTDIR | Errno::ELOOP) => return Ok(None),
                Err(err) => return Err(host_error(err)),
            };
        }
        Ok(Some(current))
    }
}

fn hash(reader: &mut impl Read) -> Result<(u64, String), AgentError> {
    let mut digest = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    let mut size = 0u64;
    loop {
        let read = reader.read(&mut buffer)?;
        if read == 0 {
            return Ok((size, hex(&digest.finalize())));
        }
        digest.update(&buffer[..read]);
        size += read as u64;
    }
}

fn host_error(err: Errno) -> AgentError {
    AgentError::Io(std::io::Error::from(err))
}
