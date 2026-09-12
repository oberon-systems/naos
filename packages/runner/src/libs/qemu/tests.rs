use super::*;

const VM_DIR: &str = "/tmp/runs/vm_00000000000000000000000000000000";
const BASE: &str = "/proc/1/fd/7";

fn args() -> Vec<String> {
    let runtime = RuntimeSpec {
        cpu: 2,
        memory_mib: 1024,
        disk_gib: 4,
    };
    argv(
        "vm_00000000000000000000000000000000",
        Path::new(BASE),
        &VmPaths::new(VM_DIR.into()),
        &runtime,
    )
    .into_iter()
    .map(|arg| arg.into_string().expect("utf-8"))
    .collect()
}

fn value_of<'a>(args: &'a [String], flag: &str) -> Vec<&'a str> {
    args.windows(2)
        .filter(|pair| pair[0] == flag)
        .map(|pair| pair[1].as_str())
        .collect()
}

#[test]
fn vm_has_no_network_no_defaults_and_a_sandbox() {
    let args = args();

    assert_eq!(value_of(&args, "-nic"), vec!["none"]);
    assert!(args.contains(&"-nodefaults".to_owned()));
    assert!(args.contains(&"-no-user-config".to_owned()));
    assert_eq!(value_of(&args, "-sandbox"), vec![SANDBOX]);
    assert_eq!(
        value_of(&args, "-name"),
        vec!["naos-vm_00000000000000000000000000000000"]
    );
}

#[test]
fn no_host_path_reaches_the_guest_implicitly() {
    let args = args();

    for forbidden in [
        "-virtfs",
        "-fsdev",
        "-hda",
        "-drive",
        "-netdev",
        "-kernel",
        "-cdrom",
        "-usb",
        "-mon",
        "-daemonize",
    ] {
        assert!(!args.contains(&forbidden.to_owned()), "{forbidden}");
    }
    for arg in args.iter().filter(|arg| arg.contains('/')) {
        assert!(
            arg.contains(&format!("{VM_DIR}/")) || arg.contains(BASE),
            "unexpected host path in {arg}"
        );
    }
}

#[test]
fn base_is_read_only_under_a_writable_overlay() {
    let args = args();
    let nodes: Vec<serde_json::Value> = value_of(&args, "-blockdev")
        .into_iter()
        .map(|raw| serde_json::from_str(raw).expect("json"))
        .collect();

    let node = |name: &str| {
        nodes
            .iter()
            .find(|node| node["node-name"] == name)
            .unwrap_or_else(|| panic!("{name}"))
    };
    assert_eq!(node("base-file")["filename"], BASE);
    assert_eq!(node("base-file")["read-only"], true);
    assert_eq!(node("base")["read-only"], true);
    assert_eq!(node("disk")["backing"], "base");
    assert!(node("disk").get("read-only").is_none());
    assert_eq!(
        value_of(&args, "-device"),
        vec!["virtio-blk-pci,drive=disk"]
    );
}
