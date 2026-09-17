use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;

use tempfile::TempDir;

use super::*;
use crate::libs::api::{RunCredential, RunStatus};
use crate::libs::shell::{ShellRequest, ShellResponse};
use crate::libs::testing::{desired_run, digest_of, FakeSource};

const IMAGE: &[u8] = b"qcow2-alpha";
const FAKE_QEMU_IMG: &str = "#!/bin/sh\n\
case \"$1\" in\n\
  info) echo '{\"format\": \"qcow2\", \"virtual-size\": 1048576}' ;;\n\
  create) : > \"$4\" ;;\n\
esac\n";

fn config(dir: &TempDir, qemu_binary: &str, qemu_img: PathBuf) -> RuntimeConfig {
    RuntimeConfig {
        image_dir: dir.path().join("images"),
        vm_dir: dir.path().join("runs"),
        qemu_binary: qemu_binary.into(),
        qemu_img,
        git_binary: "/usr/bin/git".into(),
        virtiofsd_binary: "/nonexistent/virtiofsd".into(),
        image_max_bytes: 1024 * 1024,
    }
}

/// A virtiofsd that reports `version`, records its arguments and keeps running like the real one.
fn fake_virtiofsd(dir: &Path, version: &str) -> PathBuf {
    let path = dir.join("virtiofsd");
    let record = dir.join("virtiofsd.args");
    fs::write(
        &path,
        format!(
            "#!/bin/sh\n\
             [ \"$1\" = --version ] && {{ echo 'virtiofsd {version}'; exit 0; }}\n\
             printf '%s\\n' \"$@\" > {record}\n\
             for arg; do case $arg in --socket-path=*) : > \"${{arg#--socket-path=}}\" ;; esac; done\n\
             while :; do sleep 1; done\n",
            record = record.display()
        ),
    )
    .expect("write");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("chmod");
    path
}

fn with_workspace(run: &mut DesiredRun, host: &Path, mode: &str) {
    run.policies.insert(
        "mount".into(),
        Some(json!({
            "workdir": "/naos/alpha",
            "mounts": [{
                "host_path": host.to_str().expect("utf-8"),
                "guest_path": "/naos/alpha",
                "mode": mode,
            }],
        })),
    );
}

fn fake_qemu_img(dir: &TempDir) -> PathBuf {
    let path = dir.path().join("qemu-img");
    fs::write(&path, FAKE_QEMU_IMG).expect("write");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).expect("chmod");
    path
}

fn run_of(bytes: &[u8]) -> DesiredRun {
    let mut run = desired_run("run_a", RunStatus::Starting);
    run.spec.image.digest = digest_of(bytes);
    run
}

fn vm_dirs(dir: &TempDir) -> Vec<String> {
    fs::read_dir(dir.path().join("runs"))
        .expect("read dir")
        .map(|entry| {
            entry
                .expect("entry")
                .file_name()
                .to_string_lossy()
                .into_owned()
        })
        .collect()
}

#[tokio::test]
async fn granted_capabilities_are_refused_before_anything_happens() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let source = FakeSource::new(IMAGE);
    let mut run = run_of(IMAGE);
    run.policies.insert("beta".into(), Some(json!({})));

    let outcome = runtime.ensure(&run, &source).await;

    assert!(matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("beta")));
    assert_eq!(source.calls(), 0);
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn malformed_network_policy_is_refused_before_anything_happens() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let source = FakeSource::new(IMAGE);
    let mut run = run_of(IMAGE);
    run.policies
        .insert("network".into(), Some(json!({ "allow": "all" })));

    let outcome = runtime.ensure(&run, &source).await;

    assert!(
        matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("invalid network policy"))
    );
    assert_eq!(source.calls(), 0);
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn malformed_mcp_policy_is_refused_before_anything_happens() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let source = FakeSource::new(IMAGE);
    let mut run = run_of(IMAGE);
    run.policies.insert(
        "mcp".into(),
        Some(json!({ "servers": [{ "name": "alpha", "url": "http://example.com/mcp" }] })),
    );

    let outcome = runtime.ensure(&run, &source).await;

    assert!(
        matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("invalid mcp policy"))
    );
    assert_eq!(source.calls(), 0);
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn a_failed_start_leaves_no_gate_behind() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let mut run = run_of(IMAGE);
    run.policies.insert(
        "network".into(),
        Some(json!({ "allow": [{ "host": "example.com" }] })),
    );

    assert!(runtime.ensure(&run, &FakeSource::new(IMAGE)).await.is_err());

    assert!(runtime.gates(&run.id).is_none());
}

#[tokio::test]
async fn sync_refreshes_credentials_on_the_kept_gate_and_never_launches() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let mut run = desired_run("run_a", RunStatus::Started);
    run.policies.insert(
        "mcp".into(),
        Some(json!({ "servers": [{
            "name": "alpha", "url": "https://example.com/mcp", "tools": ["search"],
            "resources": [], "credential": "alpha-token",
            "timeout_seconds": 30, "max_calls_per_minute": 60,
        }] })),
    );
    let vm = LocalVm {
        vm_id: "vm_0123456789abcdef0123456789abcdef".into(),
        run_id: run.id.clone(),
        running: true,
    };
    let issue = |run: &mut DesiredRun, value: &str| {
        run.credentials = BTreeMap::from([(
            "alpha-token".to_owned(),
            RunCredential {
                value: value.into(),
                expires_at: u64::MAX,
            },
        )]);
    };

    runtime.sync(&run, &vm).await.expect("sync");
    let first = runtime.gates(&run.id).expect("gates");
    assert_eq!(first.mcp.credential_value("alpha-token"), None);
    issue(&mut run, "alpha-secret-2");
    runtime.sync(&run, &vm).await.expect("sync again");

    let kept = runtime.gates(&run.id).expect("gates");
    assert!(Arc::ptr_eq(&first, &kept));
    assert_eq!(
        kept.mcp.credential_value("alpha-token").as_deref(),
        Some("alpha-secret-2")
    );
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn runs_sharing_a_guest_path_read_only_their_own_mounts() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let expected = [("run_a", "alpha"), ("run_b", "beta")];
    for (run_id, content) in expected {
        let host = dir.path().join(run_id);
        fs::create_dir(&host).expect("mkdir");
        fs::write(host.join("notes.txt"), content).expect("write");
        let mut run = desired_run(run_id, RunStatus::Started);
        run.policies
            .insert("shell".into(), Some(json!({ "allow": ["read_file"] })));
        run.policies.insert(
            "mount".into(),
            Some(json!({
                "workdir": "/naos/alpha",
                "mounts": [{
                    "host_path": host.to_str().expect("utf-8"),
                    "guest_path": "/naos/alpha",
                    "mode": "rw",
                }],
            })),
        );
        let vm = LocalVm {
            vm_id: format!("vm_{}", random_hex(16).expect("id")),
            run_id: run_id.into(),
            running: true,
        };
        runtime.sync(&run, &vm).await.expect("sync");
    }

    for (run_id, content) in expected {
        let gates = runtime.gates(run_id).expect("gates");
        let read = gates
            .shell
            .call(ShellRequest::ReadFile {
                path: "/naos/alpha/notes.txt".into(),
            })
            .await
            .expect("read");
        let ShellResponse::File(bytes) = read else {
            panic!("file");
        };
        assert_eq!(bytes, content.as_bytes(), "{run_id}");
    }
}

#[tokio::test]
async fn malformed_mount_policy_is_refused_before_anything_happens() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let source = FakeSource::new(IMAGE);
    let mut run = run_of(IMAGE);
    run.policies
        .insert("mount".into(), Some(json!({ "workdir": "/naos/alpha" })));

    let outcome = runtime.ensure(&run, &source).await;

    assert!(
        matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("invalid mount policy"))
    );
    assert_eq!(source.calls(), 0);
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn failed_qemu_start_leaves_no_vm_behind() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");

    let outcome = runtime
        .ensure(&run_of(IMAGE), &FakeSource::new(IMAGE))
        .await;

    assert!(
        matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("qemu exited"))
    );
    assert!(vm_dirs(&dir).is_empty());
    assert!(runtime.list().await.expect("list").is_empty());
}

#[tokio::test]
async fn a_workspace_is_shared_read_only_and_released_when_qemu_fails() {
    let dir = tempfile::tempdir().expect("tempdir");
    let bin = tempfile::tempdir().expect("tempdir");
    let workspace = dir.path().join("alpha");
    fs::create_dir(&workspace).expect("mkdir");
    let mut config = config(&dir, "/bin/false", fake_qemu_img(&dir));
    config.virtiofsd_binary = fake_virtiofsd(bin.path(), "1.13.0");
    let runtime = QemuRuntime::new(&config).expect("runtime");
    let mut run = run_of(IMAGE);
    with_workspace(&mut run, &workspace, "rw");

    let outcome = runtime.ensure(&run, &FakeSource::new(IMAGE)).await;

    assert!(
        matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("qemu exited"))
    );
    let recorded = fs::read_to_string(bin.path().join("virtiofsd.args")).expect("args");
    let args: Vec<&str> = recorded.lines().collect();
    for expected in ["--readonly", "--sandbox=namespace", "--cache=never"] {
        assert!(args.contains(&expected), "{expected} in {args:?}");
    }
    assert!(args.contains(&format!("--shared-dir={}", workspace.display()).as_str()));
    let socket = args
        .iter()
        .find_map(|arg| arg.strip_prefix("--socket-path="))
        .expect("socket");
    let paths = VmPaths::new(Path::new(socket).parent().expect("vm dir").to_path_buf());
    assert!(
        virtiofsd_pids(&paths).is_empty(),
        "virtiofsd outlived the failed start"
    );
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn a_workspace_needs_a_virtiofsd_that_can_refuse_writes() {
    let dir = tempfile::tempdir().expect("tempdir");
    let bin = tempfile::tempdir().expect("tempdir");
    let workspace = dir.path().join("alpha");
    fs::create_dir(&workspace).expect("mkdir");
    let mut config = config(&dir, "/bin/false", fake_qemu_img(&dir));
    config.virtiofsd_binary = fake_virtiofsd(bin.path(), "1.10.0");
    let runtime = QemuRuntime::new(&config).expect("runtime");
    let source = FakeSource::new(IMAGE);
    let mut run = run_of(IMAGE);
    with_workspace(&mut run, &workspace, "rw");

    let outcome = runtime.ensure(&run, &source).await;

    assert!(
        matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("virtiofsd 1.13"))
    );
    assert_eq!(source.calls(), 0);
    assert!(vm_dirs(&dir).is_empty());
    assert!(!bin.path().join("virtiofsd.args").exists());
}

#[tokio::test]
async fn a_workspace_reached_through_a_symlink_is_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let bin = tempfile::tempdir().expect("tempdir");
    let target = dir.path().join("beta");
    fs::create_dir(&target).expect("mkdir");
    let link = dir.path().join("alpha");
    std::os::unix::fs::symlink(&target, &link).expect("symlink");
    let mut config = config(&dir, "/bin/false", fake_qemu_img(&dir));
    config.virtiofsd_binary = fake_virtiofsd(bin.path(), "1.13.0");
    let runtime = QemuRuntime::new(&config).expect("runtime");
    let source = FakeSource::new(IMAGE);
    let mut run = run_of(IMAGE);
    with_workspace(&mut run, &link, "rw");

    let outcome = runtime.ensure(&run, &source).await;

    assert!(
        matches!(outcome, Err(AgentError::Runtime(message)) if message.contains("without symlinks"))
    );
    assert_eq!(source.calls(), 0);
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn corrupted_cache_is_refused_and_removed() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let run = run_of(IMAGE);
    let cached = runtime.images.path(&run.spec.image.digest).expect("path");
    fs::write(&cached, b"qcow2-beta").expect("plant");
    fs::set_permissions(&cached, fs::Permissions::from_mode(0o444)).expect("chmod");

    let outcome = runtime.ensure(&run, &FakeSource::new(IMAGE)).await;

    assert!(matches!(outcome, Err(AgentError::Image(_))));
    assert!(!cached.exists());
    assert!(vm_dirs(&dir).is_empty());
}

#[tokio::test]
async fn list_reports_dead_vms_and_destroy_is_idempotent() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let vm_id = "vm_0123456789abcdef0123456789abcdef";
    let vm_dir = dir.path().join("runs").join(vm_id);
    fs::create_dir(&vm_dir).expect("mkdir");
    fs::write(
        vm_dir.join("vm.json"),
        json!({ "vm_id": vm_id, "run_id": "run_a", "image_id": "image_alpha", "digest": digest_of(IMAGE) })
            .to_string(),
    )
    .expect("meta");
    fs::create_dir(dir.path().join("runs").join("junk")).expect("mkdir");

    let vms = runtime.list().await.expect("list");

    let dead = LocalVm {
        vm_id: vm_id.into(),
        run_id: "run_a".into(),
        running: false,
    };
    assert_eq!(vms, vec![dead.clone()]);
    runtime.destroy(&dead).await.expect("destroy");
    runtime.destroy(&dead).await.expect("destroy again");
    assert_eq!(vm_dirs(&dir), vec!["junk".to_owned()]);
}

#[tokio::test]
async fn unsafe_vm_ids_are_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let runtime =
        QemuRuntime::new(&config(&dir, "/bin/false", fake_qemu_img(&dir))).expect("runtime");
    let forged = LocalVm {
        vm_id: "../../images".into(),
        run_id: "run_a".into(),
        running: false,
    };

    assert!(runtime.destroy(&forged).await.is_err());
    assert!(runtime.stop(&forged).await.is_err());
    assert!(dir.path().join("images").exists());
}

#[tokio::test]
#[ignore = "boots a real VM: NAOS_TEST_IMAGE=<qcow2> cargo test -p naos-agent -- --ignored"]
async fn real_image_boots_probes_and_is_cleaned_up() {
    let image = std::env::var("NAOS_TEST_IMAGE").expect("NAOS_TEST_IMAGE names a built image");
    let bytes = fs::read(&image).expect("read image");
    let dir = tempfile::tempdir().expect("tempdir");
    let mut config = config(
        &dir,
        crate::libs::config::DEFAULT_QEMU_BINARY,
        crate::libs::config::DEFAULT_QEMU_IMG.into(),
    );
    config.image_max_bytes = u64::MAX;
    config.virtiofsd_binary = crate::libs::config::DEFAULT_VIRTIOFSD_BINARY.into();
    let runtime = QemuRuntime::new(&config).expect("runtime");
    let source = FakeSource::new(bytes.clone());
    let mut first = run_of(&bytes);
    first.spec.runtime.disk_gib = 8;
    first.spec.runtime.memory_mib = 2048;
    let mut second = first.clone();
    second.id = "run_b".into();
    let workspace = dir.path().join("alpha");
    fs::create_dir(&workspace).expect("mkdir");
    fs::write(workspace.join("notes.txt"), "alpha\n").expect("write");
    with_workspace(&mut first, &workspace, "rw");

    let alpha = runtime.ensure(&first, &source).await.expect("boot run_a");
    let beta = runtime.ensure(&second, &source).await.expect("boot run_b");
    let again = runtime.ensure(&first, &source).await.expect("reuse run_a");

    assert_eq!(again.vm_id, alpha.vm_id);
    assert_ne!(alpha.vm_id, beta.vm_id);
    assert_eq!(source.calls(), 1);
    let boot_log = runtime.paths(&alpha.vm_id).expect("paths").boot_log();
    let deadline = Instant::now() + Duration::from_secs(180);
    loop {
        let log = fs::read_to_string(&boot_log).unwrap_or_default();
        assert!(!log.contains("naos-probe fail"), "{log}");
        if log.contains("naos-ready") && log.contains("naos-probe ok") {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "guest did not report its probes:\n{log}"
        );
        tokio::time::sleep(Duration::from_secs(1)).await;
    }

    let cmdline = |vm: &LocalVm| {
        let pid = find_pid(&vm.vm_id).expect("running");
        let raw = fs::read(format!("/proc/{pid}/cmdline")).expect("cmdline");
        String::from_utf8_lossy(&raw).into_owned()
    };
    for (own, other) in [(&alpha, &beta), (&beta, &alpha)] {
        let other_dir = runtime.paths(&other.vm_id).expect("paths").dir;
        assert!(
            !cmdline(own).contains(other_dir.to_str().expect("utf-8")),
            "{} reaches the directory of {}",
            own.vm_id,
            other.vm_id
        );
    }

    let pid = find_pid(&beta.vm_id).expect("run_b is running");
    kill(Pid::from_raw(pid), Signal::SIGKILL).expect("kill run_b");
    assert!(wait_gone(&beta.vm_id, KILL_TIMEOUT).await);
    let listed = runtime.list().await.expect("list");
    assert!(listed
        .iter()
        .any(|vm| vm.vm_id == beta.vm_id && !vm.running));

    for vm in &listed {
        runtime.destroy(vm).await.expect("destroy");
    }
    assert!(runtime.list().await.expect("list").is_empty());
    runtime
        .images
        .open_verified(&first.spec.image.digest)
        .expect("base image is untouched by the guest");
    let entries: Vec<_> = fs::read_dir(&workspace)
        .expect("workspace")
        .map(|entry| entry.expect("entry").file_name())
        .collect();
    assert_eq!(entries, vec![std::ffi::OsString::from("notes.txt")]);
    assert_eq!(
        fs::read_to_string(workspace.join("notes.txt")).expect("notes"),
        "alpha\n",
        "the guest wrote through the read-only share"
    );
}
