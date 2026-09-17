use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::{json, Value};
use tempfile::TempDir;

use super::*;
use crate::libs::testing::{e2fs_tool, ext4_image, ext4_image_with};

const PREAMBLE: &str = "mkdir data\nmkdir work\ncd data\n";

struct Fixture {
    dir: TempDir,
    host: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        let host = dir.path().join("workspace");
        fs::create_dir(&host).expect("workspace");
        Self { dir, host }
    }

    fn host_file(&self, path: &str, body: &str) {
        let path = self.host.join(path);
        fs::create_dir_all(path.parent().expect("parent")).expect("mkdir");
        write_file(&path, body);
    }

    /// A file debugfs copies into the disk as `name`, with the same mode the host files get.
    fn source(&self, name: &str, body: &str) {
        write_file(&self.dir.path().join(name), body);
    }

    fn upper(&self, script: &str) -> PathBuf {
        ext4_image(self.dir.path(), "upper.img", &format!("{PREAMBLE}{script}"))
    }

    fn collect(&self, script: &str) -> Result<Diff, AgentError> {
        collect(&self.upper(script), &self.host)
    }
}

fn write_file(path: &Path, body: &str) {
    fs::write(path, body).expect("write");
    fs::set_permissions(path, fs::Permissions::from_mode(0o644)).expect("chmod");
}

fn entries(diff: &Diff) -> Value {
    serde_json::to_value(&diff.entries)
        .expect("json")
        .as_array()
        .expect("array")
        .iter()
        .map(|entry| {
            let mut entry = entry.clone();
            if let Some(object) = entry.as_object_mut() {
                object.remove("sha256");
            }
            entry
        })
        .collect()
}

fn sha(body: &str) -> String {
    crate::libs::ids::hex(&Sha256::digest(body.as_bytes()))
}

fn refused(outcome: Result<Diff, AgentError>, reason: &str) {
    match outcome {
        Err(AgentError::Runtime(message)) => assert!(message.contains(reason), "{message}"),
        other => panic!("expected a refusal with {reason:?}, got {other:?}"),
    }
}

fn patch(image: &Path, offset: u64, bytes: &[u8]) {
    use std::os::unix::fs::FileExt;
    let file = fs::OpenOptions::new()
        .write(true)
        .open(image)
        .expect("open");
    file.write_all_at(bytes, offset).expect("patch");
}

fn read(image: &Path, offset: u64, len: usize) -> Vec<u8> {
    use std::os::unix::fs::FileExt;
    let mut buffer = vec![0u8; len];
    fs::File::open(image)
        .expect("open")
        .read_exact_at(&mut buffer, offset)
        .expect("read");
    buffer
}

fn data_block(image: &Path) -> u64 {
    let output = Command::new(e2fs_tool("debugfs"))
        .arg(image)
        .args(["-R", "blocks /data"])
        .output()
        .expect("debugfs");
    String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse::<u64>()
        .expect("one block")
        * 4096
}

#[test]
fn changes_are_created_modified_deleted_and_renamed() {
    let fixture = Fixture::new();
    fixture.host_file("notes.txt", "alpha\n");
    fixture.host_file("old.txt", "old\n");
    fixture.host_file("moved.txt", "moved content\n");
    fixture.host_file("keep.txt", "keep\n");
    fixture.host_file("mode.txt", "mode\n");
    fixture.source("notes", "beta\n");
    fixture.source("added", "gamma\n");
    fixture.source("moved", "moved content\n");
    fixture.source("keep", "keep\n");
    fixture.source("mode", "mode\n");

    let diff = fixture
        .collect(
            "write notes notes.txt\n\
             write added added.txt\n\
             mknod old.txt c 0 0\n\
             mknod moved.txt c 0 0\n\
             write moved renamed.txt\n\
             write keep keep.txt\n\
             write mode mode.txt\n\
             set_inode_field mode.txt mode 0100755\n\
             mkdir newdir\n",
        )
        .expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "added.txt", "change": "created", "kind": "file", "size": 6, "mode": 0o644},
            {"path": "mode.txt", "change": "modified", "kind": "file", "size": 5, "mode": 0o755},
            {"path": "newdir", "change": "created", "kind": "dir"},
            {"path": "notes.txt", "change": "modified", "kind": "file", "size": 5, "mode": 0o644},
            {"path": "old.txt", "change": "deleted", "kind": "file", "size": 4, "mode": 0o644},
            {"path": "renamed.txt", "change": "renamed", "kind": "file", "from": "moved.txt", "size": 14, "mode": 0o644},
        ])
    );
    assert_eq!(diff.entries[0].sha256, Some(sha("gamma\n")));
    assert_eq!(diff.rejected(), 0);
}

// Alpine's mkfs.ext4 sets this feature, and the reader ignores checksums anyway.
#[test]
fn a_disk_with_a_checksum_seed_is_read() {
    let fixture = Fixture::new();
    fixture.host_file("notes.txt", "alpha\n");
    fixture.source("notes", "beta\n");
    let upper = ext4_image_with(
        fixture.dir.path(),
        "upper.img",
        &["-O", "metadata_csum_seed"],
        &format!("{PREAMBLE}write notes notes.txt\n"),
    );

    let diff = collect(&upper, &fixture.host).expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "notes.txt", "change": "modified", "kind": "file", "size": 5, "mode": 0o644},
        ])
    );
}

#[test]
fn the_same_disk_gives_the_same_diff() {
    let fixture = Fixture::new();
    fixture.host_file("b.txt", "beta\n");
    fixture.host_file("a/c.txt", "gamma\n");
    fixture.source("alpha", "alpha\n");
    let upper =
        fixture.upper("write alpha z.txt\nwrite alpha y.txt\nmknod a c 0 0\nmknod b.txt c 0 0\n");

    let first =
        serde_json::to_vec(&collect(&upper, &fixture.host).expect("collect")).expect("json");
    let second =
        serde_json::to_vec(&collect(&upper, &fixture.host).expect("collect")).expect("json");

    assert_eq!(first, second);
}

#[test]
fn an_opaque_directory_hides_what_the_host_had() {
    let fixture = Fixture::new();
    fixture.host_file("src/a.rs", "a\n");
    fixture.host_file("src/b.rs", "b\n");
    fixture.host_file("src/inner/c.rs", "c\n");
    fixture.source("b", "b\n");

    let diff = fixture
        .collect("mkdir src\nea_set src trusted.overlay.opaque y\nwrite b src/b.rs\n")
        .expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "src/a.rs", "change": "deleted", "kind": "file", "size": 2, "mode": 0o644},
            {"path": "src/inner", "change": "deleted", "kind": "dir"},
            {"path": "src/inner/c.rs", "change": "deleted", "kind": "file", "size": 2, "mode": 0o644},
        ])
    );
}

#[test]
fn a_whiteout_deletes_a_whole_directory_and_is_ignored_where_the_host_has_nothing() {
    let fixture = Fixture::new();
    fixture.host_file("docs/x.md", "x\n");

    let diff = fixture
        .collect("mknod docs c 0 0\nmknod ghost c 0 0\nlink docs shared\n")
        .expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "docs", "change": "deleted", "kind": "dir"},
            {"path": "docs/x.md", "change": "deleted", "kind": "file", "size": 2, "mode": 0o644},
        ])
    );
}

#[test]
fn a_file_in_place_of_a_directory_deletes_it_first() {
    let fixture = Fixture::new();
    fixture.host_file("thing/f", "f\n");
    fixture.source("thing", "thing\n");

    let diff = fixture.collect("write thing thing\n").expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "thing", "change": "deleted", "kind": "dir"},
            {"path": "thing", "change": "created", "kind": "file", "size": 6, "mode": 0o644},
            {"path": "thing/f", "change": "deleted", "kind": "file", "size": 2, "mode": 0o644},
        ])
    );
}

#[test]
fn symlinks_are_reported_and_never_followed() {
    let fixture = Fixture::new();
    let outside = fixture.dir.path().join("outside");
    fs::create_dir(&outside).expect("outside");
    write_file(&outside.join("file"), "secret\n");
    symlink(&outside, fixture.host.join("hostlink")).expect("symlink");
    fixture.source("file", "secret\n");

    let diff = fixture
        .collect(
            "symlink etc /etc/passwd\n\
             symlink up ../../outside\n\
             mkdir hostlink\n\
             write file hostlink/file\n",
        )
        .expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "etc", "change": "created", "kind": "symlink", "target": "/etc/passwd"},
            {"path": "hostlink", "change": "deleted", "kind": "symlink", "target": outside.to_str().expect("utf-8")},
            {"path": "hostlink", "change": "created", "kind": "dir"},
            {"path": "hostlink/file", "change": "created", "kind": "file", "size": 7, "mode": 0o644},
            {"path": "up", "change": "created", "kind": "symlink", "target": "../../outside"},
        ])
    );
    assert_eq!(
        fs::read_to_string(outside.join("file")).expect("read"),
        "secret\n"
    );
}

#[test]
fn unsafe_objects_are_rejected_and_the_rest_is_collected() {
    let fixture = Fixture::new();
    fixture.source("x", "x\n");
    let script = "write x hard\n\
                  link hard hard2\n\
                  set_inode_field hard links_count 2\n\
                  mknod pipe p\n\
                  mknod null c 1 3\n\
                  mknod disk b 8 0\n\
                  write x suid\n\
                  set_inode_field suid mode 0104644\n\
                  write x big\n\
                  set_inode_field big size 0x7fffffffff\n\
                  write x \u{fffd}\n\
                  write x fine\n";
    let upper = fixture.upper(script);
    let name = data_block(&upper);
    let block = read(&upper, name, 4096);
    let at = block
        .windows(3)
        .position(|window| window == "\u{fffd}".as_bytes())
        .expect("name");
    patch(&upper, name + at as u64, &[0xff, 0xfe, 0xfd]);

    let diff = collect(&upper, &fixture.host).expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "big", "change": "rejected", "kind": "file", "reason": "larger than the upper disk"},
            {"path": "disk", "change": "rejected", "kind": "other", "reason": "special file"},
            {"path": "fine", "change": "created", "kind": "file", "size": 2, "mode": 0o644},
            {"path": "hard", "change": "rejected", "kind": "file", "reason": "hardlink"},
            {"path": "hard2", "change": "rejected", "kind": "file", "reason": "hardlink"},
            {"path": "null", "change": "rejected", "kind": "other", "reason": "special file"},
            {"path": "pipe", "change": "rejected", "kind": "other", "reason": "special file"},
            {"path": "suid", "change": "rejected", "kind": "file", "reason": "special permission bits"},
            {"path": "\u{fffd}\u{fffd}\u{fffd}", "change": "rejected", "kind": "file", "reason": "name is not utf-8"},
        ])
    );
    assert_eq!(diff.rejected(), 8);
}

#[test]
fn overlay_attributes_this_format_never_writes_fail_the_collection() {
    for script in [
        "mkdir moved\nea_set moved trusted.overlay.redirect /elsewhere\n",
        "write x meta\nea_set meta trusted.overlay.metacopy y\n",
        "mkdir mixed\nea_set mixed trusted.overlay.opaque x\n",
        "write x wh\nea_set wh trusted.overlay.whiteout y\n",
    ] {
        let fixture = Fixture::new();
        fixture.source("x", "x\n");

        refused(fixture.collect(script), "overlay attribute");
    }
}

#[test]
fn a_directory_linked_twice_fails() {
    let fixture = Fixture::new();

    refused(fixture.collect("mkdir a\nlink a b\n"), "linked twice");
}

#[test]
fn a_damaged_or_unfinished_disk_is_refused() {
    let superblock = 1024;
    let cases: [(&str, u64, &[u8], &str); 4] = [
        ("magic", superblock + 0x38, &[0, 0], "no ext4 superblock"),
        ("state", superblock + 0x3A, &[0, 0], "not unmounted cleanly"),
        (
            "recover",
            superblock + 0x60,
            &[0xC6, 0x02, 0, 0],
            "unsupported features",
        ),
        (
            "blocks",
            superblock + 0x4,
            &[0xff, 0xff, 0xff, 0x7f],
            "block count",
        ),
    ];
    for (name, offset, bytes, reason) in cases {
        let fixture = Fixture::new();
        let upper = fixture.upper("mkdir src\n");
        patch(&upper, offset, bytes);

        eprintln!("case {name}");
        refused(collect(&upper, &fixture.host), reason);
    }
}

#[test]
fn a_broken_directory_or_extent_is_refused() {
    let fixture = Fixture::new();
    fixture.source("x", "x\n");
    let upper = fixture.upper("write x a\n");
    let dir = data_block(&upper);
    assert_eq!(read(&upper, dir + 32, 1), b"a");

    patch(&upper, dir + 32, b"/");
    refused(collect(&upper, &fixture.host), "not a file name");

    patch(&upper, dir + 4, &[0, 0]);
    refused(collect(&upper, &fixture.host), "entry length");

    let extents = fixture.upper("write x a\nset_inode_field a block[5] 0x7fffff\n");
    refused(collect(&extents, &fixture.host), "extent outside the disk");
}

#[test]
fn a_disk_the_guest_never_formatted_or_laid_out_is_refused() {
    let fixture = Fixture::new();
    let blank = fixture.dir.path().join("blank.img");
    fs::File::create(&blank)
        .and_then(|file| file.set_len(32 * 1024 * 1024))
        .expect("blank");
    refused(collect(&blank, &fixture.host), "no ext4 superblock");

    let bare = ext4_image(fixture.dir.path(), "bare.img", "mkdir work\n");
    refused(collect(&bare, &fixture.host), "no data directory");
}

#[test]
fn a_workspace_reached_through_a_symlink_is_refused() {
    let fixture = Fixture::new();
    let upper = fixture.upper("");
    let link = fixture.dir.path().join("link");
    symlink(&fixture.host, &link).expect("symlink");

    assert!(collect(&upper, &link).is_err());
    assert!(collect(&upper, &fixture.host)
        .expect("collect")
        .entries
        .is_empty());
}
