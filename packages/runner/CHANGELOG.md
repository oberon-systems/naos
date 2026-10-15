## runner-0.2.0 (2026-10-15)

### Features

- **release**: install the runner of a release
- **runner**: read entry content for the api
- **runner**: serve the openai responses api
- **runner**: serve a gemini provider in the openai format
- **runner**: start goose and hand it the model
- **runner**: boot only the image the api handed over
- **runner**: apply a changed model policy to a running vm
- **runner**: apply a changed network policy to a running vm
- **runner**: apply a changed shell policy to a running vm
- **runner**: swap the mcp policy of a running vm
- **runner**: serve granted secrets from the broker
- **runner**: enforce the mcp rules in the broker
- **runner**: serve each Run a model gateway on its naos.model port
- **runner**: report where the agent runs
- **runner**: hold a console socket to the api and type into the guest
- **runner**: apply the console size inside the guest
- **runner**: ship the console log to the api

### Bug Fixes

- **runner**: let go of what a run holds when it ends
- **runner**: start a claimed run from a fresh read
- **runner**: serve the model gateway over vsock instead of a serial port
- **runner**: prepare the state directory at start

### Build

- **dev/stack**: kickstart the whole stack locally

### Documentation

- **docs**: describe installing a release

## runner-0.1.0 (2026-09-20)

### Features

- **runner**: install the runner from the github releases
- **runner**: spool audit events and post them to the api
- **runner**: merge the decided diff and archive the agent's changes
- **runner**: collect the workspace diff from the upper disk
- **runner**: share the run workspace read-only through virtiofsd
- **mcp**: proxy external mcp servers through the broker
- **mcp**: serve the run gates as mcp tools
- **mcp**: serve the guest mcp port with no tools
- **shell**: add the per-Run shell gate
- **network**: add the per-Run network gate
- **images**: register image urls, agent downloads them itself
- **runner**: add qemu runtime, image cache and console
- **runner**: add runner registration, leases and reconciliation
- **packages**: add api and runner skeletons with fail-closed stubs

### Bug Fixes

- **network**: pin every resolved address, not just the last
- **runtime**: verify that runs stay apart
- **shell**: cap git output while reading and test every limit
- **shell**: run filesystem walks off the async threads
- **mcp**: hand credentials to the first start and sync running vms
- **runner**: renew the lease while reconcile runs

### Refactor

- **runner**: rename the crate and binary to naos-runner
- **runner**: call the runs endpoints of the api
- **runner**: use tasks endpoints
- **runner**: move modules into sleipnir libs layout

### Build

- **cz**: version every package from its own config
- **repo**: bootstrap tooling and component layout
