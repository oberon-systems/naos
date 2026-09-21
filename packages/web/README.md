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
| `NAOS_WEB_API_BASE_URL` | `http://127.0.0.1:8000` | The API endpoint shown in the top bar, as a browser reaches it |
| `NAOS_WEB_API_URL` | `http://127.0.0.1:8000` | The API this process calls, which may be a private address |
| `NAOS_WEB_OPERATOR_TOKEN_FILE` | unset | File holding the operator token; unset leaves every page unauthorized |
| `NAOS_WEB_API_TIMEOUT_SECONDS` | `10` | How long a page waits for the API |

The token is read once at startup and lives only in the HTTP client. It is
never rendered, and the UI is never the authorization boundary: every action
is authorized by the API, which refuses the same calls to anyone else.

The screens and what each one shows are in
[docs/10-web-ui.md](https://github.com/oberon-systems/naos/blob/main/docs/10-web-ui.md).

## Run

```bash
export NAOS_WEB_API_BASE_URL=https://api.example.com
export NAOS_WEB_API_URL=http://192.0.2.10:8000
export NAOS_WEB_OPERATOR_TOKEN_FILE=/run/secrets/operator
uvicorn --factory naos_web.app:create_app --host 127.0.0.1 --port 8001
```

`/healthz` answers `{"status": "ok"}` without touching the API.
