# 10 — Web UI

## Prompt

Build the HTMX dashboard after backend security boundaries are implemented.

Views:

- Runs list;
- Run detail;
- console;
- logs;
- network/shell/MCP events;
- filesystem diff;
- merge approval;
- profiles;
- policies, immutable: find by id or digest, read the resolved document and who uses it, create one of each kind;
- images.

Run creation should make image, host directory, mount mode, network/shell/MCP policy, merge policy, timeout, and runtime settings explicit.

The UI is never the authorization boundary. Every action is authorized by the API. Dangerous actions require confirmation. Never display secrets.
