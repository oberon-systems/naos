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
- images.

Run creation should make image, host directory, mount mode, network/shell/MCP policy, merge policy, timeout, and runtime settings explicit.

The UI is never the authorization boundary. Every action is authorized by the API. Dangerous actions require confirmation. Never display secrets.
