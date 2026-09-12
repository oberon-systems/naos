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
mod tests;
