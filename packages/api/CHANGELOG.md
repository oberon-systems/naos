## naos-api-0.1.0 (2026-09-20)

### Features

- **api**: record audit events and take the runner's own
- **api**: store the collected diff and take merge decisions
- **mcp**: proxy external mcp servers through the broker
- **shell**: add the per-Run shell gate
- **network**: add the per-Run network gate
- **images**: register image urls, agent downloads them itself
- **api**: add image catalog and fs image store
- **runner**: add runner registration, leases and reconciliation
- **api**: add run domain with lifecycle and policy snapshots
- **packages**: add api and runner skeletons with fail-closed stubs

### Bug Fixes

- **mcp**: hand credentials to the first start and sync running vms

### Refactor

- **api**: rename tasks to runs in the model, routes and audit events
- **api**: narrow settings, plain sql, tasks and policies
- **api**: drop sqlalchemy imports and sqlite lock-in

### Build

- **cz**: version every package from its own config
- **ci**: release every package from its own tag
- **repo**: bootstrap tooling and component layout

### Documentation

- **packages**: give api and web a page of their own
