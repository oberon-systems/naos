ROOT     := $(abspath ../..)
VENV     := $(ROOT)/.venv
TEMP_DIR := $(shell mktemp -dut naos-XXXXX$$(date +%s))

include *.mk

.DEFAULT_GOAL := test
.PHONY: test test-api test-image test-web smoke install-web

install-web:
	$(VENV)/bin/pip install --quiet --no-deps --editable $(ROOT)/packages/web

test: install-web
	cd $(ROOT) && $(VENV)/bin/pytest -q
	cargo test --workspace

test-api:
	cd $(ROOT) && $(VENV)/bin/pytest -q packages/api

test-web: install-web
	cd $(ROOT) && $(VENV)/bin/pytest -q packages/web
