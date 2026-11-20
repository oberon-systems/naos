use std::time::{Duration, Instant};

// The API sets the TTL; one outside this range would fence every cycle or overflow the deadline.
const MIN_TTL: Duration = Duration::from_secs(3);
const MAX_TTL: Duration = Duration::from_secs(3600);

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
        let ttl = ttl.clamp(MIN_TTL, MAX_TTL);
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
mod tests;
