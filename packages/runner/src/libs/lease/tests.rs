use super::*;

#[test]
fn deadline_follows_the_last_renewal() {
    let start = Instant::now();
    let mut clock = LeaseClock::starting(start, Duration::from_secs(60));
    assert!(!clock.expired(start));
    assert!(clock.expired(start + Duration::from_secs(60)));

    clock.renewed(start + Duration::from_secs(50), Duration::from_secs(30));
    assert!(!clock.expired(start + Duration::from_secs(79)));
    assert!(clock.expired(start + Duration::from_secs(80)));
    assert_eq!(clock.ttl(), Some(Duration::from_secs(30)));
}

#[test]
fn a_ttl_outside_the_range_is_clamped() {
    let now = Instant::now();
    let mut clock = LeaseClock::starting(now, Duration::ZERO);

    clock.renewed(now, Duration::ZERO);
    assert!(!clock.expired(now + Duration::from_secs(2)));

    clock.renewed(now, Duration::from_secs(u64::MAX));
    assert!(clock.expired(now + Duration::from_secs(3601)));
}
