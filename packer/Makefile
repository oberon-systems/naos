# Packer images for naos: `base` from the Alpine ISO and `agents` on top of it, always built together.

include .env
export

PACKER_VERSION := 1.16.0
TOOLS := $(abspath ../.packer)
BUILD := $(abspath ../build)

export PATH := $(TOOLS)/bin:$(PATH)
export PACKER_PLUGIN_PATH := $(TOOLS)/plugins

.DEFAULT_GOAL := build
.PHONY: install validate build run clean


# tools
install:
	@packer version 2> /dev/null | grep -qx "Packer v$(PACKER_VERSION)" && exit 0; \
	mkdir -p $(TOOLS)/bin && cd $(TOOLS)/bin && \
	curl -fsSLO https://releases.hashicorp.com/packer/$(PACKER_VERSION)/packer_$(PACKER_VERSION)_linux_amd64.zip && \
	curl -fsSLO https://releases.hashicorp.com/packer/$(PACKER_VERSION)/packer_$(PACKER_VERSION)_SHA256SUMS && \
	grep " packer_$(PACKER_VERSION)_linux_amd64.zip$$" packer_$(PACKER_VERSION)_SHA256SUMS | sha256sum -c - && \
	unzip -o -q packer_$(PACKER_VERSION)_linux_amd64.zip packer && rm packer_$(PACKER_VERSION)_*


# image
validate:
	@for image in base agents; do \
		(cd $$image && packer init . && packer fmt -check . \
			&& packer validate -var build_password=validate-only .) || exit 1; \
	done

# The password only lets packer log in while it builds; the agents image locks root and drops sshd.
build:
	export PKR_VAR_build_password="$$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"; \
	for image in base agents; do \
		(cd $$image && packer init . && packer build -force .) || exit 1; \
	done

# Boots a throwaway agents VM the way the runner does: no network, console here, probes in a log.
run:
	qemu-system-x86_64 -enable-kvm -cpu host -m 2048 -smp 2 \
		-drive file=$(BUILD)/agents/naos-agents-$$(sed -n 's/^  version: //p' .cz.yaml).qcow2,if=virtio,snapshot=on \
		-nic none -nographic -serial mon:stdio -serial file:$(BUILD)/agents-boot.log

clean:
	rm -rf $(BUILD)
