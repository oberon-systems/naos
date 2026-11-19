from collections.abc import Iterator

from fastapi import Request, WebSocket
from sqlmodel import Session, create_engine


class Database:
    def __init__(self, url: str) -> None:
        # A failed statement's text would otherwise carry its bound values, secrets among them.
        self.engine = create_engine(url, hide_parameters=True)


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.db.engine) as session:
        yield session


# A websocket is no Request, so its dependencies open their own session.
def get_socket_session(websocket: WebSocket) -> Iterator[Session]:
    with Session(websocket.app.state.db.engine) as session:
        yield session
