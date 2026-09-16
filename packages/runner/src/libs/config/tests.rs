use std::io::Write;
use std::os::unix::fs::symlink;

use serial_test::serial;
use tempfile::NamedTempFile;

use super::*;
use tools::EnvSetter;

const API_URL: &str = "https://api.example.com/naos";
const STATE_DIR: &str = "/var/lib/naos-agent";
const TOKEN_FILE: &str = "/etc/naos-agent/enrollment";

fn raw() -> RawConfig {
    RawConfig {
        api_url: API_URL.into(),
        name: "alpha".into(),
        capacity: None,
        state_dir: STATE_DIR.into(),
        enrollment_token_file: TOKEN_FILE.into(),
    }
}

fn full_env(env: &mut EnvSetter) {
    env.set("NAOS_AGENT_API_URL", API_URL);
    env.set("NAOS_AGENT_NAME", "alpha");
    env.set("NAOS_AGENT_STATE_DIR", STATE_DIR);
    env.set("NAOS_AGENT_ENROLLMENT_TOKEN_FILE", TOKEN_FILE);
    env.del("NAOS_AGENT_CAPACITY");
    env.del("NAOS_AGENT_ENV_FILE");
}

fn runtime() -> RuntimeConfig {
    RuntimeConfig::resolve(
        RawRuntimeConfig::default(),
        &UserDirs {
            home: Some("/home/alpha".into()),
            ..UserDirs::default()
        },
    )
    .expect("runtime config")
}

#[test]
fn full_config_parses_with_default_capacity() {
    let config = Config::parse(raw(), || Ok(runtime())).expect("valid config");

    assert_eq!(config.api_url.as_str(), "https://api.example.com/naos/");
    assert_eq!(config.capacity, DEFAULT_CAPACITY);
    assert_eq!(config.state_dir, PathBuf::from(STATE_DIR));
}

#[test]
fn runtime_dirs_default_to_xdg_locations() {
    let dirs = UserDirs {
        home: Some("/home/alpha".into()),
        data_home: None,
        state_home: Some("/srv/state/alpha".into()),
    };

    let config = RuntimeConfig::resolve(RawRuntimeConfig::default(), &dirs).expect("resolved");

    assert_eq!(
        config.image_dir,
        PathBuf::from("/home/alpha/.local/share/naos/vms")
    );
    assert_eq!(config.vm_dir, PathBuf::from("/srv/state/alpha/naos/runs"));
    assert_eq!(config.qemu_binary, PathBuf::from(DEFAULT_QEMU_BINARY));
    assert_eq!(config.git_binary, PathBuf::from(DEFAULT_GIT_BINARY));
    assert_eq!(config.image_max_bytes, DEFAULT_IMAGE_MAX_BYTES);
}

#[test]
fn runtime_dirs_need_a_home_or_an_explicit_path() {
    assert!(RuntimeConfig::resolve(RawRuntimeConfig::default(), &UserDirs::default()).is_err());

    let explicit = RawRuntimeConfig {
        image_dir: Some("/srv/naos/vms".into()),
        vm_dir: Some("/srv/naos/runs".into()),
        ..RawRuntimeConfig::default()
    };
    assert!(RuntimeConfig::resolve(explicit, &UserDirs::default()).is_ok());
}

#[test]
fn paths_that_would_break_qemu_options_are_refused() {
    for bad in [
        "/srv/naos/runs,readonly=off",
        "relative/runs",
        "/srv/naos/\nruns",
    ] {
        let raw = RawRuntimeConfig {
            vm_dir: Some(bad.into()),
            ..RawRuntimeConfig::default()
        };
        let dirs = UserDirs {
            home: Some("/home/alpha".into()),
            ..UserDirs::default()
        };
        assert!(RuntimeConfig::resolve(raw, &dirs).is_err(), "{bad}");
    }
    let zero = RawRuntimeConfig {
        image_max_bytes: Some(0),
        image_dir: Some("/srv/naos/vms".into()),
        vm_dir: Some("/srv/naos/runs".into()),
        ..RawRuntimeConfig::default()
    };
    assert!(RuntimeConfig::resolve(zero, &UserDirs::default()).is_err());
}

#[test]
fn private_dirs_are_created_and_shared_ones_refused() {
    let dir = tempfile::tempdir().expect("tempdir");
    let fresh = dir.path().join("naos/vms");

    prepare_private_dir(&fresh).expect("created");
    assert_eq!(
        fs::metadata(&fresh).expect("meta").permissions().mode() & 0o777,
        0o700
    );

    let shared = dir.path().join("shared");
    fs::create_dir(&shared).expect("mkdir");
    fs::set_permissions(&shared, fs::Permissions::from_mode(0o777)).expect("chmod");
    assert!(prepare_private_dir(&shared).is_err());

    let link = dir.path().join("link");
    symlink(&fresh, &link).expect("symlink");
    assert!(prepare_private_dir(&link).is_err());
}

#[test]
fn plain_http_is_only_allowed_to_loopback() {
    for ok in [
        "https://api.example.com",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://localhost:8000",
    ] {
        assert!(parse_api_url(ok).is_ok(), "{ok}");
    }
    for bad in [
        "http://api.example.com",
        "http://192.0.2.10:8000",
        "ftp://api.example.com",
        "https://user:pass@api.example.com",
        "https://api.example.com/?x=1",
        "not a url",
    ] {
        assert!(parse_api_url(bad).is_err(), "{bad}");
    }
}

#[test]
fn names_and_capacity_are_validated() {
    assert!(parse_name("alpha-01.beta_2").is_ok());
    for bad in ["", "-alpha", "alpha beta", "alpha/beta", &"a".repeat(65)] {
        assert!(parse_name(bad).is_err(), "{bad}");
    }
    assert_eq!(check_capacity(0).ok(), Some(0));
    assert_eq!(check_capacity(MAX_CAPACITY).ok(), Some(MAX_CAPACITY));
    assert!(check_capacity(MAX_CAPACITY + 1).is_err());
}

#[test]
#[serial]
fn load_reads_the_prefixed_environment() {
    let mut env = EnvSetter::new();
    full_env(&mut env);
    env.set("NAOS_AGENT_CAPACITY", "4");

    let config = load().expect("valid config");

    assert_eq!(config.name, "alpha");
    assert_eq!(config.capacity, 4);
    assert_eq!(config.enrollment_token_file, PathBuf::from(TOKEN_FILE));
}

#[test]
#[serial]
fn every_required_key_is_enforced() {
    for missing in [
        "NAOS_AGENT_API_URL",
        "NAOS_AGENT_NAME",
        "NAOS_AGENT_STATE_DIR",
        "NAOS_AGENT_ENROLLMENT_TOKEN_FILE",
    ] {
        let mut env = EnvSetter::new();
        full_env(&mut env);
        env.del(missing);

        let err = load().expect_err(missing).to_string();
        assert!(err.contains(missing), "{missing}: {err}");
    }
}

#[test]
#[serial]
fn an_env_file_is_read_only_when_it_is_named() {
    let mut file = NamedTempFile::new().expect("temp file");
    writeln!(file, "NAOS_AGENT_NAME=beta").expect("write");
    let path = file.path().to_str().expect("utf-8 path").to_owned();

    let mut env = EnvSetter::new();
    full_env(&mut env);
    env.del("NAOS_AGENT_NAME");
    assert!(load().is_err(), "the name is not set without the file");

    env.set("NAOS_AGENT_ENV_FILE", &path);
    assert_eq!(load().expect("valid config").name, "beta");
    env.del("NAOS_AGENT_NAME");
}

#[test]
#[serial]
fn a_named_env_file_that_is_missing_is_an_error() {
    let mut env = EnvSetter::new();
    full_env(&mut env);
    env.set("NAOS_AGENT_ENV_FILE", "/nonexistent/naos-agent.env");

    let err = load().expect_err("missing env file").to_string();
    assert!(err.contains("NAOS_AGENT_ENV_FILE"), "{err}");
}
