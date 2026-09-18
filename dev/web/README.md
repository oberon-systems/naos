# Web UI design tool

A disposable [Penpot](https://penpot.app) stack for drawing Web UI mockups.
It runs from `compose.yaml` with no persistent state. Mockups live in
`templates/` as unpacked Penpot exports, JSON plus SVG, so git shows readable
diffs and an agent can read them directly.

## Design loop

Start the stack and pick a template or `(empty)`:

```bash
make -C dev/web up
```

`up` creates a throwaway profile and opens the browser already logged in
through the printed `/autologin` link. If that session is lost, log in as
`designer@example.com` with password `naos-design`. `down` wipes the profile
with the stack.

Draw in the browser at `http://localhost:9001`. When you are done, pick the
file to export and the template to write it to, then let the stack go down:

```bash
make -C dev/web down
```

`down` replaces `templates/<name>/` with the export and runs
`docker compose down -v`, which drops the database and assets. Pick
`(skip export)` to throw the work away.

## Connect Penpot MCP

`up` enables MCP for the throwaway profile, issues its key and serves it
behind the fixed `/mcp/claude` URL, so Claude needs the server added only
once. Add it, then restart Claude:

```bash
claude mcp add --transport http penpot http://localhost:9001/mcp/claude
```

After later `up` runs, reconnect `penpot` from `/mcp` instead of restarting.
While the stack is down, the server just shows as failed.

The MCP plugin runs inside the Penpot browser tab. Keep a file open in the
workspace while the agent works; a hidden or unloaded tab stops MCP.

## Templates

- One directory per template: `manifest.json`, `files/` and `objects/`.
- Images and icons must be SVG. `down` refuses to export a file that holds
  raster images and lists them, so you can replace them and run `down` again.
- Frame thumbnails are raster renders Penpot rebuilds on its own, so the
  export leaves them out.
- Import keeps the Penpot file id, so a round trip changes only what you
  drew.
