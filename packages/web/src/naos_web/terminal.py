from dataclasses import dataclass

LIVE = frozenset({"STARTING", "STARTED", "STOPPING"})


@dataclass(frozen=True)
class TerminalView:
    stream: str
    attach_path: str
    attach_url: str
    log: str
    window: str
    host_command: str
    state: str


def view(run_id: str, status: str, api_attach_url: str) -> TerminalView:
    attach_path = f"/api/v1/runs/{run_id}/attach"
    if status in LIVE:
        state = "still running"
    elif status == "PENDING":
        state = "not started yet"
    else:
        state = "finished"
    return TerminalView(
        stream=f"/runs/{run_id}/terminal/ws",
        attach_path=attach_path,
        attach_url=f"{api_attach_url}{attach_path}",
        log=f"/runs/{run_id}/terminal/log",
        window=f"/runs/{run_id}/terminal?window=1",
        host_command=f"naos-runner console {run_id}",
        state=state,
    )
