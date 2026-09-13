// The image a Run boots: the base image plus Claude Code and Gemini CLI, the
// agent session on the console, the default instructions, and the seal that
// removes every way in except the runner's console.

packer {
  required_version = ">= 1.16.0"

  required_plugins {
    qemu = {
      source  = "github.com/hashicorp/qemu"
      version = "~> 1.1"
    }
  }
}

variable "build_password" {
  type      = string
  sensitive = true
}

variable "claude_code_version" {
  type = string
}

variable "gemini_cli_version" {
  type = string
}

variable "disk_size" {
  type    = string
  default = "2G"
}

variable "memory" {
  type    = number
  default = 2048
}

variable "cpus" {
  type    = number
  default = 2
}

locals {
  version          = yamldecode(file("${path.root}/../.cz.yaml")).commitizen.version
  base_image       = abspath("${path.root}/../../build/base/naos-base-${local.version}.qcow2")
  output_directory = abspath("${path.root}/../../build/agents")
}

source "qemu" "agents" {
  iso_url          = local.base_image
  iso_checksum     = "none"
  disk_image       = true
  use_backing_file = false

  vm_name          = "naos-agents-${local.version}.qcow2"
  output_directory = local.output_directory
  format           = "qcow2"
  disk_compression = true

  disk_size      = var.disk_size
  disk_interface = "virtio"
  net_device     = "virtio-net"

  memory      = var.memory
  cpus        = var.cpus
  accelerator = "kvm"
  headless    = true

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
    output     = "${local.output_directory}/manifest.json"
    strip_path = true
  }
}
