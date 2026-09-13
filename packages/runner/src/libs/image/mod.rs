use std::fs::{self, File, OpenOptions};
use std::future::Future;
use std::io::{self, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::time::Duration;

use reqwest::{redirect, Client};
use sha2::{Digest, Sha256};

use crate::libs::api::{is_digest, DIGEST_PREFIX};
use crate::libs::audit;
use crate::libs::error::AgentError;
use crate::libs::ids::{hex, random_hex};

const CACHED_MODE: u32 = 0o444;
const PARTIAL_MODE: u32 = 0o600;
const HASH_BUFFER: usize = 1024 * 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_REDIRECTS: usize = 5;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

pub trait ImageSource: Send + Sync {
    /// Streams the image at `url` into `sink` and fails once more than `limit` bytes arrive.
    fn fetch<'a>(
        &'a self,
        url: &'a str,
        sink: &'a mut (dyn Write + Send),
        limit: u64,
    ) -> BoxFuture<'a, Result<u64, AgentError>>;
}

/// Downloads from wherever the API points; the digest, not the host, is what gets trusted.
pub struct HttpImages {
    client: Client,
}

impl HttpImages {
    pub fn new() -> Result<Self, AgentError> {
        let client = Client::builder()
            .redirect(redirect::Policy::limited(MAX_REDIRECTS))
            .connect_timeout(CONNECT_TIMEOUT)
            .read_timeout(READ_TIMEOUT)
            .user_agent(concat!("naos-agent/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|err| AgentError::Transport(err.to_string()))?;
        Ok(Self { client })
    }

    async fn download(
        &self,
        url: &str,
        sink: &mut (dyn Write + Send),
        limit: u64,
    ) -> Result<u64, AgentError> {
        let transport = |err: reqwest::Error| AgentError::Transport(err.without_url().to_string());
        let too_large = || AgentError::Image(format!("image exceeds {limit} bytes"));
        let mut response = self.client.get(url).send().await.map_err(transport)?;
        if !response.status().is_success() {
            return Err(AgentError::Image(format!(
                "image source answered {}",
                response.status()
            )));
        }
        if response
            .content_length()
            .is_some_and(|length| length > limit)
        {
            return Err(too_large());
        }
        let mut written: u64 = 0;
        while let Some(chunk) = response.chunk().await.map_err(transport)? {
            written += chunk.len() as u64;
            if written > limit {
                return Err(too_large());
            }
            sink.write_all(&chunk)?;
        }
        Ok(written)
    }
}

impl ImageSource for HttpImages {
    fn fetch<'a>(
        &'a self,
        url: &'a str,
        sink: &'a mut (dyn Write + Send),
        limit: u64,
    ) -> BoxFuture<'a, Result<u64, AgentError>> {
        Box::pin(self.download(url, sink, limit))
    }
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
    pub async fn fetch(
        &self,
        source: &dyn ImageSource,
        url: &str,
        digest: &str,
    ) -> Result<(), AgentError> {
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
            .fetch(url, &mut writer, self.max_bytes)
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
