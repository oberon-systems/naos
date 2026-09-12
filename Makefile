# Developer entry points for naos.

PROJECT := naos
VENV    := .venv
PIP     := $(VENV)/bin/pip

# Hook environments live in the repository, not in ~/.cache/pre-commit.
export PRE_COMMIT_HOME := $(CURDIR)/.pre-commit

.DEFAULT_GOAL := shell
.PHONY: install shell test lint run-api

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

smoke:
	@echo "starting smoke test, working dir: $(TEMP_DIR)"
	@mkdir -p $(TEMP_DIR)
	@$(VENV)/bin/python -c "import secrets; print(secrets.token_urlsafe(32), end='')" > "$(TEMP_DIR)/enrollment"
	@chmod 600 "$(TEMP_DIR)/enrollment"

	@echo "starting api..."
	@$(MAKE) NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256="$$(sha256sum "$(TEMP_DIR)/enrollment" | cut -d' ' -f1)" \
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


# defaults
shell:
	@rc="$$(mktemp)"; \
	trap 'rm -f "$$rc"' EXIT; \
	cat ~/.bashrc 2> /dev/null > "$$rc" || true; \
	echo 'export PRE_COMMIT_HOME="$(PRE_COMMIT_HOME)"' >> "$$rc"; \
	echo 'source $(CURDIR)/$(VENV)/bin/activate' >> "$$rc"; \
	bash --rcfile "$$rc" -i
