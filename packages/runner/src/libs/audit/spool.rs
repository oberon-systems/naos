use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, ErrorKind, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

use super::Event;

const PRIVATE_MODE: u32 = 0o600;
pub const DEFAULT_LIMIT: u64 = 64 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct Spool {
    path: PathBuf,
    lock: PathBuf,
    limit: u64,
}

#[derive(Debug, Default)]
pub struct Batch {
    pub events: Vec<Event>,
    last: Option<String>,
}

impl Spool {
    pub fn new(state_dir: &Path) -> Self {
        Self::with_limit(state_dir, DEFAULT_LIMIT)
    }

    pub fn with_limit(state_dir: &Path, limit: u64) -> Self {
        Self {
            path: state_dir.join("audit.jsonl"),
            lock: state_dir.join("audit.lock"),
            limit,
        }
    }

    #[cfg(test)]
    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn append(&self, event: &Event) -> io::Result<()> {
        let mut line = serde_json::to_vec(event)?;
        line.push(b'\n');
        let _lock = self.locked()?;
        let size = match fs::metadata(&self.path) {
            Ok(meta) => meta.len(),
            Err(err) if err.kind() == ErrorKind::NotFound => 0,
            Err(err) => return Err(err),
        };
        if size + line.len() as u64 > self.limit {
            return self.compact(&line);
        }
        OpenOptions::new()
            .append(true)
            .create(true)
            .mode(PRIVATE_MODE)
            .open(&self.path)?
            .write_all(&line)
    }

    pub fn take(&self, limit: usize) -> io::Result<Batch> {
        let _lock = self.locked()?;
        let mut batch = Batch::default();
        for line in self.lines()? {
            if batch.events.len() == limit {
                break;
            }
            if let Some(event) = parse(&line) {
                batch.last = Some(event.id.clone());
                batch.events.push(event);
            }
        }
        Ok(batch)
    }

    /// Drops what `batch` took, and the unreadable lines right after it.
    pub fn ack(&self, batch: &Batch) -> io::Result<()> {
        let _lock = self.locked()?;
        let lines = self.lines()?;
        let mut cut = batch
            .last
            .as_ref()
            .and_then(|last| {
                lines
                    .iter()
                    .position(|line| parse(line).is_some_and(|event| &event.id == last))
            })
            .map_or(0, |at| at + 1);
        while lines.get(cut).is_some_and(|line| parse(line).is_none()) {
            cut += 1;
        }
        if cut == 0 {
            return Ok(());
        }
        self.rewrite(&lines[cut..], &[])
    }

    fn compact(&self, line: &[u8]) -> io::Result<()> {
        let lines = self.lines()?;
        let budget = self.limit / 2;
        let mut kept = 0;
        let mut start = lines.len();
        while start > 0 && kept + (lines[start - 1].len() as u64) < budget {
            start -= 1;
            kept += lines[start].len() as u64 + 1;
        }
        tracing::warn!(target: "audit", event = "audit_dropped", dropped = start);
        let dropped = Event::new(
            "audit_dropped",
            Map::from_iter([("dropped".to_owned(), Value::from(start))]),
        )?;
        let mut tail = serde_json::to_vec(&dropped)?;
        tail.push(b'\n');
        tail.extend_from_slice(line);
        self.rewrite(&lines[start..], &tail)
    }

    fn locked(&self) -> io::Result<File> {
        let file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .mode(PRIVATE_MODE)
            .open(&self.lock)?;
        file.lock()?;
        Ok(file)
    }

    fn lines(&self) -> io::Result<Vec<Vec<u8>>> {
        match File::open(&self.path) {
            Ok(file) => BufReader::new(file).split(b'\n').collect(),
            Err(err) if err.kind() == ErrorKind::NotFound => Ok(Vec::new()),
            Err(err) => Err(err),
        }
    }

    fn rewrite(&self, lines: &[Vec<u8>], tail: &[u8]) -> io::Result<()> {
        let tmp = self.path.with_extension("jsonl.tmp");
        match fs::remove_file(&tmp) {
            Err(err) if err.kind() != ErrorKind::NotFound => return Err(err),
            _ => {}
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(PRIVATE_MODE)
            .open(&tmp)?;
        let mut body = Vec::new();
        for line in lines {
            body.extend_from_slice(line);
            body.push(b'\n');
        }
        body.extend_from_slice(tail);
        file.write_all(&body)?;
        file.sync_all()?;
        fs::rename(&tmp, &self.path)
    }
}

fn parse(line: &[u8]) -> Option<Event> {
    serde_json::from_slice(line).ok()
}
