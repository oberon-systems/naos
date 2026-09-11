# 06 — Network Gate

## Prompt

Implement per-Run default-deny network enforcement.

Example:

```yaml
allow:
  - protocol: https
    host: google.com
deny:
  - host: private.example.com
```

Deny overrides allow.

Enforce policy at connection establishment. Handle hostname normalization, DNS, IPv4/IPv6, direct IP, loopback, private/link-local ranges, metadata endpoints, DNS rebinding, redirects, CONNECT, proxy bypass, and connection reuse.

Log run_id, timestamp, destination, protocol, decision, matched rule, and safe failure reason. Never log credentials.

If policy cannot be evaluated, deny.

Security tests must include allowed/denied hosts, direct IP, localhost, RFC1918, IPv6 loopback, DNS rebinding, redirects, CONNECT, and malformed hostnames.
