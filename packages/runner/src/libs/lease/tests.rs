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
