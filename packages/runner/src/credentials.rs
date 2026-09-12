use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::AgentError;

const PRIVATE_MODE: u32 = 0o600;
const MAX_SECRET_FILE: u64 = 64 * 1024;

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Credentials {
    pub runner_id: String,
    pub token: String,
}

impl fmt::Debug for Credentials {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Credentials")
            .field("runner_id", &self.runner_id)
            .field("token", &"<redacted>")
            .finish()
    }
}

#[derive(Debug, Clone)]
pub struct CredentialStore {
    path: PathBuf,
}

impl CredentialStore {
    pub fn new(state_dir: &Path) -> Self {
        Self {
            path: state_dir.join("credentials.json"),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> Result<Option<Credentials>, AgentError> {
        let raw = match read_private(&self.path) {
            Ok(raw) => raw,
            Err(AgentError::Io(err)) if err.kind() == ErrorKind::NotFound => return Ok(None),
            Err(err) => return Err(err),
        };
        serde_json::from_str(&raw)
            .map(Some)
            .map_err(|_| AgentError::Credentials(format!("{} is malformed", self.path.display())))
    }

    pub fn save(&self, credentials: &Credentials) -> Result<(), AgentError> {
        let body = serde_json::to_vec(credentials)
            .map_err(|err| AgentError::Credentials(err.to_string()))?;
        let tmp = self.path.with_extension("json.tmp");
        match fs::remove_file(&tmp) {
            Err(err) if err.kind() != ErrorKind::NotFound => return Err(err.into()),
            _ => {}
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(PRIVATE_MODE)
            .open(&tmp)?;
        file.write_all(&body)?;
        file.sync_all()?;
        fs::rename(&tmp, &self.path)?;
        Ok(())
    }

    pub fn clear(&self) -> Result<(), AgentError> {
        match fs::remove_file(&self.path) {
            Err(err) if err.kind() != ErrorKind::NotFound => Err(err.into()),
            _ => Ok(()),
        }
    }
}

pub fn read_secret_file(path: &Path) -> Result<String, AgentError> {
    let secret = read_private(path)?.trim().to_owned();
    if secret.is_empty() {
        return Err(AgentError::Credentials(format!(
            "{} is empty",
            path.display()
        )));
    }
    Ok(secret)
}

fn read_private(path: &Path) -> Result<String, AgentError> {
    let reject = |why: &str| AgentError::Credentials(format!("{} {why}", path.display()));
    if fs::symlink_metadata(path)?.file_type().is_symlink() {
        return Err(reject("must not be a symlink"));
    }
    let file = File::open(path)?;
    let meta = file.metadata()?;
    if !meta.is_file() {
        return Err(reject("must be a regular file"));
    }
    if meta.permissions().mode() & 0o077 != 0 {
        return Err(reject(
            "must not be readable or writable by group or others",
        ));
    }
    if meta.len() > MAX_SECRET_FILE {
        return Err(reject("is too large"));
    }
    let mut raw = String::new();
    file.take(MAX_SECRET_FILE).read_to_string(&mut raw)?;
    Ok(raw)
}

#[cfg(test)]
mod tests {
    use std::os::unix::fs::symlink;

    use super::*;

    fn credentials() -> Credentials {
        Credentials {
            runner_id: "rnr_alpha".into(),
            token: "secret-token".into(),
        }
    }

    #[test]
    fn save_writes_owner_only_file_and_loads_back() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = CredentialStore::new(dir.path());

        assert_eq!(store.load().expect("load"), None);
        store.save(&credentials()).expect("save");

        let mode = fs::metadata(store.path())
            .expect("meta")
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, PRIVATE_MODE);
        assert_eq!(store.load().expect("load"), Some(credentials()));

        store.clear().expect("clear");
        assert_eq!(store.load().expect("load"), None);
    }

    #[test]
    fn widely_readable_credentials_are_refused() {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = CredentialStore::new(dir.path());
        store.save(&credentials()).expect("save");
        fs::set_permissions(store.path(), fs::Permissions::from_mode(0o644)).expect("chmod");

        assert!(matches!(store.load(), Err(AgentError::Credentials(_))));
    }

    #[test]
    fn symlinked_secret_is_refused() {
        let dir = tempfile::tempdir().expect("tempdir");
        let target = dir.path().join("target");
        fs::write(&target, "token").expect("write");
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).expect("chmod");
        let link = dir.path().join("link");
        symlink(&target, &link).expect("symlink");

        assert!(read_secret_file(&target).is_ok());
        assert!(matches!(
            read_secret_file(&link),
            Err(AgentError::Credentials(_))
        ));
    }

    #[test]
    fn empty_or_malformed_files_are_refused() {
        let dir = tempfile::tempdir().expect("tempdir");
        let empty = dir.path().join("empty");
        fs::write(&empty, " \n").expect("write");
        fs::set_permissions(&empty, fs::Permissions::from_mode(0o600)).expect("chmod");
        assert!(read_secret_file(&empty).is_err());

        let store = CredentialStore::new(dir.path());
        fs::write(store.path(), "{").expect("write");
        fs::set_permissions(store.path(), fs::Permissions::from_mode(0o600)).expect("chmod");
        assert!(matches!(store.load(), Err(AgentError::Credentials(_))));
    }

    #[test]
    fn debug_output_redacts_the_token() {
        assert!(!format!("{:?}", credentials()).contains("secret-token"));
    }
}
