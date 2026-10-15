## naos-api-0.2.0 (2026-10-15)

### Features

- **api**: broker entry content to the runner
- **api**: group mcp calls by server, tool and minute
- **api**: group model calls by model and minute
- **api**: delete finished runs
- **api**: accept gemini as a provider api
- **api**: delete a policy and check model credentials
- **api**: delete an image no open run boots
- **api**: count what the shell gate decided for a run
- **api**: show the runs and calls of an mcp server
- **api**: count what the network gate decided for a run
- **api**: change the model policy of a started run
- **api**: change the network policy of a started run
- **api**: change the shell policy of a started run
- **api**: change the policy of a started run
- **api**: grant secrets to the agent by rule
- **api**: turn the mcp policy into rules
- **api**: show the mcp entries a run holds
- **api**: add the mcp server registry
- **api**: count the model calls of a run from the audit
- **api**: let alembic own the database schema
- **api**: resolve model policies and issue provider keys with the Run
- **api**: list, rotate, re-date and delete secrets and show who uses them
- **api**: search policies and count the profiles and runs that use each
- **api**: state when events arrived, count refusals and summarize the trail
- **api**: delete profiles and count the runs copied from each
- **api**: state image facts and count the runs booting each image
- **api**: revoke, drain and place runners for an operator
- **api**: record where a run waits and which slot takes it
- **api**: stamp each line of the console log with its time
- **api**: carry a live console between the runner and the terminals
- **api**: carry the console size a viewer asks for
- **api**: broker a read-only console attach
- **api**: show the profile and lease a run holds
- **api**: keep profiles and pin a run to a runner
- **api**: read the runs and runners an operator screen shows
- **api**: list the runners for an operator

### Bug Fixes

- **api**: name policies in refusals
- **api**: refuse an unknown api, not gemini, in the model test
- **api**: count model denials in the audit summary
- **console**: hand the keyboard to the terminal the operator acted on
- **api**: keep the text and not the screen
- **api**: take an empty console report
- **api**: declare the transitions dependency

### Refactor

- **common**: drop the empty package
- **api**: drop sqlalchemy imports again
- **api**: drop the console logs nobody could read

### Build

- **api**: test two runs on one runner

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
