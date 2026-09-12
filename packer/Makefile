# Image builds for naos: a pinned packer, one image per directory.
#
# Reached from the repository root as `make packer <target>`, or directly with
# `make -C packer <target>`.

PACKER_VERSION ?= 1.16.0

REPO_ROOT := $(abspath $(CURDIR)/..)
PACKER_ROOT := $(REPO_ROOT)/.packer
PACKER_HOME := $(PACKER_ROOT)/bin/$(PACKER_VERSION)
PACKER_ARCH := $(shell uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')
PACKER_ZIP := packer_$(PACKER_VERSION)_linux_$(PACKER_ARCH).zip
PACKER_URL := https://releases.hashicorp.com/packer/$(PACKER_VERSION)

# The system packer is used only when it already is the pinned version.
SYSTEM_PACKER_VERSION := \
	$(shell packer version 2> /dev/null | sed -n 's/^Packer v\([0-9.]*\).*/\1/p')
ifeq ($(SYSTEM_PACKER_VERSION),$(PACKER_VERSION))
PACKER ?= $(shell command -v packer)
else
PACKER ?= $(PACKER_HOME)/packer
endif
export PACKER_PLUGIN_PATH := $(PACKER_ROOT)/plugins

QEMU ?= qemu-system-x86_64
OUTPUT_ROOT ?= $(REPO_ROOT)/build
VERSION ?= 0.0.0-dev
BUILD_PASSWORD_FILE := $(OUTPUT_ROOT)/.build-password

RUN_MEMORY ?= 2048
RUN_CPUS ?= 2

IMAGES := $(sort $(notdir $(patsubst %/,%,$(dir $(wildcard */*.pkr.hcl)))))
BASE_IMAGE := base
ORDERED_IMAGES := $(BASE_IMAGE) $(filter-out $(BASE_IMAGE),$(IMAGES))

.DEFAULT_GOAL := help
.PHONY: help install validate build-all rebuild-all clean

help:
	@echo "Images: $(IMAGES), version $(VERSION)"
	@echo
	@echo "Targets:"
	@echo "  install          Install packer $(PACKER_VERSION) into $(PACKER_HOME) unless it is on PATH"
	@echo "  validate         Initialise the plugins and validate every template"
	@echo "  build-<image>    Build one image, for example build-base"
	@echo "  rebuild-<image>  Discard the artifact and plugins, then build"
	@echo "  run-<image>      Boot a throwaway copy with its console on this terminal"
	@echo "  build-all        Build every image, base first"
	@echo "  rebuild-all      Rebuild every image from scratch"
	@echo "  clean            Remove $(OUTPUT_ROOT)"

install: $(PACKER)

$(PACKER_HOME)/packer:
	@echo "packer $(PACKER_VERSION) is not on PATH, installing into $(PACKER_HOME)"
	@mkdir -p $(PACKER_HOME)
	curl -fsSL -o $(PACKER_HOME)/$(PACKER_ZIP) $(PACKER_URL)/$(PACKER_ZIP)
	curl -fsSL -o $(PACKER_HOME)/SHA256SUMS $(PACKER_URL)/packer_$(PACKER_VERSION)_SHA256SUMS
	cd $(PACKER_HOME) && grep ' $(PACKER_ZIP)$$' SHA256SUMS | sha256sum -c -
	cd $(PACKER_HOME) && unzip -o -q $(PACKER_ZIP) packer && rm -f $(PACKER_ZIP)
	@$@ version

# The build password only lets packer log in while an image is being built; the
# agents image locks root and removes sshd before it is shipped.
$(BUILD_PASSWORD_FILE):
	@mkdir -p $(OUTPUT_ROOT)
	@umask 077 && od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > $@

define check_image
	@case " $(IMAGES) " in \
		*" $(1) "*) ;; \
		*) echo "unknown image '$(1)', have: $(IMAGES)" >&2; exit 1 ;; \
	esac
endef

define packer_build
	@if [ "$(1)" != "$(BASE_IMAGE)" ]; then \
		base=$(OUTPUT_ROOT)/$(BASE_IMAGE)/naos-$(BASE_IMAGE)-$(VERSION).qcow2; \
		if [ ! -f "$$base" ]; then \
			echo "$(1) derives from $$base, which is not built yet" >&2; \
			exit 1; \
		fi; \
		base_var="-var base_image=$$base"; \
	fi; \
	cd $(1) && $(PACKER) init $(2) . && \
	PKR_VAR_build_password="$$(cat $(BUILD_PASSWORD_FILE))" \
	$(PACKER) build -force $$base_var \
		-var 'version=$(VERSION)' \
		-var 'output_directory=$(OUTPUT_ROOT)/$(1)' .
endef

validate: install
	@for image in $(IMAGES); do \
		echo "==> validating $$image"; \
		( cd $$image && $(PACKER) init . > /dev/null \
			&& $(PACKER) fmt -check -diff . \
			&& $(PACKER) validate -var build_password=validate-only . ) || exit 1; \
	done

build-%: install $(BUILD_PASSWORD_FILE)
	$(call check_image,$*)
	@echo "==> building $* $(VERSION) into $(OUTPUT_ROOT)/$*"
	$(call packer_build,$*,)

rebuild-%: install $(BUILD_PASSWORD_FILE)
	$(call check_image,$*)
	rm -rf $(OUTPUT_ROOT)/$*
	$(call packer_build,$*,-upgrade)

build-all:
	@for image in $(ORDERED_IMAGES); do \
		$(MAKE) --no-print-directory build-$$image || exit 1; \
	done

rebuild-all:
	@for image in $(ORDERED_IMAGES); do \
		$(MAKE) --no-print-directory rebuild-$$image || exit 1; \
	done

# Mirrors what the runner gives a Run: no network, the console on ttyS0 and the
# probe log on ttyS1. snapshot=on keeps the artifact untouched.
run-%:
	$(call check_image,$*)
	@image=$(OUTPUT_ROOT)/$*/naos-$*-$(VERSION).qcow2; \
	if [ ! -f "$$image" ]; then \
		echo "no $$image, run 'make build-$*' first" >&2; \
		exit 1; \
	fi; \
	echo "==> $$image, probes in $(OUTPUT_ROOT)/$*-boot.log"; \
	echo "==> console on this terminal, quit with Ctrl-a x"; \
	$(QEMU) -enable-kvm -cpu host -m $(RUN_MEMORY) -smp $(RUN_CPUS) \
		-drive file="$$image",if=virtio,snapshot=on \
		-nic none -nographic \
		-serial mon:stdio \
		-serial file:$(OUTPUT_ROOT)/$*-boot.log

clean:
	rm -rf $(OUTPUT_ROOT)
