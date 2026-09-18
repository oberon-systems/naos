"""Disposable Penpot stack for Web UI mockups: import a template, draw, export it back."""

import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import questionary

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
URL = "http://localhost:9001"
PROFILE = {"email": "designer@example.com", "password": "naos-design"}  # noqa: S105
EMPTY = "(empty)"
NEW = "(new template)"
SKIP = "(skip export)"


def compose(*args: str) -> None:
    env = {"PENPOT_SECRET_KEY": secrets.token_urlsafe(48), **os.environ}
    command = ["docker", "compose", "-f", str(HERE / "compose.yaml"), *args]
    subprocess.run(command, check=True, env=env)  # noqa: S603, S607


def templates() -> list[str]:
    return sorted(p.parent.name for p in TEMPLATES.glob("*/manifest.json"))


def rpc(client: httpx.Client, command: str, **params: Any) -> Any:
    response = client.post(f"/api/rpc/command/{command}", json=params)
    if response.is_error:
        sys.exit(f"{command}: {response.status_code} {response.text}")
    return response.json() if response.content else None


def stream(client: httpx.Client, command: str, **kwargs: Any) -> str:
    event = ""
    with client.stream("POST", f"/api/rpc/command/{command}", **kwargs) as response:
        if response.is_error:
            sys.exit(f"{command}: {response.status_code} {response.read().decode()}")
        for line in response.iter_lines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:") and event in ("end", "error"):
                if event == "error":
                    sys.exit(f"{command}: {line}")
                return line.removeprefix("data:")
    sys.exit(f"{command}: stream closed without a result")


def connect(wait: float) -> httpx.Client | None:
    client = httpx.Client(
        base_url=URL, headers={"Accept": "application/json"}, timeout=60, follow_redirects=True
    )
    deadline = time.monotonic() + wait
    while True:
        try:
            if client.post("/api/rpc/command/get-profile", json={}).is_success:
                return client
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            return None
        time.sleep(2)


def drafts(client: httpx.Client) -> str:
    projects = rpc(client, "get-all-projects")
    return str(next(p["id"] for p in projects if p["isDefault"] and p["isDefaultTeam"]))


def load(client: httpx.Client, project: str, source: Path) -> None:
    file_id = json.loads((source / "manifest.json").read_text())["files"][0]["id"]
    rpc(client, "create-file", id=file_id, name=source.name, projectId=project)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            archive.write(path, path.relative_to(source).as_posix())
    stream(
        client,
        "import-binfile",
        data={"name": source.name, "project-id": project, "file-id": file_id},
        files={"file": (f"{source.name}.penpot", buffer.getvalue(), "application/zip")},
    )


def save(client: httpx.Client, file_id: str, target: Path) -> None:
    result = stream(
        client,
        "export-binfile",
        json={"fileId": file_id, "includeLibraries": False, "embedAssets": True},
    )
    url = re.search(r'"~r([^"]+)"', result)
    if url is None:
        sys.exit(f"export-binfile: no download link in {result}")
    download = client.get(url.group(1))
    download.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(download.content))

    # Frame thumbnails are raster renders Penpot regenerates, so they never reach the template.
    thumbnails = [n for n in archive.namelist() if "/thumbnails/" in n]
    objects = {f"objects/{json.loads(archive.read(n))['mediaId']}." for n in thumbnails}
    entries = [
        n
        for n in archive.namelist()
        if n not in thumbnails and not n.startswith(tuple(objects)) and not n.endswith("/")
    ]
    raster = [n for n in entries if not n.endswith((".json", ".svg"))]
    if raster:
        sys.exit("only SVG images are allowed, replace these:\n  " + "\n  ".join(raster))

    shutil.rmtree(target, ignore_errors=True)
    for name in entries:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = archive.read(name)
        if name.endswith(".json"):
            content = (json.dumps(json.loads(content), indent=2) + "\n").encode()
        path.write_bytes(content)


def up() -> None:
    template = questionary.select("Template", choices=[EMPTY, *templates()]).ask()
    if template is None:
        return
    compose("up", "-d", "--wait")
    client = connect(wait=300)
    if client is None:
        sys.exit(f"penpot is not answering on {URL}")
    token = rpc(client, "prepare-register-profile", fullname="Designer", **PROFILE)["token"]
    rpc(client, "register-profile", token=token)
    rpc(client, "update-profile-props", props={"mcpEnabled": True})
    key = rpc(client, "create-access-token", name="mcp", type="mcp")["token"]
    if template != EMPTY:
        load(client, drafts(client), TEMPLATES / template)
    print(f"\nPenpot: {URL}  login {PROFILE['email']} / {PROFILE['password']}")
    print(f"MCP:    claude mcp add --transport http penpot '{URL}/mcp/stream?userToken={key}'")


def down() -> None:
    client = connect(wait=0)
    if client is not None:
        rpc(client, "login-with-password", **PROFILE)
        files = rpc(client, "get-project-files", projectId=drafts(client))
        choices = [questionary.Choice(f["name"], value=f["id"]) for f in files]
        file_id = questionary.select("Export file", choices=[SKIP, *choices]).ask()
        if file_id is None:
            return
        if file_id != SKIP:
            name = questionary.select("Template", choices=[NEW, *templates()]).ask()
            if name == NEW:
                name = questionary.text(
                    "Template name",
                    validate=lambda v: bool(re.fullmatch(r"[a-z0-9][a-z0-9-]*", v)),
                ).ask()
            if name is None:
                return
            save(client, file_id, TEMPLATES / name)
            print(f"exported to {(TEMPLATES / name).relative_to(HERE)}")
    compose("down", "-v")


if __name__ == "__main__":
    commands = {"up": up, "down": down}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(commands)}}}")
    commands[sys.argv[1]]()
