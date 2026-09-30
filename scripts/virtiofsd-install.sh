#!/usr/bin/env bash
# Puts virtiofsd 1.14.0 in place of an older packaged one at /usr/libexec/virtiofsd, then
# checks it the way the runner starts it. docs/host/build-virtiofsd.md explains why.
set -euo pipefail

version=1.14.0
minimum=1.13
binary=/usr/libexec/virtiofsd
root="$HOME/.cache/naos/virtiofsd"

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

installed() {
    "$binary" --version 2>/dev/null | awk 'NR == 1 { print $2 }'
}

usable() {
    local found
    found="$(installed)"
    [ -n "$found" ] && [ "$(printf '%s\n%s\n' "$minimum" "$found" | sort -V | head -1)" = "$minimum" ]
}

setup() {
    if usable; then
        ok "virtiofsd $(installed) at $binary is new enough, nothing to build"
        return
    fi
    sudo apt install -y build-essential libseccomp-dev libcap-ng-dev uidmap
    cargo install virtiofsd --version "$version" --locked --root "$root"
    sudo dpkg-divert --local --rename --divert "$binary.distrib" --add "$binary"
    sudo install -D -m 0755 "$root/bin/virtiofsd" "$binary"
}

check() {
    usable || fail "$binary is $(installed || true), the runner needs $minimum or newer"
    ok "$binary is virtiofsd $(installed)"
    if [ "$(installed)" = "$version" ]; then
        dpkg-divert --list "$binary" | grep -q "$binary.distrib" ||
            fail "$binary is not diverted, a package upgrade would overwrite it"
        ok "the packaged virtiofsd is diverted to $binary.distrib"
    fi
    command -v newuidmap >/dev/null || fail "newuidmap is missing, install uidmap"
    if ! grep -q "^$(id -un):" /etc/subuid || ! grep -q "^$(id -un):" /etc/subgid; then
        fail "$(id -un) has no ranges in /etc/subuid and /etc/subgid"
    fi
    ok "uidmap and the id ranges of $(id -un) are in place"

    # Started as the runner starts it; still waiting for QEMU when timeout stops it is exit 124.
    local dir status=0 output
    dir="$(mktemp -d)"
    mkdir "$dir/share"
    output="$(timeout 3 "$binary" --socket-path="$dir/fs.sock" --shared-dir="$dir/share" \
        --readonly --sandbox=namespace --cache=never \
        --uid-map=":1000:$(id -u):1:" --gid-map=":1000:$(id -g):1:" 2>&1)" || status=$?
    local socket=no
    [ -S "$dir/fs.sock" ] && socket=yes
    rm -rf "$dir"
    if [ "$status" != 124 ] || [ "$socket" != yes ] || grep -q ERROR <<<"$output"; then
        fail "virtiofsd did not start in its sandbox (exit $status):
$output"
    fi
    ok "virtiofsd starts read-only in its namespace sandbox"
    echo "${green}virtiofsd ok for $(id -un)${plain}"
}

remove() {
    if dpkg-divert --list "$binary" | grep -q "$binary.distrib"; then
        sudo rm -f "$binary"
        sudo dpkg-divert --local --rename --remove "$binary"
    fi
    echo "virtiofsd is $(installed || echo missing) again"
}

case "${1:-install}" in
install)
    setup
    check
    ;;
check) check ;;
remove) remove ;;
*) fail "usage: virtiofsd-install.sh [install|check|remove]" ;;
esac
