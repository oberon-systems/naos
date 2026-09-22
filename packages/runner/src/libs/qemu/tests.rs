use super::*;

const VM_DIR: &str = "/tmp/runs/vm_00000000000000000000000000000000";
const BASE: &str = "/proc/1/fd/7";

fn args() -> Vec<String> {
    args_with(None)
}

fn args_with(workspace: Option<WorkspaceMode>) -> Vec<String> {
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
        workspace,
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
    for args in [
        args(),
        args_with(Some(WorkspaceMode::ReadOnly)),
        args_with(Some(WorkspaceMode::ReadWrite)),
    ] {
        assert_no_implicit_host_path(&args);
    }
}

fn assert_no_implicit_host_path(args: &[String]) {
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
        value_of(&args, "-device")
            .into_iter()
            .filter(|device| device.starts_with("virtio-blk"))
            .collect::<Vec<_>>(),
        vec!["virtio-blk-pci,drive=disk"]
    );
}

#[test]
fn the_guest_gets_exactly_one_disk_and_the_two_ports_it_needs() {
    let args = args();

    assert_eq!(
        value_of(&args, "-device"),
        vec![
            "virtio-blk-pci,drive=disk",
            "virtio-serial-pci,id=naos-serial",
            "virtserialport,bus=naos-serial.0,chardev=mcp,name=naos.mcp",
            "virtserialport,bus=naos-serial.0,chardev=control,name=naos.ctl",
        ]
    );
    let chardevs = value_of(&args, "-chardev");
    assert!(chardevs
        .contains(&format!("socket,id=mcp,path={VM_DIR}/mcp.sock,server=on,wait=off").as_str()));
    assert!(chardevs.contains(
        &format!("socket,id=control,path={VM_DIR}/control.sock,server=on,wait=off").as_str()
    ));
}

#[test]
fn a_workspace_adds_one_shared_filesystem_and_an_upper_disk_only_when_writable() {
    for (mode, upper) in [
        (WorkspaceMode::ReadOnly, false),
        (WorkspaceMode::ReadWrite, true),
    ] {
        let args = args_with(Some(mode));
        let devices = value_of(&args, "-device");

        assert_eq!(value_of(&args, "-machine"), vec!["q35,memory-backend=mem"]);
        assert_eq!(
            value_of(&args, "-object"),
            vec!["memory-backend-memfd,id=mem,size=1024M,share=on"]
        );
        assert_eq!(
            devices
                .iter()
                .filter(|device| device.starts_with("vhost-user-fs-pci"))
                .collect::<Vec<_>>(),
            vec![&"vhost-user-fs-pci,chardev=workspace,tag=naos-workspace"]
        );
        assert!(value_of(&args, "-chardev")
            .contains(&format!("socket,id=workspace,path={VM_DIR}/fs.sock").as_str()));
        assert_eq!(
            devices.contains(&"virtio-blk-pci,drive=upper,serial=naos-upper"),
            upper,
            "{mode:?}"
        );
        assert_eq!(
            args.iter()
                .any(|arg| arg.contains(&format!("{VM_DIR}/upper.img"))),
            upper
        );
    }
}

#[test]
fn without_a_workspace_the_command_line_has_no_shared_memory() {
    let args = args();

    assert_eq!(value_of(&args, "-machine"), vec!["q35"]);
    assert!(value_of(&args, "-object").is_empty());
}
