#!/bin/sh
# Installs the naos runner from the GitHub releases of the repository.
set -eu

repo="${NAOS_REPO:-oberon-systems/naos}"
prefix="${NAOS_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/naos/bin}"
version="${NAOS_VERSION:-}"

fail() {
    echo "install: $1" >&2
    exit 1
}

for tool in curl sha256sum; do
    command -v "$tool" >/dev/null || fail "$tool is required"
done

if [ -z "$version" ]; then
    # The repository tags images and packages as well, so /releases/latest can
    # answer with any of them; the newest runner- tag is taken from the list.
    version="$(curl -fsSL "https://api.github.com/repos/$repo/releases?per_page=100" |
        sed -n 's/.*"tag_name": *"runner-\([^"]*\)".*/\1/p' | head -n 1)"
    [ -n "$version" ] || fail "no runner release in $repo"
fi

asset="naos-runner-$version-x86_64-linux-gnu"
url="https://github.com/$repo/releases/download/runner-$version"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/$asset" "$url/$asset" || fail "cannot download $asset"
curl -fsSL -o "$tmp/SHA256SUMS" "$url/SHA256SUMS" || fail "cannot download SHA256SUMS"

grep " $asset\$" "$tmp/SHA256SUMS" >"$tmp/SHA256SUM" || fail "$asset is not in SHA256SUMS"
(cd "$tmp" && sha256sum -c SHA256SUM >/dev/null) || fail "checksum mismatch for $asset"

mkdir -p "$prefix"
install -m 0755 "$tmp/$asset" "$prefix/runner-$version"
ln -sfn "$prefix/runner-$version" "$prefix/runner"

echo "runner $version installed in $prefix"
case ":$PATH:" in
*":$prefix:"*) ;;
*) echo "add it to PATH: export PATH=\"$prefix:\$PATH\"" ;;
esac
