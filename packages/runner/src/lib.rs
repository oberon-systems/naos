mod error;

pub use error::AgentError;

pub fn run() -> Result<(), AgentError> {
    Err(AgentError::Unimplemented("reconciler"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn run_fails_closed() {
        assert!(matches!(
            run(),
            Err(AgentError::Unimplemented("reconciler"))
        ));
    }
}
