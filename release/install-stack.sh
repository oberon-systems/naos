#!/bin/sh
# Installs or upgrades the naos stack of a naos release: compose, its .env, a systemd unit:
#   curl -fsSL https://github.com/oberon-systems/naos/releases/latest/download/install-stack.sh | sh -s -- --help
set -eu

stamped_release=
stamped_image=
stamped_image_sha256=

repo="${NAOS_REPO:-oberon-systems/naos}"
unit_name=naos-stack.service

usage() {
    cat <<EOF
usage: install-stack.sh [doctor] [flags]

Without a command it installs or upgrades the stack. Every value it lacks is
asked on the terminal; --yes, or no terminal, takes the defaults.

  --release X.Y.Z             naos release to install (default: the latest)
  --layout xdg|system         xdg: your home, no sudo; system: /opt/naos
  --bind ADDRESS              address both ports are published on (default 127.0.0.1)
  --api-port PORT             api port (default 8080)
  --web-port PORT             web ui port (default 8000)
  --systemd | --no-systemd    install the systemd unit (default: yes)
  --linger | --no-linger      keep the user unit running without a login (xdg)
  --yes                       never ask
EOF
}

fail() {
    echo "install-stack: $*" >&2
    exit 1
}

say() {
    echo "install-stack: $*"
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
    curl -fsSL -o "$dir/install-stack.sh" "$base/install-stack.sh" ||
        fail "release $1 has no install-stack.sh"
    curl -fsSL -o "$dir/SHA256SUMS" "$base/SHA256SUMS" || fail "release $1 has no SHA256SUMS"
    (cd "$dir" && grep ' install-stack.sh$' SHA256SUMS | sha256sum -c - >/dev/null) ||
        fail "install-stack.sh of $1 does not match its SHA256SUMS"
    shift
    status=0
    NAOS_INSTALL_DELEGATED=1 sh "$dir/install-stack.sh" "$@" || status=$?
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
bind=''
api_port=''
web_port=''
systemd=''
linger=''
yes=''
while [ $# -gt 0 ]; do
    case "$1" in
    doctor) command="$1" ;;
    --release) need_value "$1" $# && shift ;;
    --layout) need_value "$1" $# && layout="$2" && shift ;;
    --bind) need_value "$1" $# && bind="$2" && shift ;;
    --api-port) need_value "$1" $# && api_port="$2" && shift ;;
    --web-port) need_value "$1" $# && web_port="$2" && shift ;;
    --systemd) systemd=yes ;;
    --no-systemd) systemd=no ;;
    --linger) linger=yes ;;
    --no-linger) linger=no ;;
    --yes) yes=1 ;;
    -h | --help) usage && exit 0 ;;
    *) fail "unknown argument $1, see --help" ;;
    esac
    shift
done

interactive() {
    [ -z "$yes" ] && (: </dev/tty) 2>/dev/null
}

# ask VAR QUESTION DEFAULT: keeps a value already set, else asks, else takes the default.
ask() {
    eval "current=\${$1}"
    [ -z "$current" ] || return 0
    if interactive; then
        printf '%s [%s]: ' "$2" "$3" >/dev/tty
        read -r answer </dev/tty || answer=
        eval "$1=\${answer:-\$3}"
    else
        eval "$1=\$3"
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

xdg_dir="${XDG_CONFIG_HOME:-$HOME/.config}/naos/stack"
xdg_db="${XDG_DATA_HOME:-$HOME/.local/share}/naos/stack"

detect_layout() {
    [ -z "$layout" ] || return 0
    if [ -f /opt/naos/compose.yaml ]; then layout=system; fi
    if [ -f "$xdg_dir/compose.yaml" ]; then layout=xdg; fi
}

choose_layout() {
    detect_layout
    [ -z "$layout" ] || return 0
    interactive || fail "no terminal to choose a layout: pass --layout xdg or --layout system"
    cat >/dev/tty <<EOF
Where should the stack live?
  xdg     $xdg_dir, the database in $xdg_db,
          a systemd user unit; no sudo
  system  /opt/naos, the database in /opt/naos/db, a system unit that runs
          as $(id -un); needs sudo
EOF
    ask layout "layout" xdg
}

paths() {
    case "$layout" in
    xdg)
        dir="$xdg_dir"
        db="$xdg_db"
        unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
        ;;
    system)
        dir=/opt/naos
        db=/opt/naos/db
        unit_dir=/etc/systemd/system
        ;;
    *) fail "--layout is xdg or system, not $layout" ;;
    esac
    env_file="$dir/.env"
}

as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}

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

token() {
    head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'
}

digest() {
    sha256sum "$1" | cut -d' ' -f1
}

problems=

problem() {
    problems="$problems
  - $1
    fix: $2"
}

port_free() {
    ! ss -Hltn "sport = :$1" 2>/dev/null | grep -q .
}

stack_up() {
    [ -f "$dir/compose.yaml" ] && (cd "$dir" && docker compose ps -q 2>/dev/null | grep -q .)
}

doctor() {
    problems=
    [ "$(id -u)" != 0 ] ||
        problem "running as root; the stack runs as a user in the docker group" "run the installer as that user, sudo is used where needed"
    if ! command -v docker >/dev/null; then
        problem "no docker" "install Docker Engine: https://docs.docker.com/engine/install/"
    elif ! docker info >/dev/null 2>&1; then
        problem "the docker daemon does not answer $(id -un)" "start docker and add $(id -un) to the docker group"
    else
        compose="$(docker compose version --short 2>/dev/null || true)"
        [ "${compose%%.*}" -ge 2 ] 2>/dev/null ||
            problem "no docker compose v2" "install the docker compose plugin"
    fi
    command -v ss >/dev/null || problem "no ss" "install iproute2"
    if ! stack_up; then
        for port in "$api_port" "$web_port"; do
            port_free "$port" || problem "port $port is taken" "free it or pass --api-port / --web-port"
        done
    fi
    parent="$dir"
    while [ ! -d "$parent" ]; do parent="$(dirname "$parent")"; done
    if [ "$layout" = system ] && [ "$(id -u)" != 0 ]; then
        command -v sudo >/dev/null || problem "the system layout needs sudo" "install sudo or use --layout xdg"
    elif [ ! -w "$parent" ]; then
        problem "$parent is not writable" "pick another --layout"
    fi
    free="$(df -Pk "$parent" | awk 'NR == 2 { print $4 }')"
    [ "${free:-0}" -ge 5242880 ] || problem "less than 5 GiB free under $parent" "free some disk space"
    if [ -f "$env_file" ]; then
        for pair in NAOS_OPERATOR_TOKEN_SHA256:operator NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256:enrollment; do
            file="$dir/secrets/${pair#*:}"
            if [ ! -f "$file" ] || [ "$(setting "${pair%%:*}")" != "$(digest "$file")" ]; then
                problem "$env_file holds a ${pair%%:*} that $file does not match" "restore $file, or remove $env_file and $db"
            fi
        done
    elif [ -n "$(ls -A "$db" 2>/dev/null)" ]; then
        problem "$db holds a database but $env_file is gone" "restore $env_file, or remove $db"
    fi
    if [ -n "$problems" ]; then
        echo "install-stack: doctor found problems:$problems" >&2
        return 1
    fi
    say "doctor: the host can run the stack"
}

fetch() {
    tmp="$(mktemp -d)"
    base="https://github.com/$repo/releases/download/$stamped_release"
    for file in compose.yaml env.example SHA256SUMS; do
        curl -fsSL -o "$tmp/$file" "$base/$file" || fail "cannot download $file of $stamped_release"
    done
    (cd "$tmp" && grep -E ' (compose\.yaml|env\.example)$' SHA256SUMS | sha256sum -c - >/dev/null) ||
        fail "the files of $stamped_release do not match their SHA256SUMS"
    put install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" "$dir"
    put install -d -m 0700 -o "$(id -un)" -g "$(id -gn)" "$db"
    [ ! -f "$dir/compose.yaml" ] || cp "$dir/compose.yaml" "$dir/compose.yaml.prev"
    cp "$tmp/compose.yaml" "$dir/compose.yaml"
    cp "$tmp/env.example" "$dir/.env.example"
    rm -rf "$tmp"
}

# The web container reads the operator token as nobody, so that one file is 0644
# and the 0700 directory around it keeps it off other accounts.
write_env() {
    [ ! -f "$env_file" ] || return 0
    secrets="$dir/secrets"
    install -d -m 0700 "$secrets"
    (umask 077 && token >"$secrets/operator" && token >"$secrets/enrollment")
    chmod 0644 "$secrets/operator"
    sed -e "s|^NAOS_BIND=.*|NAOS_BIND=$bind|" \
        -e "s|^NAOS_API_PORT=.*|NAOS_API_PORT=$api_port|" \
        -e "s|^NAOS_WEB_PORT=.*|NAOS_WEB_PORT=$web_port|" \
        -e "s|^NAOS_DB_PASSWORD=.*|NAOS_DB_PASSWORD=$(token)|" \
        -e "s|^NAOS_DB_DATA=.*|NAOS_DB_DATA=$db|" \
        -e "s|^NAOS_DATABASE_AUTO_MIGRATE=.*|NAOS_DATABASE_AUTO_MIGRATE=true|" \
        -e "s|^NAOS_OPERATOR_TOKEN_SHA256=.*|NAOS_OPERATOR_TOKEN_SHA256=$(digest "$secrets/operator")|" \
        -e "s|^NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256=.*|NAOS_RUNNER_ENROLLMENT_TOKEN_SHA256=$(digest "$secrets/enrollment")|" \
        -e "s|^NAOS_WEB_API_BASE_URL=.*|NAOS_WEB_API_BASE_URL=http://$host:$api_port|" \
        -e "s|^NAOS_WEB_OPERATOR_TOKEN_FILE=.*|NAOS_WEB_OPERATOR_TOKEN_FILE=$secrets/operator|" \
        "$dir/.env.example" >"$env_file.new"
    chmod 0600 "$env_file.new"
    mv "$env_file.new" "$env_file"
    say "wrote $env_file and the tokens in $secrets"
}

compose() {
    (cd "$dir" && docker compose "$@")
}

linger_on() {
    loginctl show-user "$(id -un)" -p Linger 2>/dev/null | grep -qx Linger=yes
}

install_unit() {
    docker_bin="$(command -v docker)"
    if [ "$layout" = system ]; then
        head="After=docker.service network-online.target
Requires=docker.service"
        user_line="User=$(id -un)"
        wait_line=
        target=multi-user.target
    else
        head=
        user_line=
        wait_line="ExecStartPre=/bin/sh -c 'until $docker_bin info >/dev/null 2>&1; do sleep 2; done'"
        target=default.target
    fi
    unit="$(mktemp)"
    cat >"$unit" <<EOF
[Unit]
Description=naos stack
$head

[Service]
Type=oneshot
RemainAfterExit=yes
$user_line
WorkingDirectory=$dir
$wait_line
ExecStart=$docker_bin compose up -d --wait
ExecStop=$docker_bin compose down
TimeoutStartSec=300

[Install]
WantedBy=$target
EOF
    put install -d -m 0755 "$unit_dir"
    put install -m 0644 "$unit" "$unit_dir/$unit_name"
    rm -f "$unit"
    systemctl_unit daemon-reload
    systemctl_unit enable "$unit_name" >/dev/null 2>&1
}

start() {
    if [ "$systemd" = yes ]; then
        systemctl_unit restart "$unit_name"
    else
        compose up -d --wait
    fi
}

operator() {
    curl -fsS -H "Authorization: Bearer $(cat "$dir/secrets/operator")" "$@"
}

# Every install and upgrade registers the image of its release; the id carries the
# version because the catalog keeps one image per id.
register_image() {
    if operator "$api/api/v1/images" | grep -q "\"sha256:$stamped_image_sha256\""; then
        return
    fi
    url="https://github.com/$repo/releases/download/image-$stamped_image/naos-agents-$stamped_image.qcow2"
    operator -o /dev/null -H "Content-Type: application/json" "$api/api/v1/images" -d @- <<EOF ||
{"id": "naos-agents-$stamped_image", "version": "$stamped_image", "digest": "sha256:$stamped_image_sha256", "url": "$url"}
EOF
        fail "could not register image $stamped_image"
    say "registered image naos-agents-$stamped_image"
}

wait_healthy() {
    seconds=60
    until curl -fs -o /dev/null "$api/healthz"; do
        seconds=$((seconds - 1))
        [ "$seconds" -gt 0 ] || fail "the api did not answer at $api/healthz, see: cd $dir && docker compose logs api"
        sleep 1
    done
}

if [ "$command" = doctor ]; then
    detect_layout
    [ -n "$layout" ] || layout=xdg
    paths
    api_port="${api_port:-$(setting NAOS_API_PORT)}"
    web_port="${web_port:-$(setting NAOS_WEB_PORT)}"
    api_port="${api_port:-8080}"
    web_port="${web_port:-8000}"
    doctor
    exit
fi

choose_layout
paths
upgrade=
[ ! -f "$env_file" ] || upgrade=yes
bind="${bind:-$(setting NAOS_BIND)}"
api_port="${api_port:-$(setting NAOS_API_PORT)}"
web_port="${web_port:-$(setting NAOS_WEB_PORT)}"
ask bind "address to publish the ports on" 127.0.0.1
ask api_port "api port" 8080
ask web_port "web ui port" 8000
if command -v systemctl >/dev/null; then
    ask_yes systemd "install the systemd unit?"
else
    systemd="${systemd:-no}"
fi
case "$bind" in
0.0.0.0 | ::) host=127.0.0.1 ;;
*) host="$bind" ;;
esac
api="http://$host:$api_port"

doctor || exit 1

fetch
write_env
compose pull --quiet
if [ -n "$upgrade" ]; then
    compose run --rm api naos-api migrate
fi
[ "$systemd" != yes ] || install_unit
start
wait_healthy
register_image

if [ "$systemd" = yes ] && [ "$layout" = xdg ] && ! linger_on; then
    ask_yes linger "keep the stack running after you log out (loginctl enable-linger)?"
    if [ "$linger" = yes ]; then
        loginctl enable-linger "$(id -un)" 2>/dev/null || as_root loginctl enable-linger "$(id -un)" ||
            say "could not enable linger, run: sudo loginctl enable-linger $(id -un)"
    fi
fi

cat <<EOF

naos $stamped_release is up.

  web       http://$host:$web_port
  api       $api
  settings  $env_file
  tokens    $dir/secrets/operator (operator), $dir/secrets/enrollment (runner enrollment)

Set NAOS_ALLOWED_MOUNT_ROOTS in $env_file before a Run mounts a workspace.
Install a runner on this host with:

  curl -fsSL https://github.com/$repo/releases/download/$stamped_release/install-runner.sh | sh -s -- \\
    --api-url $api --enrollment-token-file $dir/secrets/enrollment
EOF
