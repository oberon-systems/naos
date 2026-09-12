// The image a Run boots: the base image plus Claude Code and Gemini CLI, the
// agent session on the console, the default instructions, and the seal that
// removes every way in except the runner's console.

packer {
  required_version = ">= 1.11.0"

  required_plugins {
    qemu = {
      source  = "github.com/hashicorp/qemu"
      version = "~> 1.1"
    }
  }
}

variable "version" {
  type    = string
  default = "0.0.0-dev"
}

variable "base_image" {
  type        = string
  default     = ""
  description = "Base qcow2 to derive from. Empty locates build/base for the same version."
}

variable "build_password" {
  type      = string
  sensitive = true
}

variable "claude_code_version" {
  type        = string
  default     = "2.1.269"
  description = "@anthropic-ai/claude-code release installed from npm."
}

variable "gemini_cli_version" {
  type        = string
  default     = "0.59.0"
  description = "@google/gemini-cli release installed from npm."
}

variable "image_name" {
  type    = string
  default = "naos-agents"
}

variable "output_directory" {
  type    = string
  default = "build/agents"
}

variable "disk_size" {
  type    = string
  default = "8G"
}

variable "memory" {
  type    = number
  default = 2048
}

variable "cpus" {
  type    = number
  default = 2
}

variable "accelerator" {
  type    = string
  default = "kvm"
}

variable "headless" {
  type    = bool
  default = true
}

locals {
  base_image = var.base_image != "" ? var.base_image : abspath("${path.root}/../../build/base/naos-base-${var.version}.qcow2")
}

source "qemu" "agents" {
  iso_url          = local.base_image
  iso_checksum     = "none"
  disk_image       = true
  use_backing_file = false

  vm_name          = "${var.image_name}-${var.version}.qcow2"
  output_directory = var.output_directory
  format           = "qcow2"
  disk_compression = true

  disk_size      = var.disk_size
  disk_interface = "virtio"
  net_device     = "virtio-net"

  memory      = var.memory
  cpus        = var.cpus
  accelerator = var.accelerator
  headless    = var.headless

  communicator = "ssh"
  ssh_username = "root"
  ssh_password = var.build_password
  ssh_timeout  = "10m"

  shutdown_command = "poweroff"

  boot_wait = "20s"
}

build {
  name    = "agents"
  sources = ["source.qemu.agents"]

  provisioner "shell" {
    inline = ["mkdir -p /tmp/naos-files"]
  }

  provisioner "file" {
    source      = "${path.root}/files/"
    destination = "/tmp/naos-files"
  }

  provisioner "shell" {
    environment_vars = [
      "CLAUDE_CODE_VERSION=${var.claude_code_version}",
      "GEMINI_CLI_VERSION=${var.gemini_cli_version}",
    ]
    script = "${path.root}/files/install.sh"
  }

  provisioner "shell" {
    script = "${path.root}/files/seal.sh"
  }

  post-processor "manifest" {
    output     = "${var.output_directory}/manifest.json"
    strip_path = true
  }
}
