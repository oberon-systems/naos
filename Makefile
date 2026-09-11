# Developer entry points for naos.

PROJECT := naos
VENV    := .venv
PIP     := $(VENV)/bin/pip

# Hook environments live in the repository, not in ~/.cache/pre-commit.
export PRE_COMMIT_HOME := $(CURDIR)/.pre-commit

.DEFAULT_GOAL := shell
.PHONY: install shell test lint run-api


install:
	python3 -m venv --prompt $(PROJECT) $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(VENV)/bin/pre-commit install

test:
	$(VENV)/bin/pytest -q
	cargo test --workspace

lint:
	$(VENV)/bin/pre-commit run --all-files

run-api:
	$(VENV)/bin/uvicorn --factory naos_api.app:create_app --host 127.0.0.1

shell:
	@rc="$$(mktemp)"; \
	trap 'rm -f "$$rc"' EXIT; \
	cat ~/.bashrc 2> /dev/null > "$$rc" || true; \
	echo 'export PRE_COMMIT_HOME="$(PRE_COMMIT_HOME)"' >> "$$rc"; \
	echo 'source $(CURDIR)/$(VENV)/bin/activate' >> "$$rc"; \
	bash --rcfile "$$rc" -i
