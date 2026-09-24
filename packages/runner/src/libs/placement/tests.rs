use super::*;

#[test]
fn the_platform_names_the_distribution() {
    let release = "NAME=\"Ubuntu\"\nPRETTY_NAME=\"Ubuntu 24.04.1 LTS\"\nID=ubuntu\n";

    assert_eq!(
        platform("linux", "x86_64", Some(release)),
        "linux/amd64 \u{b7} Ubuntu 24.04.1 LTS"
    );
}

#[test]
fn without_os_release_the_platform_is_os_and_arch() {
    assert_eq!(platform("linux", "aarch64", None), "linux/arm64");
    assert_eq!(
        platform("linux", "riscv64", Some("ID=alpha\n")),
        "linux/riscv64"
    );
}

#[test]
fn control_characters_never_leave_the_host() {
    let release = "PRETTY_NAME=\"Alpha\u{1b}[31m Linux\"\n";

    assert_eq!(
        platform("linux", "x86_64", Some(release)),
        "linux/amd64 \u{b7} Alpha[31m Linux"
    );
}

#[test]
fn an_overlong_platform_is_cut() {
    let release = format!("PRETTY_NAME={}\n", "a".repeat(400));

    assert_eq!(
        platform("linux", "x86_64", Some(&release)).len(),
        MAX_PLATFORM
    );
}

#[test]
fn a_host_name_outside_the_alphabet_is_not_sent() {
    assert_eq!(
        host("alpha-01.example.com").as_deref(),
        Some("alpha-01.example.com")
    );
    assert_eq!(host("alpha 01"), None);
    assert_eq!(host(""), None);
    assert_eq!(host(&"a".repeat(254)), None);
}
