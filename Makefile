# Developer entry points for naos.

PROJECT := naos
VENV    := .venv
PIP     := $(VENV)/bin/pip

# lint reads NUL-separated names with `read -d`, which dash's read lacks.
SHELL := /bin/bash

# Hook environments live in the repository, not in ~/.cache/pre-commit.
export PRE_COMMIT_HOME := $(CURDIR)/.pre-commit

# The packer pinned in packer/Makefile, installed by `make install`.
export PATH := $(CURDIR)/.packer/bin:$(PATH)
export PACKER_PLUGIN_PATH := $(CURDIR)/.packer/plugins

.DEFAULT_GOAL := shell
.PHONY: install shell test test-api test-image test-web smoke lint run-api run-web kickstart


# common targets
# The last line puts the terminal back: the installers draw progress bars and
# leave it without echo or cursor when they are done.
install:
	python3 -m venv --prompt $(PROJECT) $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(VENV)/bin/pre-commit install
	$(MAKE) -C packer install
	@stty sane 2> /dev/null; tput cnorm 2> /dev/null; true


# tests
test test-api test-image test-web smoke:
	$(MAKE) -C make/tests $@


# linters
# --all-files sees only what git tracks; this adds untracked files not ignored
# and drops tracked ones deleted from the tree, which hooks cannot open.
lint:
	@git ls-files -z --cached --others --exclude-standard | \
	while IFS= read -r -d '' f; do [ -e "$$f" ] && printf '%s\0' "$$f"; done | \
	xargs -0 -s $$(( $$(getconf ARG_MAX) - 65536 )) $(VENV)/bin/pre-commit run --files


# runs
run-api:
	$(VENV)/bin/uvicorn --factory naos_api.app:create_app --host 127.0.0.1 --port 8080

run-web:
	$(VENV)/bin/uvicorn --factory naos_web.app:create_app --host 127.0.0.1 --port 8000

kickstart:
	$(MAKE) -C dev/stack kickstart


# defaults
shell:
	@rc="$$(mktemp)"; \
	trap 'rm -f "$$rc"' EXIT; \
	cat ~/.bashrc 2> /dev/null > "$$rc" || true; \
	echo 'export PRE_COMMIT_HOME="$(PRE_COMMIT_HOME)"' >> "$$rc"; \
	echo 'export PATH="$(CURDIR)/.packer/bin:$$PATH"' >> "$$rc"; \
	echo 'export PACKER_PLUGIN_PATH="$(PACKER_PLUGIN_PATH)"' >> "$$rc"; \
	echo 'source $(CURDIR)/$(VENV)/bin/activate' >> "$$rc"; \
	bash --rcfile "$$rc" -i || true
