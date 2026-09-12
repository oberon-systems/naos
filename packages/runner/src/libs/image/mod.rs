use std::fs::{self, File, OpenOptions};
use std::future::Future;
use std::io::{self, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::pin::Pin;

use sha2::{Digest, Sha256};

use crate::libs::api::{is_digest, DIGEST_PREFIX};
use crate::libs::audit;
use crate::libs::error::AgentError;
use crate::libs::ids::{hex, random_hex};

const CACHED_MODE: u32 = 0o444;
const PARTIAL_MODE: u32 = 0o600;
const HASH_BUFFER: usize = 1024 * 1024;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

pub trait ImageSource: Send + Sync {
    fn fetch<'a>(
        &'a self,
        digest: &'a str,
        sink: &'a mut (dyn Write + Send),
        limit: u64,
    ) -> BoxFuture<'a, Result<u64, AgentError>>;
}

#[derive(Debug, Clone)]
pub struct ImageCache {
    dir: PathBuf,
    max_bytes: u64,
}

struct HashingWriter {
    file: File,
    hasher: Sha256,
}

impl Write for HashingWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let written = self.file.write(buf)?;
        self.hasher.update(&buf[..written]);
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.file.flush()
    }
}

impl ImageCache {
    pub fn new(dir: PathBuf, max_bytes: u64) -> Self {
        Self { dir, max_bytes }
    }

    pub fn path(&self, digest: &str) -> Result<PathBuf, AgentError> {
        let hex = digest
            .strip_prefix(DIGEST_PREFIX)
            .filter(|_| is_digest(digest))
            .ok_or_else(|| AgentError::Image(format!("{digest:?} is not a sha256 digest")))?;
        Ok(self.dir.join(format!("sha256-{hex}.qcow2")))
    }

    /// Downloads the image unless a file already sits under its name; `open_verified` judges it.
    pub async fn fetch(&self, source: &dyn ImageSource, digest: &str) -> Result<(), AgentError> {
        let target = self.path(digest)?;
        if fs::symlink_metadata(&target).is_ok() {
            return Ok(());
        }
        let partial = self.dir.join(format!(".fetch-{}.part", random_hex(8)?));
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(PARTIAL_MODE)
            .open(&partial)?;
        let mut writer = HashingWriter {
            file,
            hasher: Sha256::new(),
        };
        let outcome = source
            .fetch(digest, &mut writer, self.max_bytes)
            .await
            .and_then(|_| finish(writer, &partial, &target, digest));
        if outcome.is_err() {
            let _ = fs::remove_file(&partial);
        }
        outcome
    }

    /// Hashes the cached file through the descriptor it returns, so the bytes checked are the bytes used.
    pub fn open_verified(&self, digest: &str) -> Result<File, AgentError> {
        let path = self.path(digest)?;
        let reject = |why: &str| {
            audit::image_rejected(digest, why);
            AgentError::Image(format!("{} {why}", path.display()))
        };
        let link = fs::symlink_metadata(&path)?;
        if !link.file_type().is_file() {
            return Err(reject("must be a regular file"));
        }
        let mut file = File::open(&path)?;
        let meta = file.metadata()?;
        if (meta.dev(), meta.ino()) != (link.dev(), link.ino()) {
            return Err(reject("changed while it was being opened"));
        }
        if meta.mode() & 0o022 != 0 {
            return Err(reject("must not be writable by group or others"));
        }
        let mut hasher = Sha256::new();
        let mut buffer = vec![0u8; HASH_BUFFER];
        loop {
            let read = file.read(&mut buffer)?;
            if read == 0 {
                break;
            }
            hasher.update(&buffer[..read]);
        }
        if format!("{DIGEST_PREFIX}{}", hex(&hasher.finalize())) != digest {
            let _ = fs::remove_file(&path);
            return Err(reject("does not match its digest and was removed"));
        }
        Ok(file)
    }
}

fn finish(
    writer: HashingWriter,
    partial: &Path,
    target: &Path,
    digest: &str,
) -> Result<(), AgentError> {
    let HashingWriter { file, hasher } = writer;
    file.sync_all()?;
    if format!("{DIGEST_PREFIX}{}", hex(&hasher.finalize())) != digest {
        audit::image_rejected(digest, "downloaded content does not match the digest");
        return Err(AgentError::Image(format!(
            "downloaded image does not match {digest}"
        )));
    }
    fs::set_permissions(partial, fs::Permissions::from_mode(CACHED_MODE))?;
    fs::rename(partial, target)?;
    audit::image_cached(digest);
    Ok(())
}

#[cfg(test)]
mod tests;
