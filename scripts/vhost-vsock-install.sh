#!/usr/bin/env bash
# Grants the runner's user read and write on /dev/vhost-vsock and nothing else, then
# checks the grant. Run it as that user; docs/host/vhost-vsock.md explains why.
set -euo pipefail

device=/dev/vhost-vsock
rule=/etc/udev/rules.d/70-naos-vhost-vsock.rules
modules=/etc/modules-load.d/naos-vhost-vsock.conf
user="$(id -un)"

green="" red="" plain=""
[ -t 1 ] && green=$'\e[32m' plain=$'\e[0m'
[ -t 2 ] && red=$'\e[31m' plain=$'\e[0m'

ok() {
    echo "${green}[ok]${plain} $1"
}

fail() {
    echo "${red}[fail]${plain} $1" >&2
    exit 1
}

setup() {
    echo vhost_vsock | sudo tee "$modules" >/dev/null
    printf 'ACTION=="add", SUBSYSTEM=="misc", KERNEL=="vhost-vsock", RUN+="/usr/bin/setfacl -m u:%s:rw %s"\n' \
        "$user" "$device" | sudo tee "$rule" >/dev/null
    sudo modprobe vhost_vsock
    sudo udevadm control --reload
    sudo udevadm trigger --action=add --name-match=vhost-vsock
    sudo udevadm settle
}

check() {
    getfacl -p "$device" 2>/dev/null | grep -q "^user:$user:rw-" ||
        fail "$device carries no ACL for $user"
    ok "$device has an ACL for $user"
    (exec 3<>"$device") 2>/dev/null || fail "$user cannot open $device for read and write"
    ok "$user opens $device for read and write"
    # A VM that is still running when timeout stops it (exit 124) started with the device.
    # A random CID, as the runner picks, so a VM that holds one does not fail the check.
    local status=0 output cid=$((1000 + RANDOM * 32768 + RANDOM))
    output="$(timeout 3 qemu-system-x86_64 -nodefaults -display none -machine q35 -accel kvm -S \
        -device "vhost-vsock-pci,guest-cid=$cid" 2>&1)" || status=$?
    [ "$status" = 124 ] || fail "qemu did not start with a vhost-vsock device (exit $status):
$output"
    ok "qemu starts a VM with a vhost-vsock device"
    local foreign
    foreign="$(ss --vsock -lnpH | grep -v '"naos-runner"' || true)"
    [ -z "$foreign" ] || fail "vsock listeners other than the runner are reachable from guests:
$foreign"
    ok "no vsock listener but the runner's"
    echo "${green}vhost-vsock ok for $user${plain}"
}

remove() {
    sudo rm -f "$rule" "$modules"
    sudo udevadm control --reload
    sudo setfacl -x "u:$user" "$device" 2>/dev/null || true
    echo "vhost-vsock grant removed for $user"
}

case "${1:-install}" in
install)
    setup
    check
    ;;
check) check ;;
remove) remove ;;
*) fail "usage: vhost-vsock-install.sh [install|check|remove]" ;;
esac
