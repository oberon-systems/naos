use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions, Permissions};
use std::io::{BufRead, BufReader, ErrorKind, Write};
use std::os::fd::{AsFd, OwnedFd};
use std::os::unix::ffi::OsStringExt;
use std::os::unix::fs::{symlink, OpenOptionsExt, PermissionsExt};
use std::path::Path;

use nix::errno::Errno;
use nix::fcntl::{openat, renameat2, AtFlags, OFlag, RenameFlags};
use nix::sys::stat::{fstatat, mkdirat, Mode};
use nix::unistd::{symlinkat, unlinkat, UnlinkatFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::ext4::{corrupt, Ext4, Inode, S_IFLNK, S_IFREG};
use super::host::{Host, Node};
use super::{open_upper, Change, Diff, Entry, Kind};
use crate::libs::error::AgentError;
use crate::libs::ids::{hex, random_hex};

const STAGING_PREFIX: &str = ".naos-merge-";
const JOURNAL: &str = "journal";
const RESULT: &str = "result.json";
const BACKUP: &str = "backup";
const EXPORT: &str = "export";
const DIR_MODE: u32 = 0o755;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Decision {
    pub paths: Vec<String>,
    #[serde(default)]
    pub resolutions: BTreeMap<String, Resolution>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Resolution {
    Skip,
    Take,
    Export,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Conflict {
    pub path: String,
    pub reason: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Report {
    pub applied: Vec<String>,
    pub skipped: Vec<String>,
    pub exported: Vec<String>,
    pub backed_up: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum Outcome {
    Applied(Report),
    Conflict { conflicts: Vec<Conflict> },
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum Expect {
    Absent,
    AbsentOrDir,
    Dir,
    File { sha256: String, mode: u32 },
    Symlink(String),
    Other,
    Any,
}

impl Expect {
    fn of_deleted(entry: &Entry) -> Self {
        match entry.kind {
            Kind::File => Self::File {
                sha256: entry.sha256.clone().unwrap_or_default(),
                mode: entry.mode.unwrap_or_default(),
            },
            Kind::Dir => Self::Dir,
            Kind::Symlink => Self::Symlink(entry.target.clone().unwrap_or_default()),
            Kind::Other => Self::Other,
        }
    }

    fn holds(&self, node: Option<&Node>) -> bool {
        match (self, node) {
            (Self::Any, _) | (Self::Absent | Self::AbsentOrDir, None) => true,
            (Self::Dir | Self::AbsentOrDir, Some(Node::Dir)) | (Self::Other, Some(Node::Other)) => {
                true
            }
            (
                Self::File { sha256, mode },
                Some(Node::File {
                    sha256: s, mode: m, ..
                }),
            ) => sha256 == s && mode == m,
            (Self::Symlink(target), Some(Node::Symlink(t))) => target.as_bytes() == t.as_slice(),
            _ => false,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Action {
    Remove,
    Mkdir,
    Place(usize),
}

#[derive(Debug, Clone)]
struct Op<'a> {
    path: &'a str,
    owner: &'a str,
    action: Action,
    expect: Expect,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Step {
    Staging(String),
    Moved { path: String, to: String },
    Made { path: String },
    Placed { path: String, from: String },
    Commit(Report),
}

/// Applies the selected part of a collected diff to the workspace, all of it or none of it.
///
/// `dir` is the Run's own merge directory: it holds the journal, the host's replaced entries under
/// `backup/`, the agent's exported versions under `export/` and the final `result.json`.
pub fn merge(
    upper: &Path,
    workspace: Option<&Path>,
    diff: &Diff,
    decision: &Decision,
    dir: &Path,
) -> Result<Outcome, AgentError> {
    fs::create_dir_all(dir)?;
    if let Ok(raw) = fs::read(dir.join(RESULT)) {
        return Ok(Outcome::Applied(
            serde_json::from_slice(&raw).map_err(corrupt)?,
        ));
    }
    if let Some(steps) = read_journal(dir)? {
        let workspace = workspace.ok_or_else(|| corrupt("a journal without a workspace"))?;
        if let Some(Step::Commit(report)) = steps.last() {
            return finalize(workspace, dir, &steps, report.clone()).map(Outcome::Applied);
        }
        rollback(&Host::open(workspace)?, workspace, &steps)?;
        fs::remove_file(dir.join(JOURNAL))?;
    }

    let (mut ops, mut report) = match plan(diff, decision) {
        Ok(planned) => planned,
        Err(conflicts) => return Ok(Outcome::Conflict { conflicts }),
    };
    let exports: Vec<usize> = take_exports(&mut ops, decision, &mut report);
    if ops.is_empty() && exports.is_empty() {
        return finish(dir, report);
    }
    let workspace = workspace.ok_or_else(|| corrupt("a merge without a workspace"))?;
    let host = Host::open(workspace)?;
    let conflicts = precheck(&host, &mut ops, decision)?;
    if !conflicts.is_empty() {
        return Ok(Outcome::Conflict { conflicts });
    }

    let (fs_upper, data) = open_upper(upper)?;
    for &index in &exports {
        export(&fs_upper, &data, &diff.entries[index], &dir.join(EXPORT))?;
    }
    if ops.is_empty() {
        return finish(dir, report);
    }
    ops.sort_by(|a, b| {
        let by_path = if a.action == Action::Remove {
            b.path.cmp(a.path)
        } else {
            a.path.cmp(b.path)
        };
        rank(a).cmp(&rank(b)).then(by_path)
    });
    let mut applier = Applier::start(host, dir)?;
    for op in &ops {
        if let Action::Place(index) = op.action {
            applier.stage(&fs_upper, &data, &diff.entries[index], index)?;
        }
    }
    let mut steps = vec![Step::Staging(applier.staging.clone())];
    for op in &ops {
        if let Err(reason) = applier.apply(op, &mut steps, &mut report) {
            rollback(&applier.host, workspace, &steps)?;
            fs::remove_file(dir.join(JOURNAL))?;
            return Ok(Outcome::Conflict {
                conflicts: vec![Conflict {
                    path: op.owner.to_owned(),
                    reason,
                }],
            });
        }
    }
    report.applied = ops
        .iter()
        .map(|op| op.owner.to_owned())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    applier.record(&Step::Commit(report.clone()))?;
    steps.push(Step::Commit(report.clone()));
    finalize(workspace, dir, &steps, report).map(Outcome::Applied)
}

/// Removals deepest first, then new directories top down, then files.
fn rank(op: &Op<'_>) -> u8 {
    match op.action {
        Action::Remove => 0,
        Action::Mkdir => 1,
        Action::Place(_) => 2,
    }
}

fn plan<'a>(
    diff: &'a Diff,
    decision: &'a Decision,
) -> Result<(Vec<Op<'a>>, Report), Vec<Conflict>> {
    let selected: BTreeSet<&str> = decision.paths.iter().map(String::as_str).collect();
    let live = |entry: &&Entry| entry.change != Change::Rejected;
    let mut conflicts = Vec::new();
    for path in &selected {
        if parts(path).is_none() || !diff.entries.iter().filter(live).any(|e| e.path == *path) {
            conflicts.push(conflict(path, "not in the diff"));
        }
    }
    for entry in diff.entries.iter().filter(live) {
        if !selected.contains(entry.path.as_str()) {
            continue;
        }
        let prefix = format!("{}/", entry.path);
        let under = |path: &str| path.starts_with(&prefix);
        if entry.change == Change::Deleted && entry.kind == Kind::Dir {
            let missing = diff.entries.iter().filter(live).find(|other| {
                let removes = match other.change {
                    Change::Deleted => Some(other.path.as_str()),
                    Change::Renamed => other.from.as_deref(),
                    _ => None,
                };
                removes.is_some_and(under) && !selected.contains(other.path.as_str())
            });
            if let Some(other) = missing {
                conflicts.push(conflict(&entry.path, &format!("needs {} too", other.path)));
            }
        }
        if let Some((parent, _)) = entry.path.rsplit_once('/') {
            let created = diff.entries.iter().filter(live).any(|other| {
                other.path == parent && other.kind == Kind::Dir && other.change == Change::Created
            });
            if created && !selected.contains(parent) {
                conflicts.push(conflict(&entry.path, &format!("needs {parent} too")));
            }
        }
    }
    if !conflicts.is_empty() {
        return Err(conflicts);
    }

    let mut ops = Vec::new();
    for (index, entry) in diff.entries.iter().enumerate() {
        if entry.change == Change::Rejected || !selected.contains(entry.path.as_str()) {
            continue;
        }
        let op = |path: &'a str, action, expect| Op {
            path,
            owner: &entry.path,
            action,
            expect,
        };
        match (entry.change, entry.kind) {
            (Change::Deleted, _) => {
                ops.push(op(&entry.path, Action::Remove, Expect::of_deleted(entry)))
            }
            (Change::Created, Kind::Dir) => {
                ops.push(op(&entry.path, Action::Mkdir, Expect::AbsentOrDir))
            }
            (Change::Created, _) => ops.push(op(&entry.path, Action::Place(index), Expect::Absent)),
            (Change::Modified, Kind::Symlink) => ops.push(op(
                &entry.path,
                Action::Place(index),
                Expect::Symlink(entry.base_target.clone().unwrap_or_default()),
            )),
            (Change::Modified, _) => ops.push(op(
                &entry.path,
                Action::Place(index),
                Expect::File {
                    sha256: entry.base_sha256.clone().unwrap_or_default(),
                    mode: entry.base_mode.unwrap_or_default(),
                },
            )),
            (Change::Renamed, _) => {
                let from = entry.from.as_deref().unwrap_or_default();
                if parts(from).is_none() {
                    return Err(vec![conflict(&entry.path, "not in the diff")]);
                }
                ops.push(op(
                    from,
                    Action::Remove,
                    Expect::File {
                        sha256: entry.sha256.clone().unwrap_or_default(),
                        mode: entry.base_mode.unwrap_or_default(),
                    },
                ));
                ops.push(op(&entry.path, Action::Place(index), Expect::Absent));
            }
            (Change::Rejected, _) => {}
        }
    }
    Ok((ops, Report::default()))
}

/// Drops the host side of skipped and exported paths; exported ones keep the entries to write out.
fn take_exports(ops: &mut Vec<Op<'_>>, decision: &Decision, report: &mut Report) -> Vec<usize> {
    let mut exports = Vec::new();
    let mut seen = BTreeSet::new();
    ops.retain(|op| match decision.resolutions.get(op.owner) {
        Some(Resolution::Skip) => {
            if seen.insert(op.owner) {
                report.skipped.push(op.owner.to_owned());
            }
            false
        }
        Some(Resolution::Export) => {
            if seen.insert(op.owner) {
                report.exported.push(op.owner.to_owned());
            }
            if let Action::Place(index) = op.action {
                exports.push(index);
            }
            false
        }
        _ => true,
    });
    exports
}

/// Checks every selected entry against the host before anything is written.
fn precheck(
    host: &Host,
    ops: &mut [Op<'_>],
    decision: &Decision,
) -> Result<Vec<Conflict>, AgentError> {
    let removed: BTreeSet<&str> = ops
        .iter()
        .filter(|op| op.action == Action::Remove)
        .map(|op| op.path)
        .collect();
    let mut conflicts: BTreeMap<&str, String> = BTreeMap::new();
    for op in ops.iter_mut() {
        if decision.resolutions.get(op.owner) == Some(&Resolution::Take) {
            op.expect = Expect::Any;
            continue;
        }
        if op.action != Action::Remove && removed.contains(op.path) {
            continue;
        }
        let node = host.node(&parts(op.path).unwrap_or_default())?;
        if !op.expect.holds(node.as_ref()) {
            let reason = match (&op.expect, node) {
                (Expect::Absent | Expect::AbsentOrDir, Some(_)) => "the host has an entry here",
                (_, None) => "the host no longer has this entry",
                _ => "the host changed since collection",
            };
            conflicts.entry(op.owner).or_insert_with(|| reason.into());
        }
    }
    Ok(conflicts
        .into_iter()
        .map(|(path, reason)| Conflict {
            path: path.to_owned(),
            reason,
        })
        .collect())
}

struct Applier {
    host: Host,
    staging: String,
    staging_fd: OwnedFd,
    journal: File,
}

impl Applier {
    fn start(host: Host, dir: &Path) -> Result<Self, AgentError> {
        let staging = format!("{STAGING_PREFIX}{}", random_hex(8)?);
        let journal = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(dir.join(JOURNAL))?;
        let mut journal = journal;
        write_step(&mut journal, &Step::Staging(staging.clone()))?;
        let root = host.root()?;
        mkdirat(&root, staging.as_str(), Mode::from_bits_truncate(0o700)).map_err(errno)?;
        let staging_fd =
            openat(&root, staging.as_str(), dir_flags(), Mode::empty()).map_err(errno)?;
        Ok(Self {
            host,
            staging,
            staging_fd,
            journal,
        })
    }

    fn record(&mut self, step: &Step) -> Result<(), AgentError> {
        write_step(&mut self.journal, step)
    }

    fn stage(
        &self,
        fs: &Ext4,
        data: &Inode,
        entry: &Entry,
        index: usize,
    ) -> Result<(), AgentError> {
        let name = format!("w{index}");
        let inode = lookup(fs, data, &parts(&entry.path).unwrap_or_default())?;
        if entry.kind == Kind::Symlink {
            let target = checked_target(fs, &inode, entry)?;
            return symlinkat(target.as_str(), &self.staging_fd, name.as_str()).map_err(errno);
        }
        let flags =
            OFlag::O_WRONLY | OFlag::O_CREAT | OFlag::O_EXCL | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC;
        let fd = openat(
            &self.staging_fd,
            name.as_str(),
            flags,
            Mode::from_bits_truncate(0o600),
        )
        .map_err(errno)?;
        write_checked(fs, &inode, entry, &mut File::from(fd))
    }

    /// One host change; an `Err` is the reason the whole merge is rolled back.
    fn apply(
        &mut self,
        op: &Op<'_>,
        steps: &mut Vec<Step>,
        report: &mut Report,
    ) -> Result<(), String> {
        self.try_apply(op, steps, report)
            .map_err(|err| err.to_string())?
    }

    fn try_apply(
        &mut self,
        op: &Op<'_>,
        steps: &mut Vec<Step>,
        report: &mut Report,
    ) -> Result<Result<(), String>, AgentError> {
        let path = parts(op.path).unwrap_or_default();
        let Some((name, _)) = path.split_last() else {
            return Ok(Err("the workspace root cannot change".into()));
        };
        let name = OsString::from_vec(name.clone());
        let Some(parent) = self.host.parent(&path)? else {
            return Ok(Err("the parent directory is gone".into()));
        };
        let node = self.host.node(&path)?;
        if !op.expect.holds(node.as_ref()) {
            return Ok(Err("the host changed while the merge ran".into()));
        }
        let keeps_dir = op.action == Action::Mkdir && node == Some(Node::Dir);
        if node.is_some() && !keeps_dir {
            let to = format!("b{}", steps.len());
            self.step(
                steps,
                Step::Moved {
                    path: op.path.to_owned(),
                    to: to.clone(),
                },
            )?;
            renameat2(
                &parent,
                name.as_os_str(),
                &self.staging_fd,
                to.as_str(),
                RenameFlags::RENAME_NOREPLACE,
            )
            .map_err(errno)?;
            report.backed_up.push(op.path.to_owned());
        }
        match op.action {
            Action::Remove => {}
            Action::Mkdir if keeps_dir => {}
            Action::Mkdir => {
                self.step(
                    steps,
                    Step::Made {
                        path: op.path.to_owned(),
                    },
                )?;
                mkdirat(
                    &parent,
                    name.as_os_str(),
                    Mode::from_bits_truncate(DIR_MODE),
                )
                .map_err(errno)?;
            }
            Action::Place(index) => {
                let from = format!("w{index}");
                self.step(
                    steps,
                    Step::Placed {
                        path: op.path.to_owned(),
                        from: from.clone(),
                    },
                )?;
                renameat2(
                    &self.staging_fd,
                    from.as_str(),
                    &parent,
                    name.as_os_str(),
                    RenameFlags::RENAME_NOREPLACE,
                )
                .map_err(errno)?;
            }
        }
        Ok(Ok(()))
    }

    fn step(&mut self, steps: &mut Vec<Step>, step: Step) -> Result<(), AgentError> {
        self.record(&step)?;
        steps.push(step);
        Ok(())
    }
}

/// Undoes whatever part of a journal reached the host, newest step first.
fn rollback(host: &Host, workspace: &Path, steps: &[Step]) -> Result<(), AgentError> {
    let Some(Step::Staging(staging)) = steps.first() else {
        return Ok(());
    };
    let root = host.root()?;
    let staging_fd = match openat(&root, staging.as_str(), dir_flags(), Mode::empty()) {
        Ok(fd) => fd,
        Err(Errno::ENOENT) => return Ok(()),
        Err(err) => return Err(errno(err)),
    };
    for step in steps.iter().rev() {
        let (path, staged) = match step {
            Step::Moved { path, to } => (path, Some(to)),
            Step::Placed { path, from } => (path, Some(from)),
            Step::Made { path } => (path, None),
            Step::Staging(_) | Step::Commit(_) => continue,
        };
        let parts = parts(path).ok_or_else(|| corrupt("a journal path is not relative"))?;
        let Some(parent) = host.parent(&parts)? else {
            continue;
        };
        let name = OsString::from_vec(parts.last().cloned().unwrap_or_default());
        match (step, staged) {
            (Step::Placed { .. }, Some(from)) => {
                if !exists(&staging_fd, from.as_str())? && exists(&parent, name.as_os_str())? {
                    renameat2(
                        &parent,
                        name.as_os_str(),
                        &staging_fd,
                        from.as_str(),
                        RenameFlags::RENAME_NOREPLACE,
                    )
                    .map_err(errno)?;
                }
            }
            (Step::Moved { .. }, Some(to)) => {
                if exists(&staging_fd, to.as_str())? {
                    renameat2(
                        &staging_fd,
                        to.as_str(),
                        &parent,
                        name.as_os_str(),
                        RenameFlags::RENAME_NOREPLACE,
                    )
                    .map_err(errno)?;
                }
            }
            _ => match unlinkat(&parent, name.as_os_str(), UnlinkatFlags::RemoveDir) {
                Ok(()) | Err(Errno::ENOENT) => {}
                Err(err) => return Err(errno(err)),
            },
        }
    }
    remove_tree(&workspace.join(staging))
}

/// Moves the replaced host entries out of the workspace and records the result.
fn finalize(
    workspace: &Path,
    dir: &Path,
    steps: &[Step],
    report: Report,
) -> Result<Report, AgentError> {
    let Some(Step::Staging(staging)) = steps.first() else {
        return Err(corrupt("a journal without its staging directory"));
    };
    let staging = workspace.join(staging);
    for step in steps {
        if let Step::Moved { path, to } = step {
            let source = staging.join(to);
            if fs::symlink_metadata(&source).is_err() {
                continue;
            }
            let target = dir.join(BACKUP).join(path);
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            copy_tree(&source, &target)?;
        }
    }
    remove_tree(&staging)?;
    let outcome = finish(dir, report)?;
    match fs::remove_file(dir.join(JOURNAL)) {
        Err(err) if err.kind() != ErrorKind::NotFound => return Err(err.into()),
        _ => {}
    }
    match outcome {
        Outcome::Applied(report) => Ok(report),
        Outcome::Conflict { .. } => unreachable!("finish only applies"),
    }
}

fn finish(dir: &Path, report: Report) -> Result<Outcome, AgentError> {
    let staged = dir.join(format!("{RESULT}.tmp"));
    fs::write(&staged, serde_json::to_vec(&report).map_err(corrupt)?)?;
    fs::rename(&staged, dir.join(RESULT))?;
    Ok(Outcome::Applied(report))
}

fn export(fs_upper: &Ext4, data: &Inode, entry: &Entry, root: &Path) -> Result<(), AgentError> {
    let target = root.join(&entry.path);
    remove_tree(&target)?;
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    let inode = lookup(fs_upper, data, &parts(&entry.path).unwrap_or_default())?;
    if entry.kind == Kind::Symlink {
        symlink(checked_target(fs_upper, &inode, entry)?, &target)?;
        return Ok(());
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .custom_flags(OFlag::O_NOFOLLOW.bits())
        .mode(0o600)
        .open(&target)?;
    write_checked(fs_upper, &inode, entry, &mut file)
}

fn write_checked(
    fs: &Ext4,
    inode: &Inode,
    entry: &Entry,
    file: &mut File,
) -> Result<(), AgentError> {
    if inode.kind() != S_IFREG || inode.links > 1 || Some(inode.size) != entry.size {
        return Err(corrupt(format!("{} changed since collection", entry.path)));
    }
    let mut digest = Sha256::new();
    fs.read_data(inode, |chunk| {
        digest.update(chunk);
        file.write_all(chunk)?;
        Ok(())
    })?;
    if Some(hex(&digest.finalize())) != entry.sha256 {
        return Err(corrupt(format!("{} changed since collection", entry.path)));
    }
    file.set_permissions(Permissions::from_mode(entry.mode.unwrap_or(0o600) & 0o777))?;
    file.sync_all()?;
    Ok(())
}

fn checked_target(fs: &Ext4, inode: &Inode, entry: &Entry) -> Result<String, AgentError> {
    let target = (inode.kind() == S_IFLNK)
        .then(|| fs.read_link(inode))
        .transpose()?
        .and_then(|target| String::from_utf8(target).ok());
    match target {
        Some(target) if Some(&target) == entry.target.as_ref() => Ok(target),
        _ => Err(corrupt(format!("{} changed since collection", entry.path))),
    }
}

fn lookup(fs: &Ext4, data: &Inode, path: &[Vec<u8>]) -> Result<Inode, AgentError> {
    let mut inode = data.clone();
    for part in path {
        let children = fs.read_dir(&inode)?;
        let number = children
            .binary_search_by(|(name, _)| name.as_slice().cmp(part))
            .map(|at| children[at].1)
            .map_err(|_| corrupt("a merged path is missing from the upper disk"))?;
        inode = fs.inode(number)?;
    }
    Ok(inode)
}

fn read_journal(dir: &Path) -> Result<Option<Vec<Step>>, AgentError> {
    let file = match File::open(dir.join(JOURNAL)) {
        Ok(file) => file,
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(None),
        Err(err) => return Err(err.into()),
    };
    let mut steps = Vec::new();
    for line in BufReader::new(file).lines() {
        // A torn last line is a step that was never taken.
        match serde_json::from_str(&line?) {
            Ok(step) => steps.push(step),
            Err(_) => break,
        }
    }
    Ok(Some(steps))
}

fn write_step(journal: &mut File, step: &Step) -> Result<(), AgentError> {
    let mut line = serde_json::to_vec(step).map_err(corrupt)?;
    line.push(b'\n');
    journal.write_all(&line)?;
    journal.sync_data()?;
    Ok(())
}

/// Copies into `target`, keeping what a directory there already holds: a directory is
/// backed up after the entries that were moved out of it.
fn copy_tree(source: &Path, target: &Path) -> Result<(), AgentError> {
    let kind = fs::symlink_metadata(source)?.file_type();
    let existing = fs::symlink_metadata(target).ok();
    if !(kind.is_dir() && existing.as_ref().is_some_and(fs::Metadata::is_dir)) {
        remove_tree(target)?;
    }
    if kind.is_symlink() {
        symlink(fs::read_link(source)?, target)?;
    } else if kind.is_dir() {
        if !existing.is_some_and(|meta| meta.is_dir()) {
            fs::create_dir(target)?;
        }
        for entry in fs::read_dir(source)? {
            let entry = entry?;
            copy_tree(&entry.path(), &target.join(entry.file_name()))?;
        }
    } else if kind.is_file() {
        fs::copy(source, target)?;
    }
    Ok(())
}

fn remove_tree(path: &Path) -> Result<(), AgentError> {
    let removed = match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_dir() => fs::remove_dir_all(path),
        Ok(_) => fs::remove_file(path),
        Err(err) => Err(err),
    };
    match removed {
        Err(err) if err.kind() != ErrorKind::NotFound => Err(err.into()),
        _ => Ok(()),
    }
}

fn exists<Fd: AsFd, P: ?Sized + nix::NixPath>(dir: Fd, name: &P) -> Result<bool, AgentError> {
    match fstatat(dir, name, AtFlags::AT_SYMLINK_NOFOLLOW) {
        Ok(_) => Ok(true),
        Err(Errno::ENOENT) => Ok(false),
        Err(err) => Err(errno(err)),
    }
}

/// A diff path as components, refused unless it is plainly relative.
fn parts(path: &str) -> Option<Vec<Vec<u8>>> {
    let parts: Vec<Vec<u8>> = path
        .split('/')
        .map(|part| part.as_bytes().to_vec())
        .collect();
    let plain =
        |part: &Vec<u8>| !part.is_empty() && part != b"." && part != b".." && !part.contains(&0);
    (!path.is_empty() && parts.iter().all(plain)).then_some(parts)
}

fn conflict(path: &str, reason: &str) -> Conflict {
    Conflict {
        path: path.to_owned(),
        reason: reason.to_owned(),
    }
}

fn dir_flags() -> OFlag {
    OFlag::O_RDONLY | OFlag::O_DIRECTORY | OFlag::O_NOFOLLOW | OFlag::O_CLOEXEC
}

fn errno(err: Errno) -> AgentError {
    AgentError::Io(std::io::Error::from(err))
}
