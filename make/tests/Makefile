ROOT     := $(abspath ../..)
VENV     := $(ROOT)/.venv
TEMP_DIR := $(shell mktemp -dut naos-XXXXX$$(date +%s))

include *.mk

.DEFAULT_GOAL := test
.PHONY: test test-api test-image smoke

test:
	cd $(ROOT) && $(VENV)/bin/pytest -q
	cargo test --workspace

test-api:
	cd $(ROOT) && $(VENV)/bin/pytest -q packages/api
