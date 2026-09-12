use std::fs::File;
use std::io::{self, Read};

const DIGITS: &[u8; 16] = b"0123456789abcdef";

pub fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(char::from(DIGITS[usize::from(byte >> 4)]));
        out.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
    }
    out
}

pub fn random_hex(bytes: usize) -> io::Result<String> {
    let mut buffer = vec![0u8; bytes];
    File::open("/dev/urandom")?.read_exact(&mut buffer)?;
    Ok(hex(&buffer))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_is_lowercase_and_random_ids_differ() {
        assert_eq!(hex(&[0x00, 0x0f, 0xa5, 0xff]), "000fa5ff");
        let first = random_hex(16).expect("urandom");
        assert_eq!(first.len(), 32);
        assert_ne!(first, random_hex(16).expect("urandom"));
    }
}
