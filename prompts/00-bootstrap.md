# Prompt 00 — Bootstrap

Read `AGENTS.md` and relevant `docs/` first.

Create the initial repository structure for the Python control plane and Rust runner.

Python: 3.10+, FastAPI, Pydantic, SQLModel, SQLite.
Rust: stable Rust, structured errors/logging.
Add tests, formatting, linting, type checking, and local development configuration.

Do not implement fake authorization, fake VM isolation, arbitrary host shell, or insecure placeholders. Stubs must fail closed.

Acceptance:

- repository builds;
- Python and Rust tests run;
- quality checks are configured;
- no secrets are committed;
- structure matches architecture.
