## naos-web-0.2.0 (2026-10-15)

### Features

- **web**: show entry content in the changes tab
- **web**: show the mcp gate in the gates tab
- **web**: show the model gate in the gates tab
- **web**: show policy names instead of ids
- **web**: name policies and delete finished runs
- **web**: offer gemini and current models in the policy form
- **web**: delete a policy and pick credentials from secrets
- **web**: delete an image and mark required fields
- **web**: show the shell gate in the gates tab
- **web**: manage mcp servers and the mcp policy of a run
- **web**: show the network gate in the gates tab
- **web**: show policy changes in the audit
- **web**: show the secrets granted to the agent
- **web**: edit and show the mcp rules
- **web**: read mcp servers from the registry
- **web**: add the model policy screens
- **web**: add the secrets screens
- **web**: merge a run from its Changes tab
- **web**: read the collected diff of a run on its Changes tab
- **web**: list policies and open each in a popup with its document, usage and a form
- **web**: list the audit trail and open each event in a popup with its logs
- **web**: list profiles and open each in a tabbed popup with a form and delete
- **web**: list images and open each in a tabbed popup with a register dialog
- **web**: list runners and open each in a tabbed popup
- **web**: show a run's audit timeline in its logs tab
- **web**: type into a run from its terminal
- **web**: ask in a popup before a stop or a rerun
- **web**: watch a run from its terminal tab
- **web**: inspect, stop and rerun a run from its overlay
- **web**: start a run from the new run dialog
- **web**: open a runner from the runs page
- **web**: the runs list over the api

### Bug Fixes

- **web**: require a name when saving an mcp policy
- **web**: redraw run statuses live
- **web**: require a model policy in the new run dialog
- **web**: keep device attribute answers out of the guest
- **web**: fill the model policy form and mark an auto image
- **web**: count the secrets of a run from its own entries
- **web**: show model gateway events in the logs and the audit trail
- **web**: keep model policies off the policy pages until their screens exist
- **web**: stretch the policy used-by cards and give their tables the board's columns
- **web**: draw the audit as the board does and link run, runner and policy
- **web**: keep the image digest in one piece around its line break
- **console**: hand the keyboard to the terminal the operator acted on
- **web**: follow the terminal by its scroll, as the boards have it
- **web**: close an overlay back to its page
- **web**: put the terminal toolbar the way the board has it
- **web**: center the page column
- **web**: drop the size note no board shows
- **web**: fit the terminal to the popup and the window
- **web**: scroll the new run dialog instead of shrinking it
- **web**: keep the shell when the nav is boosted
- **dev/stack**: serve the web ui on 8000 and build the stack every run

### Refactor

- **common**: drop the empty package

### Build

- **web**: carry model denials in the stub summary

## naos-web-0.1.0 (2026-09-20)

### Features

- **web**: add the htmx shell and shared components

### Build

- **cz**: version every package from its own config
- **ci**: release every package from its own tag
- **repo**: bootstrap tooling and component layout

### Documentation

- **packages**: give api and web a page of their own
