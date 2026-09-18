use std::fs;
use std::os::unix::fs::PermissionsExt;

use serde_json::json;
use tempfile::TempDir;

use super::*;

fn event(run_id: &str) -> Event {
    let fields = json!({ "run_id": run_id, "entries": 3 });
    Event::new(
        "workspace_collected",
        fields.as_object().cloned().expect("object"),
    )
    .expect("id")
}

fn spool(limit: u64) -> (TempDir, Spool) {
    let dir = tempfile::tempdir().expect("tempdir");
    let spool = Spool::with_limit(dir.path(), limit);
    (dir, spool)
}

#[test]
fn events_carry_an_id_and_flatten_their_fields() {
    let first = event("run_a");
    let second = event("run_a");

    assert!(first.id.starts_with("evt_") && first.id.len() == 36);
    assert_ne!(first.id, second.id);
    let wire = serde_json::to_value(&first).expect("json");
    assert_eq!(wire["event"], "workspace_collected");
    assert_eq!(wire["run_id"], "run_a");
    assert_eq!(wire["entries"], 3);
}

#[test]
fn appended_events_are_taken_in_order_and_acked() {
    let (_dir, spool) = spool(1 << 20);
    let events: Vec<_> = ["run_a", "run_b", "run_c"].into_iter().map(event).collect();
    for item in &events {
        spool.append(item).expect("append");
    }

    let mode = fs::metadata(spool.path())
        .expect("spool")
        .permissions()
        .mode();
    assert_eq!(mode & 0o777, 0o600);
    let batch = spool.take(2).expect("take");
    assert_eq!(batch.events, events[..2]);
    spool.ack(&batch).expect("ack");

    assert_eq!(spool.take(10).expect("take").events, events[2..]);
}

#[test]
fn events_appended_after_a_take_survive_its_ack() {
    let (_dir, spool) = spool(1 << 20);
    spool.append(&event("run_a")).expect("append");
    let batch = spool.take(10).expect("take");
    let late = event("run_b");
    spool.append(&late).expect("append");

    spool.ack(&batch).expect("ack");

    assert_eq!(spool.take(10).expect("take").events, vec![late]);
}

#[test]
fn unreadable_lines_are_skipped_and_trimmed() {
    let (_dir, spool) = spool(1 << 20);
    let good = event("run_a");
    spool.append(&good).expect("append");
    let mut raw = fs::read(spool.path()).expect("read");
    raw.extend_from_slice(b"{\"torn\n");
    fs::write(spool.path(), raw).expect("write");

    let batch = spool.take(10).expect("take");
    assert_eq!(batch.events, vec![good]);
    spool.ack(&batch).expect("ack");

    assert!(fs::read(spool.path()).expect("read").is_empty());
}

#[test]
fn a_full_spool_drops_the_oldest_and_says_how_many() {
    let line = serde_json::to_vec(&event("run_a")).expect("json").len() as u64 + 1;
    let (_dir, spool) = spool(line * 4);
    let events: Vec<_> = (0..5).map(|n| event(&format!("run_{n}"))).collect();
    for item in &events {
        spool.append(item).expect("append");
    }

    let kept = spool.take(10).expect("take").events;
    let dropped = kept
        .iter()
        .find(|item| item.event == "audit_dropped")
        .expect("marker");
    let count = dropped.fields["dropped"].as_u64().expect("count");
    let survivors: Vec<_> = kept
        .iter()
        .filter(|item| item.event != "audit_dropped")
        .collect();
    assert_eq!(count as usize + survivors.len(), events.len());
    assert_eq!(survivors.last(), events.last().as_ref());
}
