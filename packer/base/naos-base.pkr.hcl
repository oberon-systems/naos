// Plain Alpine with the naos account, the serial layout the runner expects and
// the isolation probes. It keeps sshd for the builds derived from it and is
// never shipped on its own.

packer {
  required_version = ">= 1.16.0"

  required_plugins {
    qemu = {
      source  = "github.com/hashicorp/qemu"
      version = "~> 1.1"
    }
  }
}

locals {
  version          = yamldecode(file("${path.root}/../.cz.yaml")).commitizen.version
  output_directory = abspath("${path.root}/../../build/base")

  iso_url = join("", [
    "https://dl-cdn.alpinelinux.org/alpine/",
    var.alpine_branch,
    "/releases/x86_64/alpine-virt-",
    var.alpine_version,
    "-x86_64.iso",
  ])
}

source "qemu" "alpine" {
  iso_url      = local.iso_url
  iso_checksum = var.iso_checksum

  vm_name          = "naos-base-${local.version}.qcow2"
  output_directory = local.output_directory
  format           = "qcow2"

  disk_size      = var.disk_size
  disk_interface = "virtio"
  net_device     = "virtio-net"

  memory      = var.memory
  cpus        = var.cpus
  accelerator = "kvm"
  headless    = true

  http_directory = "${path.root}/http"

  communicator = "ssh"
  ssh_username = "root"
  ssh_password = var.build_password
  ssh_timeout  = "20m"

  shutdown_command = "poweroff"

  boot_wait = "30s"
  boot_command = [
    "root<enter><wait5>",
    "setup-alpine -e -f http://{{ .HTTPIP }}:{{ .HTTPPort }}/answers<enter>",
    "<wait3m>",
    "mount /dev/vda2 /mnt<enter><wait5>",
    "chroot /mnt /bin/sh -c 'echo \"root:${var.build_password}\" | chpasswd'<enter><wait5>",
    "chroot /mnt /bin/sh -c 'sed -i \"s/^#*PermitRootLogin.*/PermitRootLogin yes/\" /etc/ssh/sshd_config'<enter><wait5>",
    "umount /mnt<enter><wait5>",
    "reboot<enter>",
  ]
}

build {
  name    = "base"
  sources = ["source.qemu.alpine"]

  provisioner "file" {
    source      = "${path.root}/files/naos-probe"
    destination = "/etc/init.d/naos-probe"
  }

  provisioner "file" {
    source      = "${path.root}/files/naos-workspace"
    destination = "/etc/init.d/naos-workspace"
  }

  provisioner "shell" {
    inline = [
      "apk update",
      "apk upgrade --no-cache",
      "apk add --no-cache agetty e2fsprogs",

      // The runner wires ttyS0 to the interactive console and ttyS1 to boot.log,
      // so the kernel talks to ttyS1 and ttyS0 logs naos in.
      "sed -i 's|^default_kernel_opts=\"\\(.*\\)\"|default_kernel_opts=\"\\1 console=ttyS1,115200\"|' /etc/update-extlinux.conf",
      "update-extlinux",
      "sed -i '/^ttyS0::/d' /etc/inittab",
      "echo 'ttyS0::respawn:/sbin/agetty --autologin naos --noclear 115200 ttyS0 vt100' >> /etc/inittab",

      "echo qemu_fw_cfg >> /etc/modules",
      "echo virtio_console >> /etc/modules",
      "echo virtiofs >> /etc/modules",
      "echo overlay >> /etc/modules",
      "chmod 0755 /etc/init.d/naos-probe /etc/init.d/naos-workspace",
      "rc-update add naos-workspace default",
      "rc-update add naos-probe default",
      "rc-update add acpid default",
      "rm -rf /var/cache/apk/*",
    ]
  }

  post-processor "manifest" {
    output     = "${local.output_directory}/manifest.json"
    strip_path = true
  }
}
