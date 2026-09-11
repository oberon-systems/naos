use thiserror::Error;

#[derive(Debug, Error)]
pub enum AgentError {
    #[error("{0} is not implemented")]
    Unimplemented(&'static str),
}
