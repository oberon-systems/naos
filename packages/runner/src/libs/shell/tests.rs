use std::os::unix::fs::symlink;

use serde_json::json;
use tempfile::TempDir;

use super::*;

const GUEST: &str = "/naos/alpha";

fn git_binary() -> PathBuf {
    ["/usr/bin/git", "/bin/git", "/usr/local/bin/git"]
        .into_iter()
        .map(PathBuf::from)
        .find(|path| path.exists())
        .expect("git is required by these tests")
}

fn mounts(host: &Path) -> Value {
    json!({
        "workdir": GUEST,
        "mounts": [{
            "host_path": host.to_str().expect("utf-8"),
            "guest_path": GUEST,
            "mode": "rw",
        }],
    })
}

fn gate(dir: &TempDir, allow: &[&str]) -> ShellGate {
    ShellGate::from_snapshot(
        "run_a",
        Some(&json!({ "allow": allow })),
        Some(&mounts(dir.path())),
        &git_binary(),
    )
    .expect("policy")
}

fn read(name: &str) -> ShellRequest {
    ShellRequest::ReadFile {
        path: format!("{GUEST}/{name}"),
    }
}

fn repo(dir: &Path) {
    let git = git_binary();
    let run = |args: &[&str]| {
        let status = std::process::Command::new(&git)
            .args(args)
            .current_dir(dir)
            .env_clear()
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_CONFIG_GLOBAL", "/dev/null")
            .env("GIT_AUTHOR_NAME", "alpha")
            .env("GIT_AUTHOR_EMAIL", "alpha@example.com")
            .env("GIT_COMMITTER_NAME", "alpha")
            .env("GIT_COMMITTER_EMAIL", "alpha@example.com")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .expect("git");
        assert!(status.success(), "{args:?}");
    };
    run(&["init", "-q", "-b", "main"]);
    fs::write(dir.join("tracked.txt"), "one\n").expect("write");
    run(&["add", "tracked.txt"]);
    run(&["commit", "-qm", "first"]);
}

/// Every file under `.git` with its size and modification time, to prove nothing wrote there.
fn git_state(dir: &Path) -> Vec<(PathBuf, u64, i64)> {
    let mut state = Vec::new();
    let mut stack = vec![dir.join(".git")];
    while let Some(current) = stack.pop() {
        let meta = fs::symlink_metadata(&current).expect("metadata");
        if meta.is_dir() {
            for entry in fs::read_dir(&current).expect("read dir") {
                stack.push(entry.expect("entry").path());
            }
            continue;
        }
        state.push((current, meta.len(), meta.mtime()));
    }
    state.sort();
    state
}

#[test]
fn an_unknown_capability_is_refused() {
    let err = ShellGate::from_snapshot(
        "run_a",
        Some(&json!({ "allow": ["write_file"] })),
        None,
        &git_binary(),
    )
    .expect_err("refused");

    assert!(err.to_string().contains("invalid shell policy"));
}

#[test]
fn a_malformed_mount_snapshot_is_refused() {
    for document in [
        json!({ "workdir": GUEST }),
        json!({ "workdir": GUEST, "mounts": [{"host_path": "relative", "guest_path": GUEST, "mode": "ro"}] }),
        json!({ "workdir": GUEST, "mounts": [{"host_path": "/nonexistent/alpha", "guest_path": GUEST, "mode": "ro"}] }),
    ] {
        let err = ShellGate::from_snapshot(
            "run_a",
            Some(&json!({ "allow": ["read_file"] })),
            Some(&document),
            &git_binary(),
        )
        .expect_err("refused");
        assert!(
            err.to_string().contains("invalid mount policy"),
            "{document}"
        );
    }
}

#[tokio::test]
async fn no_policy_grants_nothing() {
    let dir = tempfile::tempdir().expect("tempdir");
    fs::write(dir.path().join("file"), b"beta").expect("write");
    let absent = ShellGate::from_snapshot("run_a", None, Some(&mounts(dir.path())), &git_binary())
        .expect("policy");
    let empty = gate(&dir, &[]);

    assert!(absent.call(read("file")).await.is_err());
    assert!(empty.call(read("file")).await.is_err());
}

#[tokio::test]
async fn a_capability_that_was_not_granted_is_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    fs::write(dir.path().join("file"), b"beta").expect("write");
    let gate = gate(&dir, &["list_dir"]);

    let err = gate.call(read("file")).await.expect_err("denied");

    assert!(err.to_string().contains("capability not granted"));
}

#[tokio::test]
async fn traversal_and_unnormalized_paths_are_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let gate = gate(&dir, &["read_file"]);

    for path in [
        format!("{GUEST}/../../etc/passwd"),
        format!("{GUEST}/./file"),
        format!("{GUEST}//file"),
        "naos/alpha/file".to_owned(),
        format!("{GUEST}/file\0name"),
    ] {
        let err = gate
            .call(ShellRequest::ReadFile { path: path.clone() })
            .await
            .expect_err("denied");
        assert!(
            err.to_string().contains("not absolute and normalized"),
            "{path}"
        );
    }
}

#[tokio::test]
async fn a_path_outside_every_mount_is_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let host = dir.path().join("file");
    fs::write(&host, b"beta").expect("write");
    let gate = gate(&dir, &["read_file"]);

    for path in [
        "/etc/passwd".to_owned(),
        "/naos/beta/file".to_owned(),
        host.to_string_lossy().into_owned(),
    ] {
        let err = gate
            .call(ShellRequest::ReadFile { path: path.clone() })
            .await
            .expect_err("denied");
        assert!(err.to_string().contains("outside every mount"), "{path}");
    }
}

#[tokio::test]
async fn a_symlink_out_of_the_mount_is_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let outside = tempfile::tempdir().expect("tempdir");
    fs::write(outside.path().join("secret"), b"beta").expect("write");
    symlink(outside.path().join("secret"), dir.path().join("file")).expect("symlink");
    symlink(outside.path(), dir.path().join("dir")).expect("symlink");
    let gate = gate(&dir, &["read_file", "list_dir"]);

    let file = gate.call(read("file")).await.expect_err("denied");
    let listed = gate
        .call(ShellRequest::ListDir {
            path: format!("{GUEST}/dir"),
        })
        .await
        .expect_err("denied");

    assert!(file.to_string().contains("escapes its mount"));
    assert!(listed.to_string().contains("escapes its mount"));
}

#[tokio::test]
async fn a_hard_link_is_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let target = dir.path().join("real");
    fs::write(&target, b"beta").expect("write");
    fs::hard_link(&target, dir.path().join("linked")).expect("hard link");
    let gate = gate(&dir, &["read_file"]);

    // Both names carry the extra link count, and neither can be told from the other by path.
    for name in ["real", "linked"] {
        let err = gate.call(read(name)).await.expect_err("denied");
        assert!(err.to_string().contains("hard link"), "{name}");
    }
}

#[tokio::test]
async fn an_oversized_file_is_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    fs::write(
        dir.path().join("big"),
        vec![b'x'; MAX_FILE_BYTES as usize + 1],
    )
    .expect("write");
    let gate = gate(&dir, &["read_file"]);

    let err = gate.call(read("big")).await.expect_err("denied");

    assert!(err.to_string().contains("too large"));
}

#[tokio::test]
async fn a_granted_path_is_read_and_listed() {
    let dir = tempfile::tempdir().expect("tempdir");
    fs::write(dir.path().join("file"), b"beta").expect("write");
    fs::create_dir(dir.path().join("sub")).expect("mkdir");
    symlink("/etc", dir.path().join("link")).expect("symlink");
    let gate = gate(&dir, &["read_file", "list_dir"]);

    let bytes = gate.call(read("file")).await.expect("read");
    let listed = gate
        .call(ShellRequest::ListDir {
            path: GUEST.to_owned(),
        })
        .await
        .expect("list");

    assert!(matches!(bytes, ShellResponse::File(bytes) if bytes == b"beta"));
    let ShellResponse::Entries(entries) = listed else {
        panic!("entries");
    };
    let seen: Vec<(&str, &str)> = entries
        .iter()
        .map(|entry| (entry.name.as_str(), entry.kind))
        .collect();
    assert_eq!(
        seen,
        vec![("file", "file"), ("link", "other"), ("sub", "dir")]
    );
}

#[tokio::test]
async fn grep_finds_matches_and_stops_at_the_cap() {
    let dir = tempfile::tempdir().expect("tempdir");
    fs::create_dir(dir.path().join("sub")).expect("mkdir");
    fs::write(dir.path().join("sub/one.txt"), "alpha\nbeta\nalpha gamma\n").expect("write");
    fs::write(
        dir.path().join("many.txt"),
        "needle\n".repeat(MAX_MATCHES + 10),
    )
    .expect("write");
    let gate = gate(&dir, &["grep"]);

    let found = gate
        .call(ShellRequest::Grep {
            path: format!("{GUEST}/sub"),
            pattern: "alpha".into(),
        })
        .await
        .expect("grep");
    let capped = gate
        .call(ShellRequest::Grep {
            path: format!("{GUEST}/many.txt"),
            pattern: "needle".into(),
        })
        .await
        .expect("grep");

    let ShellResponse::Matches(found) = found else {
        panic!("matches");
    };
    let ShellResponse::Matches(capped) = capped else {
        panic!("matches");
    };
    assert_eq!(found.len(), 2);
    assert_eq!(found[0].path, format!("{GUEST}/sub/one.txt"));
    assert_eq!((found[0].line, found[1].line), (1, 3));
    assert_eq!(capped.len(), MAX_MATCHES);
}

#[tokio::test]
async fn grep_does_not_follow_a_symlinked_directory() {
    let dir = tempfile::tempdir().expect("tempdir");
    let outside = tempfile::tempdir().expect("tempdir");
    fs::write(outside.path().join("secret.txt"), "needle\n").expect("write");
    symlink(outside.path(), dir.path().join("out")).expect("symlink");
    let gate = gate(&dir, &["grep"]);

    let found = gate
        .call(ShellRequest::Grep {
            path: GUEST.to_owned(),
            pattern: "needle".into(),
        })
        .await
        .expect("grep");

    assert!(matches!(found, ShellResponse::Matches(matches) if matches.is_empty()));
}

#[tokio::test]
async fn a_run_cannot_exceed_its_call_budget() {
    let dir = tempfile::tempdir().expect("tempdir");
    let gate = gate(&dir, &["read_file"]);

    for _ in 0..MAX_CALLS {
        assert!(gate.call(read("missing")).await.is_err());
    }
    let err = gate.call(read("missing")).await.expect_err("throttled");

    assert!(err.to_string().contains("rate limit"));
}

#[tokio::test]
async fn git_reports_the_worktree_without_writing_to_it() {
    let dir = tempfile::tempdir().expect("tempdir");
    repo(dir.path());
    fs::write(dir.path().join("tracked.txt"), "two\n").expect("write");
    fs::write(dir.path().join("untracked.txt"), "three\n").expect("write");
    let before = git_state(dir.path());
    let gate = gate(&dir, &["git_status", "git_diff"]);

    let status = gate
        .call(ShellRequest::GitStatus {
            path: GUEST.to_owned(),
        })
        .await
        .expect("status");
    let diff = gate
        .call(ShellRequest::GitDiff {
            path: GUEST.to_owned(),
        })
        .await
        .expect("diff");

    let ShellResponse::Text(status) = status else {
        panic!("text");
    };
    let ShellResponse::Text(diff) = diff else {
        panic!("text");
    };
    assert!(status.contains("M tracked.txt"), "{status}");
    assert!(status.contains("?? untracked.txt"), "{status}");
    assert!(diff.contains("-one") && diff.contains("+two"), "{diff}");
    assert_eq!(git_state(dir.path()), before, "the gate wrote into .git");
}

#[tokio::test]
async fn a_hostile_repository_config_cannot_hook_a_program() {
    let toucher = ["/usr/bin/touch", "/bin/touch"]
        .into_iter()
        .find(|path| Path::new(path).exists())
        .expect("touch");
    let dir = tempfile::tempdir().expect("tempdir");
    repo(dir.path());
    fs::write(dir.path().join("tracked.txt"), "two\n").expect("write");
    let marker = dir.path().join("pwned");
    let config = dir.path().join(".git/config");
    let mut text = fs::read_to_string(&config).expect("read");
    text.push_str(&format!(
        "[diff]\n\texternal = {toucher} {0}\n[core]\n\tfsmonitor = {toucher} {0}\n",
        marker.display()
    ));
    fs::write(&config, text).expect("write");
    let gate = gate(&dir, &["git_status", "git_diff"]);

    let diff = gate
        .call(ShellRequest::GitDiff {
            path: GUEST.to_owned(),
        })
        .await
        .expect("diff");
    gate.call(ShellRequest::GitStatus {
        path: GUEST.to_owned(),
    })
    .await
    .expect("status");

    let ShellResponse::Text(diff) = diff else {
        panic!("text");
    };
    assert!(diff.contains("-one") && diff.contains("+two"), "{diff}");
    assert!(!marker.exists(), "the repository config hooked a program");
}

#[tokio::test]
async fn git_that_cannot_run_is_denied() {
    let dir = tempfile::tempdir().expect("tempdir");
    let missing = ShellGate::from_snapshot(
        "run_a",
        Some(&json!({ "allow": ["git_status"] })),
        Some(&mounts(dir.path())),
        Path::new("/nonexistent/git"),
    )
    .expect("policy");
    let outside = gate(&dir, &["git_status"]);

    let absent = missing
        .call(ShellRequest::GitStatus {
            path: GUEST.to_owned(),
        })
        .await
        .expect_err("denied");
    let not_a_repo = outside
        .call(ShellRequest::GitStatus {
            path: GUEST.to_owned(),
        })
        .await
        .expect_err("denied");

    assert!(absent.to_string().contains("git could not run"));
    assert!(not_a_repo.to_string().contains("git failed"));
}
