# naos web

The operator interface of [naos](https://github.com/oberon-systems/naos):
Runs and their detail, runners, images, profiles and the audit trail. It reads
everything from the [naos api](https://pypi.org/project/naos-api/) and holds no
state and no database of its own.

The pages are server-rendered [Jinja](https://jinja.palletsprojects.com/)
templates driven by [htmx](https://htmx.org/), so a browser with no JavaScript
still gets the full page. Nothing is bundled and nothing is fetched from a CDN.

## Install

```bash
pip install "naos-web[server]"
```

The `server` extra adds [uvicorn](https://www.uvicorn.org/), which the library
itself does not need. The published image carries it:

```bash
docker pull ghcr.io/oberon-systems/naos-web
```

## Configure

| Variable | Default | Meaning |
|---|---|---|
| `NAOS_WEB_API_BASE_URL` | `http://127.0.0.1:8000` | Where the API answers |

The screens and what each one shows are in
[docs/10-web-ui.md](https://github.com/oberon-systems/naos/blob/main/docs/10-web-ui.md).

## Run

```bash
export NAOS_WEB_API_BASE_URL=https://api.example.com
uvicorn --factory naos_web.app:create_app --host 127.0.0.1 --port 8001
```

`/healthz` answers `{"status": "ok"}` without touching the API.
