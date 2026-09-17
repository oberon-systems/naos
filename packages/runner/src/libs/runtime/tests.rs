use std::os::unix::fs::PermissionsExt;

use tempfile::TempDir;

use super::*;
use crate::libs::api::RunStatus;
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
        image_max_bytes: 1024 * 1024,
    }
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
    let runtime = QemuRuntime::new(&config).expect("runtime");
    let source = FakeSource::new(bytes.clone());
    let mut first = run_of(&bytes);
    first.spec.runtime.disk_gib = 8;
    first.spec.runtime.memory_mib = 2048;
    let mut second = first.clone();
    second.id = "run_b".into();

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
}
