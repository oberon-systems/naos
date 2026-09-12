use thiserror::Error;

#[derive(Debug, Error)]
pub enum AgentError {
    #[error("{0} is not implemented")]
    Unimplemented(&'static str),
    #[error("configuration: {0}")]
    Config(String),
    #[error("credentials: {0}")]
    Credentials(String),
    #[error("api rejected the runner credentials")]
    Unauthorized,
    #[error("api reported a conflict: {0}")]
    Conflict(String),
    #[error("api returned {status}: {detail}")]
    Api { status: u16, detail: String },
    #[error("api transport: {0}")]
    Transport(String),
    #[error("runtime: {0}")]
    Runtime(String),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
}
