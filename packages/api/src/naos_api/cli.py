import argparse
import sys

from alembic.util import CommandError

from naos_api import schema
from naos_api.db import Database
from naos_api.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="naos-api")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the database to the head revision")
    commands.add_parser("downgrade", help="take the database one revision back")
    args = parser.parse_args(argv)

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
