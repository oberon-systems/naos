mod ext4;
mod host;

use std::collections::{BTreeMap, HashSet};
use std::fs::OpenOptions;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use nix::fcntl::OFlag;
use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::libs::error::AgentError;
use crate::libs::ids::hex;
use ext4::{corrupt, Ext4, Inode, ROOT_INODE, S_IFCHR, S_IFDIR, S_IFLNK, S_IFREG, TRUSTED_INDEX};
use host::{Host, Node};

const MAX_DEPTH: usize = 256;
const MAX_ENTRIES: usize = 100_000;
const MAX_PATH: usize = 4096;
const DATA_DIR: &[u8] = b"data";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Change {
    Deleted,
    Created,
    Modified,
    Renamed,
    Rejected,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    File,
    Dir,
    Symlink,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Entry {
    pub path: String,
    pub change: Change,
    pub kind: Kind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<&'static str>,
}

impl Entry {
    fn new(path: String, change: Change, kind: Kind) -> Self {
        Self {
            path,
            change,
            kind,
            from: None,
            size: None,
            sha256: None,
            mode: None,
            target: None,
            reason: None,
        }
    }
}

#[derive(Debug, Default, Clone, PartialEq, Eq, Serialize)]
pub struct Diff {
    pub entries: Vec<Entry>,
}

impl Diff {
    pub fn rejected(&self) -> usize {
        self.entries
            .iter()
            .filter(|entry| entry.change == Change::Rejected)
            .count()
    }
}

/// Reads the upper disk of a stopped VM and compares it with the host workspace it was laid over.
pub fn collect(upper: &Path, workspace: &Path) -> Result<Diff, AgentError> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(OFlag::O_NOFOLLOW.bits())
        .open(upper)?;
    if !file.metadata()?.is_file() {
        return Err(corrupt("the upper disk is not a regular file"));
    }
    let fs = Ext4::open(file)?;
    let root = fs.inode(ROOT_INODE)?;
    let data = fs
        .read_dir(&root)?
        .into_iter()
        .find(|(name, _)| name == DATA_DIR)
        .ok_or_else(|| corrupt("no data directory"))?;
    let data = fs.inode(data.1)?;
    let mut walker = Walker {
        fs: &fs,
        host: Host::open(workspace)?,
        entries: Vec::new(),
        dirs: HashSet::new(),
        visited: 0,
    };
    let opaque = opaque(&fs, &data)?;
    walker.dir(&data, &[], 0, opaque)?;
    let mut entries = pair_renames(walker.entries);
    entries.sort_by(|a, b| (a.path.as_bytes(), a.change).cmp(&(b.path.as_bytes(), b.change)));
    Ok(Diff { entries })
}

struct Walker<'a> {
    fs: &'a Ext4,
    host: Host,
    entries: Vec<Entry>,
    dirs: HashSet<u32>,
    visited: usize,
}

impl Walker<'_> {
    fn dir(
        &mut self,
        inode: &Inode,
        path: &[Vec<u8>],
        depth: usize,
        hides_host: bool,
    ) -> Result<(), AgentError> {
        if inode.kind() != S_IFDIR {
            return Err(corrupt(format!(
                "inode {} is not a directory",
                inode.number
            )));
        }
        if !self.dirs.insert(inode.number) {
            return Err(corrupt("a directory is linked twice"));
        }
        let children = self.fs.read_dir(inode)?;
        if hides_host {
            for name in self.host.children(path)? {
                if children
                    .binary_search_by(|(child, _)| child.cmp(&name))
                    .is_err()
                {
                    self.deleted(&join(path, name), depth + 1)?;
                }
            }
        }
        for (name, number) in children {
            self.visit(&join(path, name), number, depth + 1)?;
        }
        Ok(())
    }

    fn visit(&mut self, path: &[Vec<u8>], number: u32, depth: usize) -> Result<(), AgentError> {
        self.count(depth)?;
        let inode = self.fs.inode(number)?;
        let opaque = opaque(self.fs, &inode)?;
        let kind = kind_of(inode.kind());
        let Some(display) = display(path) else {
            self.reject(lossy(path), kind, "name is not utf-8");
            return Ok(());
        };
        if display.len() > MAX_PATH {
            self.reject(display, kind, "path too long");
            return Ok(());
        }
        if inode.kind() == S_IFCHR && inode.rdev() == (0, 0) {
            return if self.host.exists(path)? {
                self.deleted(path, depth)
            } else {
                Ok(())
            };
        }
        match inode.kind() {
            S_IFDIR => {
                let host = self.host.node(path)?;
                match host {
                    Some(Node::Dir) => {}
                    None => self.push(Entry::new(display, Change::Created, Kind::Dir)),
                    Some(_) => {
                        self.deleted(path, depth)?;
                        self.push(Entry::new(display, Change::Created, Kind::Dir));
                    }
                }
                self.dir(&inode, path, depth, opaque && host == Some(Node::Dir))
            }
            S_IFREG | S_IFLNK if inode.links > 1 => {
                self.reject(display, kind, "hardlink");
                Ok(())
            }
            S_IFREG if inode.mode & 0o7000 != 0 => {
                self.reject(display, kind, "special permission bits");
                Ok(())
            }
            S_IFREG if inode.size > self.fs.image_len() => {
                self.reject(display, kind, "larger than the upper disk");
                Ok(())
            }
            S_IFREG => {
                let mut digest = Sha256::new();
                self.fs.read_data(&inode, |chunk| {
                    digest.update(chunk);
                    Ok(())
                })?;
                let (size, sha256) = (inode.size, hex(&digest.finalize()));
                let mode = u32::from(inode.mode & 0o777);
                let change = match self.host.node(path)? {
                    Some(Node::File {
                        mode: host_mode,
                        sha256: host_sha,
                        ..
                    }) if host_sha == sha256 && host_mode == mode => return Ok(()),
                    Some(Node::File { .. }) => Change::Modified,
                    None => Change::Created,
                    Some(_) => {
                        self.deleted(path, depth)?;
                        Change::Created
                    }
                };
                self.push(Entry {
                    size: Some(size),
                    sha256: Some(sha256),
                    mode: Some(mode),
                    ..Entry::new(display, change, Kind::File)
                });
                Ok(())
            }
            S_IFLNK => {
                let target = self.fs.read_link(&inode)?;
                let Ok(text) = String::from_utf8(target.clone()) else {
                    self.reject(display, kind, "symlink target is not utf-8");
                    return Ok(());
                };
                let change = match self.host.node(path)? {
                    Some(Node::Symlink(host_target)) if host_target == target => return Ok(()),
                    Some(Node::Symlink(_)) => Change::Modified,
                    None => Change::Created,
                    Some(_) => {
                        self.deleted(path, depth)?;
                        Change::Created
                    }
                };
                self.push(Entry {
                    target: Some(text),
                    ..Entry::new(display, change, Kind::Symlink)
                });
                Ok(())
            }
            _ => {
                self.reject(display, kind, "special file");
                Ok(())
            }
        }
    }

    /// Everything the host has at `path`, a directory with all it holds.
    fn deleted(&mut self, path: &[Vec<u8>], depth: usize) -> Result<(), AgentError> {
        self.count(depth)?;
        let Some(node) = self.host.node(path)? else {
            return Ok(());
        };
        let kind = match &node {
            Node::File { .. } => Kind::File,
            Node::Dir => Kind::Dir,
            Node::Symlink(_) => Kind::Symlink,
            Node::Other => Kind::Other,
        };
        let Some(display) = display(path) else {
            self.reject(lossy(path), kind, "name is not utf-8");
            return Ok(());
        };
        let mut entry = Entry::new(display, Change::Deleted, kind);
        match node {
            Node::File { size, mode, sha256 } => {
                entry.size = Some(size);
                entry.mode = Some(mode);
                entry.sha256 = Some(sha256);
            }
            Node::Symlink(target) => entry.target = String::from_utf8(target).ok(),
            Node::Dir => {
                for name in self.host.children(path)? {
                    self.deleted(&join(path, name), depth + 1)?;
                }
            }
            Node::Other => {}
        }
        self.push(entry);
        Ok(())
    }

    fn count(&mut self, depth: usize) -> Result<(), AgentError> {
        self.visited += 1;
        if depth > MAX_DEPTH {
            return Err(corrupt("the workspace is nested too deep"));
        }
        if self.visited > MAX_ENTRIES {
            return Err(corrupt("too many entries"));
        }
        Ok(())
    }

    fn reject(&mut self, path: String, kind: Kind, reason: &'static str) {
        self.push(Entry {
            reason: Some(reason),
            ..Entry::new(path, Change::Rejected, kind)
        });
    }

    fn push(&mut self, entry: Entry) {
        self.entries.push(entry);
    }
}

/// Overlay attributes this format never writes fail the collection; `opaque` hides the host directory.
fn opaque(fs: &Ext4, inode: &Inode) -> Result<bool, AgentError> {
    let mut opaque = false;
    for xattr in fs.xattrs(inode)? {
        let Some(name) = xattr
            .name
            .strip_prefix(b"overlay.")
            .filter(|_| xattr.index == TRUSTED_INDEX)
        else {
            continue;
        };
        match (name, xattr.value.as_slice()) {
            (b"opaque", b"y") if inode.kind() == S_IFDIR => opaque = true,
            (b"origin" | b"impure" | b"nlink" | b"upper" | b"uuid" | b"protattr", _) => {}
            _ => {
                return Err(corrupt(format!(
                    "overlay attribute {} is not supported",
                    String::from_utf8_lossy(name)
                )))
            }
        }
    }
    Ok(opaque)
}

/// A deleted and a created file with the same content are a rename when neither side is ambiguous.
fn pair_renames(entries: Vec<Entry>) -> Vec<Entry> {
    let key = |entry: &Entry| {
        (entry.kind == Kind::File && entry.size.is_some_and(|size| size > 0))
            .then(|| (entry.size, entry.sha256.clone()))
    };
    let mut sides: BTreeMap<_, (Vec<usize>, Vec<usize>)> = BTreeMap::new();
    for (index, entry) in entries.iter().enumerate() {
        let Some(key) = key(entry) else { continue };
        match entry.change {
            Change::Deleted => sides.entry(key).or_default().0.push(index),
            Change::Created => sides.entry(key).or_default().1.push(index),
            _ => {}
        }
    }
    let mut renamed_from = BTreeMap::new();
    for (deleted, created) in sides.into_values() {
        if let ([gone], [new]) = (deleted.as_slice(), created.as_slice()) {
            renamed_from.insert(*new, *gone);
        }
    }
    let gone: HashSet<usize> = renamed_from.values().copied().collect();
    let mut result = Vec::with_capacity(entries.len());
    for (index, entry) in entries.iter().enumerate() {
        if gone.contains(&index) {
            continue;
        }
        let mut entry = entry.clone();
        if let Some(from) = renamed_from.get(&index) {
            entry.change = Change::Renamed;
            entry.from = Some(entries[*from].path.clone());
        }
        result.push(entry);
    }
    result
}

fn kind_of(mode: u16) -> Kind {
    match mode {
        S_IFREG => Kind::File,
        S_IFDIR => Kind::Dir,
        S_IFLNK => Kind::Symlink,
        _ => Kind::Other,
    }
}

fn join(path: &[Vec<u8>], name: Vec<u8>) -> Vec<Vec<u8>> {
    let mut joined = path.to_vec();
    joined.push(name);
    joined
}

fn display(path: &[Vec<u8>]) -> Option<String> {
    let parts: Option<Vec<&str>> = path
        .iter()
        .map(|part| std::str::from_utf8(part).ok())
        .collect();
    parts.map(|parts| parts.join("/"))
}

fn lossy(path: &[Vec<u8>]) -> String {
    path.iter()
        .map(|part| String::from_utf8_lossy(part))
        .collect::<Vec<_>>()
        .join("/")
}

#[cfg(test)]
mod tests;
