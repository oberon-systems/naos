smoke:
	@echo "starting smoke test, working dir: $(TEMP_DIR)"
	@mkdir -p $(TEMP_DIR)
	@$(VENV)/bin/python -c "import secrets; print(secrets.token_urlsafe(32), end='')" > "$(TEMP_DIR)/enrollment"
	@chmod 600 "$(TEMP_DIR)/enrollment"

	@echo "starting api..."
	@$(MAKE) -C $(ROOT) NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256="$$(sha256sum "$(TEMP_DIR)/enrollment" | cut -d' ' -f1)" \
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
