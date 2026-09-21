# Prompt 10h — Images

Read `AGENTS.md`, `docs/10-web-ui.md`, `docs/05-vm-and-qemu.md`, and the
board `Images — List` on page `Images` in `dev/web/templates/base`.

Build only what the board shows. When a screen named here has no board,
report the missing board and build nothing for it.

Build the Images catalog over `GET /images` and `GET /images/{image_id}`: the
tiles Registered / In use / Unused / Catalog size, the search and the filters
All / In use / Unused, the table IMAGE, VERSION, DIGEST, SOURCE, REGISTERED,
USAGE, and the detail pane with digest and source, the facts block, the runs
booting the image and the recent events.

The catalog is append-only: the UI offers `Register image` and `Copy url`,
never an edit or a delete. Show the full digest in the detail pane and the
truncated one in the table, exactly as the board splits them.

The UI is never an authorization boundary. Every action goes through API
authorization. Never display secrets.

Acceptance: an operator can find an image by digest and see which Runs booted
it, and no view offers a way to rewrite or delete a catalog row.
