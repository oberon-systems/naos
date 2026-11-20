import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from alembic.util import CommandError

from naos_api import credentials, schema
from naos_api.db import Database
from naos_api.settings import get_settings

STDIN = "-"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="naos-api")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the database to the head revision")
    commands.add_parser("downgrade", help="take the database one revision back")
    sign = commands.add_parser("sign", help="print the headers that sign one operator request")
    sign.add_argument("method")
    sign.add_argument("target", help="the path with its query, as the request sends it")
    sign.add_argument("--body", help="a file holding the request body, - for stdin")
    sign.add_argument("--data", help="the request body itself")
    sign.add_argument("--api", default=os.environ.get("NAOS_API_URL", "http://127.0.0.1:8080"))
    sign.add_argument("--token-file", default=os.environ.get("NAOS_OPERATOR_TOKEN_FILE"))
    args = parser.parse_args(argv)
    if args.command == "sign":
        return _sign(args)

    db = Database(get_settings().database_url)
    try:
        if args.command == "migrate":
            revision: str | None = schema.migrate(db.engine)
        else:
            revision = schema.downgrade(db.engine)
    except (schema.SchemaError, CommandError) as err:
        print(f"naos-api: {err}", file=sys.stderr)
        return 1
    finally:
        db.engine.dispose()
    print(f"database at {revision or 'base'}")
    return 0


# Logs in with the operator token and prints the headers, one per line, ready for curl -H.
def _sign(args: argparse.Namespace) -> int:
    if not args.token_file:
        print(
            "naos-api: --token-file or NAOS_OPERATOR_TOKEN_FILE names the operator token",
            file=sys.stderr,
        )
        return 1
    from_stdin = args.token_file == STDIN
    if from_stdin and args.body == STDIN:
        print("naos-api: only one of the token and the body can come on stdin", file=sys.stderr)
        return 1
    if from_stdin:
        token = sys.stdin.read().strip()
    else:
        with open(args.token_file) as file:
            token = file.read().strip()
    body = (args.data or "").encode()
    if args.body == STDIN:
        body = sys.stdin.buffer.read()
    elif args.body:
        with open(args.body, "rb") as file:
            body = file.read()
    login = urllib.request.Request(  # noqa: S310  checked to be http or https above
        f"{args.api.rstrip('/')}/api/v1/auth/credential",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(login, timeout=10) as answer:  # noqa: S310  the operator's own api
            issued = credentials.Issued(**json.load(answer))
    except (urllib.error.URLError, ValueError, TypeError) as err:
        print(f"naos-api: the api refused the login: {err}", file=sys.stderr)
        return 1
    for name, value in credentials.headers(
        issued, args.method, args.target, body, int(time.time())
    ).items():
        print(f"{name}: {value}")
    return 0
