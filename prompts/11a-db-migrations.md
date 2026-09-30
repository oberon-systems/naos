# Prompt 11a — Database Migrations

Read `AGENTS.md`, `docs/03-api-design.md`, `docker/README.md`,
`dev/stack/README.md` and `11-hardening.md`.

The API builds its schema on start with `SQLModel.metadata.create_all` and
refuses to start when a table differs from the models
(`naos_api.db.Database.create_schema`). Any column added to a model breaks a
database that outlives a restart, and nothing records how a schema got where
it is. Make [Alembic](https://alembic.sqlalchemy.org) own the schema and
every change to it.

- Alembic lives in the api package, under `naos_api/migrations/`, ships in the
  wheel and the api image, and takes the url from `NAOS_DATABASE_URL` only.
- The first revision creates the schema the models define now, on PostgreSQL
  and SQLite. Every later model change comes with its revision in the same
  commit. Autogenerate may draft it, and a person reviews it.
- The api never calls `create_all` again. On start it compares the database
  revision with the head:
  - at head, it starts;
  - empty, it upgrades to head when `NAOS_DATABASE_AUTO_MIGRATE` is on and
    refuses otherwise; the setting is on in tests, `make smoke` and
    `make kickstart`, and off by default;
  - behind head or at an unknown revision, it refuses to start and names both
    revisions.
- `naos-api migrate` upgrades to head explicitly, and `make migrate` runs it.
  A downgrade goes one revision back where the change allows it, and the
  revision says so where it does not.
- A database built by `create_all` before this prompt is stamped at the
  first revision, after a check that its tables match it.
- `make test` runs every revision from empty to head on SQLite and fails when
  autogenerate finds a difference between head and the models, so a model
  change without a revision cannot land. A pre-commit hook runs the same
  check.

Update `docs/03-api-design.md` with a Schema section, and update
`docker/README.md` and `dev/stack/README.md`.

Acceptance: an empty database comes up at head; a model change without a
revision fails `make test`; a database one revision behind refuses to start
until `naos-api migrate`, then starts; the upgrade path runs on PostgreSQL in
`make kickstart` and on SQLite in `make smoke`; no code path calls
`create_all`.
