#!/bin/sh
# Writes the section of a naos release into CHANGELOG.md: the commits of the api, web,
# runner and image since the previous release, grouped by type. `cz bump` runs it.
set -eu

new="${CZ_PRE_NEW_VERSION:?run by cz bump}"
previous="${CZ_PRE_CURRENT_TAG_VERSION:-}"
root="$(git rev-parse --show-toplevel)"
file="$root/release/CHANGELOG.md"

range=HEAD
if [ -n "$previous" ] && git -C "$root" rev-parse -q --verify "refs/tags/$previous" >/dev/null; then
    range="$previous..HEAD"
fi

component() {
    git -C "$root" diff-tree --no-commit-id --name-only -r "$1" |
        sed -n 's#^packages/\(api\|web\|runner\)/.*#\1#p; s#^packer/.*#image#p' |
        sort -u | paste -sd, - | sed 's/,/, /g'
}

entries() {
    git -C "$root" log --no-merges --format='%H %s' "$range" -- \
        packages/api packages/web packages/runner packer |
        while read -r hash subject; do
            type="$(printf '%s\n' "$subject" | sed -n 's/^\(\[!\]\)\{0,1\}\[\([a-z]*\)\]\[[^]]*\]: .*/\2/p')"
            [ -n "$type" ] || continue
            printf '%s\t- **%s**: %s\n' "$type" "$(component "$hash")" "${subject#*]: }"
        done
}

section() {
    lines="$(entries)"
    printf '## %s (%s)\n' "$new" "$(date +%Y-%m-%d)"
    for pair in "feat:Features" "fix:Bug Fixes" "refactor:Refactor" "build:Build" "docs:Documentation"; do
        group="$(printf '%s\n' "$lines" | sed -n "s/^${pair%%:*}\t//p")"
        [ -n "$group" ] || continue
        printf '\n### %s\n\n%s\n' "${pair#*:}" "$group"
    done
}

body="$(sed 1d "$file")"
{
    printf '# Changelog\n\n'
    section
    [ -z "$body" ] || printf '%s\n' "$body"
} >"$file"
