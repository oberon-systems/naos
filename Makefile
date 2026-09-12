# Developer entry points for naos.

PROJECT := naos
VENV    := .venv
PIP     := $(VENV)/bin/pip

# Hook environments live in the repository, not in ~/.cache/pre-commit.
export PRE_COMMIT_HOME := $(CURDIR)/.pre-commit

.DEFAULT_GOAL := shell
.PHONY: install shell test test-api test-qemu smoke lint run-api packer

# `make packer <target>` reads as a subcommand: everything after `packer` is
# handed to packer/Makefile and turned into a no-op here.
ROOT_GOALS := install shell test test-api test-qemu smoke lint run-api packer
ifeq ($(firstword $(MAKECMDGOALS)),packer)
PACKER_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
ifneq ($(strip $(filter-out $(ROOT_GOALS),$(PACKER_ARGS))),)
$(eval $(filter-out $(ROOT_GOALS),$(PACKER_ARGS)):;@:)
endif
endif

# temporary dir
TEMP_DIR := $(shell mktemp -dut naos-XXXXX$$(date +%s))


# common targets
install:
	python3 -m venv --prompt $(PROJECT) $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(VENV)/bin/pre-commit install


# tests
test:
	$(VENV)/bin/pytest -q
	cargo test --workspace

test-api:
	$(VENV)/bin/pytest -q packages/api

# Boots real VMs from a built agents image: make test-qemu IMAGE=build/agents/<image>.qcow2
test-qemu:
	@test -n "$(IMAGE)" || { echo "IMAGE=<path to a naos-agents qcow2> is required" >&2; exit 1; }
	NAOS_TEST_IMAGE="$(abspath $(IMAGE))" cargo test -p naos-agent -- --ignored real_image

smoke:
	@echo "starting smoke test, working dir: $(TEMP_DIR)"
	@mkdir -p $(TEMP_DIR)
	@$(VENV)/bin/python -c "import secrets; print(secrets.token_urlsafe(32), end='')" > "$(TEMP_DIR)/enrollment"
	@chmod 600 "$(TEMP_DIR)/enrollment"

	@echo "starting api..."
	@$(MAKE) NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256="$$(sha256sum "$(TEMP_DIR)/enrollment" | cut -d' ' -f1)" \
		NAOS_DATABASE_URL="sqlite:///$(TEMP_DIR)/naos.db" \
		NAOS_IMAGE_STORE_PATH="$(TEMP_DIR)/images" \
		run-api > "$(TEMP_DIR)/api.log" 2>&1 \
		& echo $$$! > "$(TEMP_DIR)/api.pin"

	@echo "starting agent..."
	@mkdir -m 700 "$(TEMP_DIR)/state"
	@touch "$(TEMP_DIR)/agent.log"
	@tail -f "$(TEMP_DIR)/api.log" "$(TEMP_DIR)/agent.log" \
		& echo $$$! > "$(TEMP_DIR)/tail.pin"
	@NAOS_AGENT_API_URL=http://127.0.0.1:8000 \
		NAOS_AGENT_NAME=alpha \
		NAOS_AGENT_STATE_DIR="$(TEMP_DIR)/state" \
		NAOS_AGENT_ENROLLMENT_TOKEN_FILE="$(TEMP_DIR)/enrollment" \
		NAOS_AGENT_IMAGE_DIR="$(TEMP_DIR)/vms" \
		NAOS_AGENT_VM_DIR="$(TEMP_DIR)/runs" \
		cargo run -p naos-agent > "$(TEMP_DIR)/agent.log" 2>&1

	@kill $$(cat "$(TEMP_DIR)/tail.pin") 2>/dev/null || true
	@kill $$(cat "$(TEMP_DIR)/api.pin") 2>/dev/null || true
	@rm -rf $(TEMP_DIR)


# linters
lint:
	$(VENV)/bin/pre-commit run --all-files


# runs
run-api:
	$(VENV)/bin/uvicorn --factory naos_api.app:create_app --host 127.0.0.1

packer:
	@$(MAKE) --no-print-directory -C packer $(PACKER_ARGS)


# defaults
shell:
	@rc="$$(mktemp)"; \
	trap 'rm -f "$$rc"' EXIT; \
	cat ~/.bashrc 2> /dev/null > "$$rc" || true; \
	echo 'export PRE_COMMIT_HOME="$(PRE_COMMIT_HOME)"' >> "$$rc"; \
	echo 'source $(CURDIR)/$(VENV)/bin/activate' >> "$$rc"; \
	bash --rcfile "$$rc" -i
