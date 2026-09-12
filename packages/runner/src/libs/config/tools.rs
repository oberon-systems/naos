use std::env;

// Safe env setter with auto-cleanup. std::env::set_var is safe on edition 2021, which is
// what lets this live under the workspace's forbid(unsafe_code).
pub struct EnvSetter {
    keys: Vec<String>,
}

impl EnvSetter {
    pub fn new() -> Self {
        EnvSetter { keys: Vec::new() }
    }

    pub fn set(&mut self, key: &str, value: &str) {
        env::set_var(key, value);
        self.keys.push(key.to_string());
    }

    pub fn del(&mut self, key: &str) {
        env::remove_var(key);
        self.keys.retain(|k| k != key);
    }
}

impl Drop for EnvSetter {
    fn drop(&mut self) {
        for key in &self.keys {
            env::remove_var(key);
        }
    }
}
