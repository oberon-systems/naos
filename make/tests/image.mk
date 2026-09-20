test-image:
	@version="$$(sed -n 's/^  version: //p' $(ROOT)/packer/.cz.yaml)"; \
	images="$$(ls $(ROOT)/build/*/naos-*-$$version.qcow2 2> /dev/null)"; \
	test -n "$$images" || { echo "no images of version $$version in $(ROOT)/build" >&2; exit 1; }; \
	for image in $$images; do \
		echo "testing $$image"; \
		NAOS_TEST_IMAGE="$$image" cargo test -p naos-runner -- --ignored real_image || exit 1; \
	done
