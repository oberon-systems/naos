use std::time::{Duration, Instant};

pub struct LeaseClock {
    deadline: Instant,
    ttl: Option<Duration>,
}

impl LeaseClock {
    pub fn starting(now: Instant, grace: Duration) -> Self {
        Self {
            deadline: now + grace,
            ttl: None,
        }
    }

    // Measured from when the request was sent, so the local deadline never outlives the server's.
    pub fn renewed(&mut self, sent_at: Instant, ttl: Duration) {
        self.deadline = sent_at + ttl;
        self.ttl = Some(ttl);
    }

    pub fn expired(&self, now: Instant) -> bool {
        now >= self.deadline
    }

    pub fn ttl(&self) -> Option<Duration> {
        self.ttl
    }
}

#[cfg(test)]
mod tests {
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
}
