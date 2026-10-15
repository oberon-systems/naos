#!/bin/sh
# Installs the naos runner of a naos release and its systemd unit:
#   curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-runner.sh | sh -s -- --help
set -eu

stamped_release=
stamped_runner=

repo="${NAOS_REPO:-oberon-systems/naos}"
docs="https://github.com/$repo/blob/main/docs/host"
unit_name=naos-runner.service

usage() {
    cat <<EOF
usage: install-runner.sh [doctor|uninstall] [flags]

Without a command it installs or upgrades the runner; uninstall removes all of it. Every value it lacks is
asked on the terminal; --yes, or no terminal, turns a missing value into an error.

  --release X.Y.Z             naos release to install (default: the latest)
  --layout xdg|system         xdg: your home, no sudo; system: /opt/naos/runner
  --api-url URL               naos api the runner leases from
  --name NAME                 runner name (default: the host name)
  --enrollment-token-file F   file holding the enrollment token
  --capacity N                Runs at once (default 1)
  --zone ZONE                 zone label
  --labels A,B                labels
  --systemd | --no-systemd    install the systemd unit (default: yes)
  --linger | --no-linger      keep the user unit running without a login (xdg)
  --yes                       never ask
  --force                     uninstall without asking
EOF
}

fail() {
    echo "install-runner: $*" >&2
    exit 1
}

say() {
    echo "install-runner: $*"
}

need_value() {
    [ "$2" -ge 2 ] || fail "$1 needs a value"
}

latest_release() {
    url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$repo/releases/latest")" ||
        fail "cannot reach github.com/$repo"
    case "${url##*/}" in
    [0-9]*.[0-9]*.[0-9]*) printf '%s\n' "${url##*/}" ;;
    *) fail "github.com/$repo has no naos release yet" ;;
    esac
}

# An unstamped copy, or a request for another release, runs that release's own script.
delegate() {
    [ -z "${NAOS_INSTALL_DELEGATED:-}" ] || fail "release $1 does not stamp its installer"
    dir="$(mktemp -d)"
    base="https://github.com/$repo/releases/download/$1"
    curl -fsSL -o "$dir/install-runner.sh" "$base/install-runner.sh" ||
        fail "release $1 has no install-runner.sh"
    curl -fsSL -o "$dir/SHA256SUMS" "$base/SHA256SUMS" || fail "release $1 has no SHA256SUMS"
    (cd "$dir" && grep ' install-runner.sh$' SHA256SUMS | sha256sum -c - >/dev/null) ||
        fail "install-runner.sh of $1 does not match its SHA256SUMS"
    shift
    status=0
    NAOS_INSTALL_DELEGATED=1 sh "$dir/install-runner.sh" "$@" || status=$?
    rm -rf "$dir"
    exit "$status"
}

wanted=
previous=
for arg in "$@"; do
    [ "$previous" != --release ] || wanted="$arg"
    previous="$arg"
done
for tool in curl sha256sum; do
    command -v "$tool" >/dev/null || fail "$tool is required"
done
target="${wanted:-$(latest_release)}"
[ "$target" = "$stamped_release" ] || delegate "$target" "$@"

command=install
layout=''
api_url=''
name=''
token_file=''
capacity=''
zone=''
labels=''
systemd=''
linger=''
yes=''
force=''
while [ $# -gt 0 ]; do
    case "$1" in
    doctor | uninstall) command="$1" ;;
    --release) need_value "$1" $# && shift ;;
    --layout) need_value "$1" $# && layout="$2" && shift ;;
    --api-url) need_value "$1" $# && api_url="$2" && shift ;;
    --name) need_value "$1" $# && name="$2" && shift ;;
    --enrollment-token-file) need_value "$1" $# && token_file="$2" && shift ;;
    --capacity) need_value "$1" $# && capacity="$2" && shift ;;
    --zone) need_value "$1" $# && zone="$2" && shift ;;
    --labels) need_value "$1" $# && labels="$2" && shift ;;
    --systemd) systemd=yes ;;
    --no-systemd) systemd=no ;;
    --linger) linger=yes ;;
    --no-linger) linger=no ;;
    --yes) yes=1 ;;
    --force) force=1 ;;
    -h | --help) usage && exit 0 ;;
    *) fail "unknown argument $1, see --help" ;;
    esac
    shift
done

interactive() {
    [ -z "$yes" ] && (: </dev/tty) 2>/dev/null
}

# ask VAR QUESTION DEFAULT FLAG: keeps a value already set, else asks, else takes the default.
ask() {
    eval "current=\${$1}"
    [ -z "$current" ] || return 0
    if interactive; then
        printf '%s [%s]: ' "$2" "$3" >/dev/tty
        read -r answer </dev/tty || answer=
        eval "$1=\${answer:-\$3}"
    elif [ -n "$3" ]; then
        eval "$1=\$3"
    else
        fail "no terminal to ask for $2: pass $4"
    fi
}

ask_yes() {
    eval "current=\${$1}"
    [ -z "$current" ] || return 0
    if interactive; then
        printf '%s [Y/n]: ' "$2" >/dev/tty
        read -r answer </dev/tty || answer=
        case "$answer" in [nN]*) eval "$1=no" ;; *) eval "$1=yes" ;; esac
    else
        eval "$1=yes"
    fi
}

ask_secret() {
    interactive || fail "no terminal to ask for the enrollment token: pass --enrollment-token-file"
    printf 'enrollment token (not shown): ' >/dev/tty
    stty -echo </dev/tty
    read -r secret </dev/tty || secret=
    stty echo </dev/tty
    printf '\n' >/dev/tty
    [ -n "$secret" ] || fail "no enrollment token given"
}

paths() {
    case "$layout" in
    xdg)
        bin_dir="${XDG_DATA_HOME:-$HOME/.local/share}/naos/bin"
        conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/naos"
        state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/naos/agent"
        link="$bin_dir/runner"
        unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
        ;;
    system)
        bin_dir=/opt/naos/runner/bin
        conf_dir=/opt/naos/runner
        state_dir=/opt/naos/runner/state
        link=/usr/local/bin/runner
        unit_dir=/etc/systemd/system
        ;;
    *) fail "--layout is xdg or system, not $layout" ;;
    esac
    env_file="$conf_dir/runner.env"
}

detect_layout() {
    [ -z "$layout" ] || return 0
    if [ -f /opt/naos/runner/runner.env ]; then layout=system; fi
    if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/naos/runner.env" ]; then layout=xdg; fi
}

choose_layout() {
    detect_layout
    [ -z "$layout" ] || return 0
    interactive || fail "no terminal to choose a layout: pass --layout xdg or --layout system"
    cat >/dev/tty <<EOF
Where should the runner live?
  xdg     ${XDG_DATA_HOME:-$HOME/.local/share}/naos/bin, ${XDG_CONFIG_HOME:-$HOME/.config}/naos,
          ${XDG_STATE_HOME:-$HOME/.local/state}/naos/agent, a systemd user unit; no sudo
  system  /opt/naos/runner/{bin,state}, /usr/local/bin/runner, a system unit
          that runs as $(id -un); needs sudo
EOF
    ask layout "layout" xdg --layout
}

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

# Writes with root only where the system layout puts a file.
put() {
    if [ "$layout" = system ]; then as_root "$@"; else "$@"; fi
}

systemctl_unit() {
    if [ "$layout" = system ]; then as_root systemctl "$@"; else systemctl --user "$@"; fi
}

setting() {
    [ -f "$env_file" ] || return 0
    sed -n "s/^$1=//p" "$env_file"
}

problems=

problem() {
    problems="$problems
  - $1
    fix: $2"
}

virtiofsd_version_ok() {
    version="$(/usr/libexec/virtiofsd --version 2>/dev/null |
        sed -n 's/[^0-9]*\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p' | head -n 1)"
    [ -n "$version" ] || return 1
    major="${version% *}"
    minor="${version#* }"
    [ "$major" -gt 1 ] || { [ "$major" -eq 1 ] && [ "$minor" -ge 13 ]; }
}

userns_ok() {
    [ "$(cat /proc/sys/user/max_user_namespaces 2>/dev/null || echo 0)" -gt 0 ] || return 1
    [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" = 1 ] || return 0
    grep -ls 'userns' /etc/apparmor.d/* 2>/dev/null | xargs -r grep -l '/usr/libexec/virtiofsd' >/dev/null 2>&1
}

doctor() {
    problems=
    [ "$(uname -s)/$(uname -m)" = Linux/x86_64 ] ||
        problem "the runner is built for Linux on x86_64, this is $(uname -s)/$(uname -m)" "use an x86_64 Linux host"
    [ "$(id -u)" != 0 ] ||
        problem "running as root; the runner runs as an unprivileged user" "run the installer as that user, sudo is used where needed"
    { [ -r /dev/kvm ] && [ -w /dev/kvm ]; } ||
        problem "no read and write access to /dev/kvm" "enable KVM and add $(id -un) to the kvm group"
    { [ -r /dev/vhost-vsock ] && [ -w /dev/vhost-vsock ]; } ||
        problem "no read and write access to /dev/vhost-vsock" "$docs/vhost-vsock.md"
    for binary in /usr/bin/qemu-system-x86_64 /usr/bin/qemu-img /usr/bin/git; do
        [ -x "$binary" ] || problem "$binary is missing" "install the package that provides it"
    done
    virtiofsd_version_ok ||
        problem "/usr/libexec/virtiofsd is missing or older than 1.13" "$docs/build-virtiofsd.md"
    userns_ok ||
        problem "unprivileged user namespaces are off, or AppArmor allows none to virtiofsd" "$docs/build-virtiofsd.md"
    for ids in /etc/subuid /etc/subgid; do
        grep -q "^$(id -un):" "$ids" 2>/dev/null ||
            problem "$ids lists no range for $(id -un)" "sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(id -un)"
    done
    if [ -n "$api_url" ]; then
        curl -fsS --max-time 10 -o /dev/null "$api_url/healthz" ||
            problem "the api does not answer at $api_url/healthz" "check --api-url and that the stack is up"
    fi
    if [ "$systemd" = yes ]; then
        command -v systemctl >/dev/null || problem "no systemctl" "pass --no-systemd and start the runner yourself"
    fi
    if [ "$layout" = system ] && [ "$(id -u)" != 0 ]; then
        command -v sudo >/dev/null || problem "the system layout needs sudo" "install sudo or use --layout xdg"
    fi
    if [ -n "$problems" ]; then
        echo "install-runner: doctor found problems:$problems" >&2
        return 1
    fi
    say "doctor: the host can run the runner"
}

download() {
    asset="naos-runner-$stamped_runner-x86_64-linux-gnu"
    url="https://github.com/$repo/releases/download/runner-$stamped_runner"
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/$asset" "$url/$asset" || fail "cannot download $asset"
    curl -fsSL -o "$tmp/SHA256SUMS" "$url/SHA256SUMS" || fail "cannot download SHA256SUMS"
    (cd "$tmp" && grep " $asset\$" SHA256SUMS | sha256sum -c - >/dev/null) ||
        fail "$asset does not match its SHA256SUMS"
    put install -d -m 0755 "$bin_dir"
    put install -m 0755 "$tmp/$asset" "$bin_dir/runner-$stamped_runner"
    rm -rf "$tmp"
    if [ "$layout" = system ]; then
        as_root ln -sfn "$bin_dir/runner-$stamped_runner" "$bin_dir/runner"
        as_root ln -sfn "$bin_dir/runner" "$link"
    else
        ln -sfn "$bin_dir/runner-$stamped_runner" "$link"
    fi
}

configure() {
    owner="$(id -un)"
    group="$(id -gn)"
    put install -d -m 0700 -o "$owner" -g "$group" "$conf_dir" "$state_dir"
    token_path="$conf_dir/enrollment"
    if [ -n "$token_file" ]; then
        [ -r "$token_file" ] || fail "cannot read $token_file"
        (umask 077 && tr -d '\n' <"$token_file" >"$token_path.new")
        mv "$token_path.new" "$token_path"
    elif [ ! -s "$token_path" ] && [ ! -f "$state_dir/credentials.json" ]; then
        ask_secret
        (umask 077 && printf '%s' "$secret" >"$token_path.new")
        mv "$token_path.new" "$token_path"
    fi
    {
        echo "NAOS_AGENT_API_URL=$api_url"
        echo "NAOS_AGENT_NAME=$name"
        echo "NAOS_AGENT_STATE_DIR=$state_dir"
        echo "NAOS_AGENT_ENROLLMENT_TOKEN_FILE=$token_path"
        [ -z "$capacity" ] || echo "NAOS_AGENT_CAPACITY=$capacity"
        [ -z "$zone" ] || echo "NAOS_AGENT_ZONE=$zone"
        [ -z "$labels" ] || echo "NAOS_AGENT_LABELS=$labels"
    } >"$env_file.new"
    chmod 0600 "$env_file.new"
    mv "$env_file.new" "$env_file"
}

linger_on() {
    loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -qx Linger=yes
}

install_unit() {
    [ "$systemd" = yes ] || return 0
    if [ "$layout" = system ]; then
        user_line="User=$(id -un)"
        target=multi-user.target
    else
        user_line=
        target=default.target
    fi
    unit="$(mktemp)"
    cat >"$unit" <<EOF
[Unit]
Description=naos runner
After=network-online.target
Wants=network-online.target

[Service]
$user_line
Environment=NAOS_AGENT_ENV_FILE=$env_file
ExecStart=$link
Restart=on-failure
RestartSec=5

[Install]
WantedBy=$target
EOF
    put install -d -m 0755 "$unit_dir"
    put install -m 0644 "$unit" "$unit_dir/$unit_name"
    rm -f "$unit"
    systemctl_unit daemon-reload
    systemctl_unit enable "$unit_name" >/dev/null 2>&1
    systemctl_unit restart "$unit_name"
    say "the runner runs as $unit_name"
    [ "$layout" = xdg ] || return 0
    linger_on && return 0
    ask_yes linger "keep the runner running after you log out (loginctl enable-linger)?"
    [ "$linger" = yes ] || return 0
    loginctl enable-linger "$(id -un)" 2>/dev/null || as_root loginctl enable-linger "$(id -un)" ||
        say "could not enable linger, run: sudo loginctl enable-linger $(id -un)"
}

confirm() {
    [ -z "$force" ] || return 0
    interactive || fail "no terminal to confirm: pass --force"
    printf '%s\nremove all of it? [y/N]: ' "$1" >/dev/tty
    read -r answer </dev/tty || answer=
    case "$answer" in [yY]*) ;; *) fail "nothing removed" ;; esac
}

# The state can hold files of the Runs' user namespaces, which only root removes.
uninstall() {
    detect_layout
    [ -n "$layout" ] || { say "no runner is installed" && return 0; }
    paths
    confirm "This removes the naos runner: $unit_name, $link, the binaries in $bin_dir,
the settings and the enrollment token in $conf_dir and the state in $state_dir."
    if [ -f "$unit_dir/$unit_name" ]; then
        systemctl_unit disable --now "$unit_name" >/dev/null 2>&1 || true
        put rm -f "$unit_dir/$unit_name"
        systemctl_unit daemon-reload
    fi
    put rm -f "$link"
    put rm -rf "$bin_dir" "$env_file" "$conf_dir/enrollment"
    put rm -rf "$state_dir" 2>/dev/null || as_root rm -rf "$state_dir"
    if [ "$layout" = system ]; then
        as_root rm -rf "$conf_dir"
        as_root rmdir "${conf_dir%/*}" 2>/dev/null || true
    else
        rmdir "$conf_dir" "${bin_dir%/*}" "${state_dir%/*}" 2>/dev/null || true
    fi
    say "removed the runner"
    [ "$layout" = system ] || ! linger_on || say "linger stays on, turn it off with: loginctl disable-linger"
}

if [ "$command" = doctor ]; then
    detect_layout
    if [ -n "$layout" ]; then
        paths
        api_url="${api_url:-$(setting NAOS_AGENT_API_URL)}"
    fi
    doctor
    exit
fi

if [ "$command" = uninstall ]; then
    uninstall
    exit 0
fi

choose_layout
paths

api_url="${api_url:-$(setting NAOS_AGENT_API_URL)}"
name="${name:-$(setting NAOS_AGENT_NAME)}"
capacity="${capacity:-$(setting NAOS_AGENT_CAPACITY)}"
zone="${zone:-$(setting NAOS_AGENT_ZONE)}"
labels="${labels:-$(setting NAOS_AGENT_LABELS)}"
ask api_url "naos api url" "" --api-url
ask name "runner name" "$(uname -n)" --name
if [ ! -f "$env_file" ]; then
    ask capacity "Runs at once" 1 --capacity
fi
if command -v systemctl >/dev/null; then
    ask_yes systemd "install the systemd unit?"
else
    systemd="${systemd:-no}"
fi

doctor || exit 1

download
configure
install_unit
say "runner $stamped_runner of naos $stamped_release installed, $link"
[ "$systemd" = yes ] || say "start it with: NAOS_AGENT_ENV_FILE=$env_file $link"
