# Security policy

Naos runs untrusted AI agents inside disposable VMs and treats every way out
of the VM as a capability a Run must be granted. A flaw that lets an agent
cross a boundary it was not granted, reach a credential, or change a policy
without an operator is a security issue.

## Supported versions

Fixes go into the next release of each component. Older releases get none.

| Component             | Supported          |
| --------------------- | ------------------ |
| `naos-api-X.Y.Z`      | the latest release |
| `naos-web-X.Y.Z`      | the latest release |
| `runner-X.Y.Z`        | the latest release |
| `image-X.Y.Z`         | the latest release |
| meta release `X.Y.Z`  | the latest release |

## Report a vulnerability

Do not open a public issue or pull request. Report it privately on GitHub:
the Security tab, then
[Report a vulnerability](../../security/advisories/new). The report stays
visible to you and the maintainers only, and the fix is discussed there.

Include:

- the component and its version, or the commit;
- what an attacker needs: an agent inside a Run, an operator login, a
  runner credential, a local account on the runner host;
- the steps to reproduce it and the boundary they cross.

## Scope

The model and its limits are in
[docs/01-security-model.md](docs/01-security-model.md) and
[docs/02-threat-model.md](docs/02-threat-model.md). Out of scope:

- a QEMU or KVM escape that needs a flaw in QEMU or the kernel itself;
- prompt injection that changes what an agent does inside the capabilities
  its Run was granted: prompts are not a security boundary;
- a host that ignores the stated requirements, for example a runner host
  without user namespaces or an API reachable from the guest network.
