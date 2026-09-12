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
