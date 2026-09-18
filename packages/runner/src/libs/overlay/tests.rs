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
                object.remove("base_sha256");
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
            {"path": "mode.txt", "change": "modified", "kind": "file", "size": 5, "mode": 0o755, "base_mode": 0o644},
            {"path": "newdir", "change": "created", "kind": "dir"},
            {"path": "notes.txt", "change": "modified", "kind": "file", "size": 5, "mode": 0o644, "base_mode": 0o644},
            {"path": "old.txt", "change": "deleted", "kind": "file", "size": 4, "mode": 0o644},
            {"path": "renamed.txt", "change": "renamed", "kind": "file", "from": "moved.txt", "size": 14, "mode": 0o644, "base_mode": 0o644},
        ])
    );
    assert_eq!(diff.entries[0].sha256, Some(sha("gamma\n")));
    assert_eq!(diff.entries[3].base_sha256, Some(sha("alpha\n")));
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
            {"path": "notes.txt", "change": "modified", "kind": "file", "size": 5, "mode": 0o644, "base_mode": 0o644},
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
fn symlinks_are_reported_never_followed_and_kept_inside() {
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
             write file hostlink/file\n\
             symlink hostlink/back ../etc\n\
             symlink hostlink/away ../../x\n",
        )
        .expect("collect");

    assert_eq!(
        entries(&diff),
        json!([
            {"path": "etc", "change": "rejected", "kind": "symlink", "reason": "symlink leaves the workspace"},
            {"path": "hostlink", "change": "deleted", "kind": "symlink", "target": outside.to_str().expect("utf-8")},
            {"path": "hostlink", "change": "created", "kind": "dir"},
            {"path": "hostlink/away", "change": "rejected", "kind": "symlink", "reason": "symlink leaves the workspace"},
            {"path": "hostlink/back", "change": "created", "kind": "symlink", "target": "../etc"},
            {"path": "hostlink/file", "change": "created", "kind": "file", "size": 7, "mode": 0o644},
            {"path": "up", "change": "rejected", "kind": "symlink", "reason": "symlink leaves the workspace"},
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

#[test]
fn paths_that_run_on_the_host_are_marked_sensitive() {
    let fixture = Fixture::new();
    fixture.source("x", "x\n");

    let diff = fixture
        .collect(
            "mkdir .git\nmkdir .git/hooks\nwrite x .git/hooks/pre-commit\n\
             mkdir .github\nmkdir .github/workflows\nwrite x .github/workflows/ci.yml\n\
             write x Makefile\nwrite x Dockerfile.dev\nmkdir src\nwrite x src/main.rs\n",
        )
        .expect("collect");

    let flagged: Vec<(&str, bool)> = diff
        .entries
        .iter()
        .map(|entry| (entry.path.as_str(), entry.sensitive))
        .collect();
    assert_eq!(
        flagged,
        vec![
            (".git", true),
            (".git/hooks", true),
            (".git/hooks/pre-commit", true),
            (".github", true),
            (".github/workflows", true),
            (".github/workflows/ci.yml", true),
            ("Dockerfile.dev", true),
            ("Makefile", true),
            ("src", false),
            ("src/main.rs", false),
        ]
    );
    assert!(sensitive("vendor/lib/.git/config"));
    assert!(sensitive("deploy/main.tf"));
    assert!(!sensitive("docs/github.md"));
}

fn decide(paths: &[&str]) -> merge::Decision {
    merge::Decision {
        paths: paths.iter().map(|path| (*path).to_owned()).collect(),
        resolutions: std::collections::BTreeMap::new(),
    }
}

fn everything(diff: &Diff) -> merge::Decision {
    merge::Decision {
        paths: diff
            .entries
            .iter()
            .filter(|entry| entry.change != Change::Rejected)
            .map(|entry| entry.path.clone())
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect(),
        resolutions: std::collections::BTreeMap::new(),
    }
}

impl Fixture {
    fn merge_dir(&self) -> PathBuf {
        self.dir.path().join("merge")
    }

    fn merge(&self, upper: &Path, diff: &Diff, decision: &merge::Decision) -> merge::Outcome {
        merge::merge(upper, Some(&self.host), diff, decision, &self.merge_dir()).expect("merge")
    }

    fn host_text(&self, path: &str) -> String {
        fs::read_to_string(self.host.join(path)).expect("host file")
    }

    fn kept(&self, area: &str, path: &str) -> String {
        fs::read_to_string(self.merge_dir().join(area).join(path)).expect("kept file")
    }

    fn has(&self, path: &str) -> bool {
        fs::symlink_metadata(self.host.join(path)).is_ok()
    }

    fn left_behind(&self) -> bool {
        let staging = fs::read_dir(&self.host).expect("workspace").any(|entry| {
            entry
                .expect("entry")
                .file_name()
                .to_string_lossy()
                .starts_with(".naos-merge-")
        });
        staging || self.merge_dir().join("journal").exists()
    }
}

fn applied(outcome: merge::Outcome) -> merge::Report {
    match outcome {
        merge::Outcome::Applied(report) => report,
        other => panic!("expected the merge to apply, got {other:?}"),
    }
}

fn conflicts(outcome: merge::Outcome) -> Vec<(String, String)> {
    match outcome {
        merge::Outcome::Conflict { conflicts } => conflicts
            .into_iter()
            .map(|conflict| (conflict.path, conflict.reason))
            .collect(),
        other => panic!("expected a conflict, got {other:?}"),
    }
}

#[test]
fn a_merge_applies_every_change_and_keeps_the_host_versions() {
    let fixture = Fixture::new();
    fixture.host_file("notes.txt", "alpha\n");
    fixture.host_file("old.txt", "old\n");
    fixture.host_file("moved.txt", "moved content\n");
    fixture.host_file("docs/x.md", "x\n");
    fixture.source("notes", "beta\n");
    fixture.source("added", "gamma\n");
    fixture.source("moved", "moved content\n");
    let upper = fixture.upper(
        "write notes notes.txt\n\
         mknod old.txt c 0 0\n\
         mknod moved.txt c 0 0\n\
         write moved renamed.txt\n\
         mknod docs c 0 0\n\
         mkdir newdir\n\
         write added newdir/added.txt\n\
         symlink link notes.txt\n",
    );
    let diff = collect(&upper, &fixture.host).expect("collect");

    let report = applied(fixture.merge(&upper, &diff, &everything(&diff)));

    assert_eq!(
        report.applied,
        [
            "docs",
            "docs/x.md",
            "link",
            "newdir",
            "newdir/added.txt",
            "notes.txt",
            "old.txt",
            "renamed.txt"
        ]
    );
    assert_eq!(fixture.host_text("notes.txt"), "beta\n");
    assert_eq!(fixture.host_text("newdir/added.txt"), "gamma\n");
    assert_eq!(fixture.host_text("renamed.txt"), "moved content\n");
    assert_eq!(
        fs::read_link(fixture.host.join("link")).expect("link"),
        Path::new("notes.txt")
    );
    assert!(!fixture.has("old.txt") && !fixture.has("moved.txt") && !fixture.has("docs"));
    assert_eq!(fixture.kept("backup", "notes.txt"), "alpha\n");
    assert_eq!(fixture.kept("backup", "old.txt"), "old\n");
    assert_eq!(fixture.kept("backup", "moved.txt"), "moved content\n");
    assert_eq!(fixture.kept("backup", "docs/x.md"), "x\n");
    assert!(!fixture.left_behind());
    assert_eq!(
        applied(fixture.merge(&upper, &diff, &everything(&diff))),
        report
    );
}

#[test]
fn an_empty_decision_changes_nothing() {
    let fixture = Fixture::new();
    fixture.host_file("notes.txt", "alpha\n");
    fixture.source("notes", "beta\n");
    let upper = fixture.upper("write notes notes.txt\n");
    let diff = collect(&upper, &fixture.host).expect("collect");

    let report = applied(fixture.merge(&upper, &diff, &decide(&[])));

    assert_eq!(report, merge::Report::default());
    assert_eq!(fixture.host_text("notes.txt"), "alpha\n");
    assert!(!fixture.merge_dir().join("backup").exists());
    assert!(!fixture.left_behind());
}

#[test]
fn a_host_changed_since_collection_is_a_conflict_and_nothing_is_written() {
    let fixture = Fixture::new();
    fixture.host_file("notes.txt", "alpha\n");
    fixture.source("notes", "beta\n");
    fixture.source("added", "gamma\n");
    let upper = fixture.upper("write notes notes.txt\nwrite added added.txt\n");
    let diff = collect(&upper, &fixture.host).expect("collect");
    fixture.host_file("notes.txt", "local edit\n");

    let found = conflicts(fixture.merge(&upper, &diff, &everything(&diff)));

    assert_eq!(
        found,
        [(
            "notes.txt".to_owned(),
            "the host changed since collection".to_owned()
        )]
    );
    assert_eq!(fixture.host_text("notes.txt"), "local edit\n");
    assert!(!fixture.has("added.txt"));
    assert!(!fixture.left_behind());
}

#[test]
fn conflicts_are_resolved_by_skip_take_or_export() {
    let fixture = Fixture::new();
    for name in ["a.txt", "b.txt", "c.txt"] {
        fixture.host_file(name, "host\n");
    }
    fixture.source("agent", "agent\n");
    let upper = fixture.upper("write agent a.txt\nwrite agent b.txt\nwrite agent c.txt\n");
    let diff = collect(&upper, &fixture.host).expect("collect");
    for name in ["a.txt", "b.txt", "c.txt"] {
        fixture.host_file(name, "local\n");
    }
    let mut decision = everything(&diff);
    decision.resolutions.extend([
        ("a.txt".to_owned(), merge::Resolution::Skip),
        ("b.txt".to_owned(), merge::Resolution::Take),
        ("c.txt".to_owned(), merge::Resolution::Export),
    ]);

    let report = applied(fixture.merge(&upper, &diff, &decision));

    assert_eq!(report.applied, ["b.txt"]);
    assert_eq!(report.skipped, ["a.txt"]);
    assert_eq!(report.exported, ["c.txt"]);
    assert_eq!(report.backed_up, ["b.txt"]);
    assert_eq!(fixture.host_text("a.txt"), "local\n");
    assert_eq!(fixture.host_text("b.txt"), "agent\n");
    assert_eq!(fixture.host_text("c.txt"), "local\n");
    assert_eq!(fixture.kept("backup", "b.txt"), "local\n");
    assert_eq!(fixture.kept("export", "c.txt"), "agent\n");
    assert!(!fixture.left_behind());
}

#[test]
fn a_directory_swapped_for_a_symlink_is_never_written_through() {
    let fixture = Fixture::new();
    fixture.host_file("sub/keep.txt", "keep\n");
    fixture.source("new", "new\n");
    let upper = fixture.upper("mkdir sub\nwrite new sub/new.txt\n");
    let diff = collect(&upper, &fixture.host).expect("collect");
    let outside = fixture.dir.path().join("outside");
    fs::create_dir(&outside).expect("outside");
    fs::rename(fixture.host.join("sub"), fixture.dir.path().join("sub")).expect("move");
    symlink(&outside, fixture.host.join("sub")).expect("symlink");

    let found = conflicts(fixture.merge(&upper, &diff, &everything(&diff)));

    assert_eq!(
        found,
        [(
            "sub/new.txt".to_owned(),
            "the parent directory is gone".to_owned()
        )]
    );
    assert_eq!(fs::read_dir(&outside).expect("outside").count(), 0);
    assert!(!fixture.left_behind());
}

#[test]
fn a_failure_midway_rolls_back_what_was_applied() {
    // Root ignores the read-only directory this test fails the merge with.
    if std::os::unix::fs::MetadataExt::uid(&fs::metadata("/proc/self").expect("proc")) == 0 {
        return;
    }
    let fixture = Fixture::new();
    fixture.host_file("a.txt", "a\n");
    fixture.host_file("locked/keep", "keep\n");
    fixture.source("agent", "agent\n");
    let upper = fixture.upper("write agent a.txt\nmkdir locked\nwrite agent locked/b.txt\n");
    let diff = collect(&upper, &fixture.host).expect("collect");
    let locked = fixture.host.join("locked");
    fs::set_permissions(&locked, fs::Permissions::from_mode(0o555)).expect("chmod");

    let found = conflicts(fixture.merge(&upper, &diff, &everything(&diff)));

    fs::set_permissions(&locked, fs::Permissions::from_mode(0o755)).expect("chmod");
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].0, "locked/b.txt");
    assert!(found[0].1.contains("Permission denied"), "{}", found[0].1);
    assert_eq!(fixture.host_text("a.txt"), "a\n");
    assert!(!fixture.has("locked/b.txt"));
    assert!(!fixture.left_behind());
}

#[test]
fn an_interrupted_merge_is_rolled_back_before_it_runs_again() {
    let fixture = Fixture::new();
    fixture.host_file("notes.txt", "alpha\n");
    fixture.source("notes", "beta\n");
    let upper = fixture.upper("write notes notes.txt\n");
    let diff = collect(&upper, &fixture.host).expect("collect");
    // What a crash right after moving the host file aside leaves behind.
    let staging = fixture.host.join(".naos-merge-dead");
    fs::create_dir(&staging).expect("staging");
    fs::rename(fixture.host.join("notes.txt"), staging.join("b1")).expect("move");
    fs::create_dir(fixture.merge_dir()).expect("merge dir");
    fs::write(
        fixture.merge_dir().join("journal"),
        "{\"staging\":\".naos-merge-dead\"}\n{\"moved\":{\"path\":\"notes.txt\",\"to\":\"b1\"}}\n{\"placed\"",
    )
    .expect("journal");

    let report = applied(fixture.merge(&upper, &diff, &everything(&diff)));

    assert_eq!(report.applied, ["notes.txt"]);
    assert_eq!(fixture.host_text("notes.txt"), "beta\n");
    assert_eq!(fixture.kept("backup", "notes.txt"), "alpha\n");
    assert!(!staging.exists());
    assert!(!fixture.left_behind());
}

#[test]
fn only_mergeable_and_complete_selections_are_accepted() {
    let fixture = Fixture::new();
    fixture.host_file("docs/x.md", "x\n");
    fixture.source("x", "x\n");
    fixture.source("y", "y\n");
    let upper = fixture.upper(
        "write x hard\nlink hard hard2\nset_inode_field hard links_count 2\n\
         mknod docs c 0 0\nmkdir newdir\nwrite y newdir/f\n",
    );
    let diff = collect(&upper, &fixture.host).expect("collect");

    let found = conflicts(fixture.merge(
        &upper,
        &diff,
        &decide(&["hard", "../etc/passwd", "/etc/passwd", "docs", "newdir/f"]),
    ));

    let paths: Vec<&str> = found.iter().map(|(path, _)| path.as_str()).collect();
    assert_eq!(
        paths,
        ["../etc/passwd", "/etc/passwd", "hard", "docs", "newdir/f"]
    );
    assert_eq!(found[3].1, "needs docs/x.md too");
    assert_eq!(found[4].1, "needs newdir too");
    assert_eq!(fixture.host_text("docs/x.md"), "x\n");
    assert!(!fixture.has("newdir"));
    assert!(!fixture.left_behind());
}

#[test]
fn a_deleted_directory_needs_the_rename_out_of_it() {
    let fixture = Fixture::new();
    fixture.host_file("docs/x.md", "x\n");
    fixture.source("x", "x\n");
    let upper = fixture.upper("mknod docs c 0 0\nwrite x moved.md\n");
    let diff = collect(&upper, &fixture.host).expect("collect");

    let found = conflicts(fixture.merge(&upper, &diff, &decide(&["docs"])));
    let report = applied(fixture.merge(&upper, &diff, &decide(&["docs", "moved.md"])));

    assert_eq!(
        found,
        [("docs".to_owned(), "needs moved.md too".to_owned())]
    );
    assert_eq!(report.applied, ["docs", "moved.md"]);
    assert_eq!(fixture.host_text("moved.md"), "x\n");
    assert!(!fixture.has("docs"));
    assert_eq!(fixture.kept("backup", "docs/x.md"), "x\n");
}
