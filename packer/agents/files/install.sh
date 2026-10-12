#!/bin/sh
# Runs as root inside the agents image build.
set -eu

files=/tmp/naos-files
home=/home/naos

# The base filesystem is as large as the base disk; it is grown to the disk of this image.
root=$(awk '$2 == "/" { print $1 }' /proc/mounts)
disk=${root%"${root##*[!0-9]}"}
apk add --no-cache --virtual .grow cloud-utils-growpart e2fsprogs-extra
growpart "$disk" "${root#"$disk"}"
resize2fs "$root"
apk del .grow

apk add --no-cache bash git libgcc libstdc++ nodejs npm ripgrep socat tmux

npm install -g \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "@google/gemini-cli@${GEMINI_CLI_VERSION}"
claude --version
gemini --version

# The musl build is the one that runs on Alpine; the archive holds the binary alone.
goose=/tmp/goose
install -d "$goose"
wget -qO- "https://github.com/block/goose/releases/download/v${GOOSE_VERSION}/goose-x86_64-unknown-linux-musl.tar.gz" |
    tar -xz -C "$goose"
mv "$(find "$goose" -type f -name goose | head -n 1)" /usr/local/bin/goose
chown root:root /usr/local/bin/goose
chmod 0755 /usr/local/bin/goose
rm -rf "$goose"
goose --version

install -d -m 0755 /etc/claude-code
install -m 0644 "$files/claude/managed-settings.json" /etc/claude-code/managed-settings.json
install -m 0644 "$files/claude/managed-mcp.json" /etc/claude-code/managed-mcp.json
install -d -m 0755 /etc/naos /etc/gemini-cli
install -m 0444 "$files/AGENTS.md" /etc/naos/AGENTS.md
ln -sf /etc/naos/AGENTS.md /etc/claude-code/CLAUDE.md
install -m 0644 "$files/gemini/settings.json" /etc/gemini-cli/settings.json

install -m 0755 "$files/naos-session" /etc/init.d/naos-session
install -m 0755 "$files/naos-mcp" /usr/local/bin/naos-mcp
install -m 0755 "$files/naos-model" /etc/init.d/naos-model
install -m 0644 "$files/naos-model.sh" /etc/profile.d/naos-model.sh
install -m 0755 "$files/naos-resize" /etc/init.d/naos-resize
install -m 0755 "$files/naos-resize.sh" /usr/local/bin/naos-resize
install -m 0644 "$files/naos-console.sh" /etc/profile.d/naos-console.sh
rc-update add naos-session default
rc-update add naos-model default
rc-update add naos-resize default

# The agents keep their state in these directories, so they stay naos-owned;
# only the shipped skill and instructions inside them belong to root.
for agent in .claude .gemini; do
    install -d -o naos -g naos -m 0700 "$home/$agent" "$home/$agent/skills"
    install -d -o root -g root -m 0755 "$home/$agent/skills/naos-environment"
    install -o root -g root -m 0444 \
        "$files/skills/naos-environment/SKILL.md" \
        "$home/$agent/skills/naos-environment/SKILL.md"
done
ln -sf /etc/naos/AGENTS.md "$home/.gemini/GEMINI.md"

# Goose rewrites its own config, so the file is naos-owned; the instructions stay root's.
install -d -o naos -g naos -m 0700 "$home/.config" "$home/.config/goose"
install -o naos -g naos -m 0600 "$files/goose/config.yaml" "$home/.config/goose/config.yaml"
ln -sf /etc/naos/AGENTS.md "$home/.config/goose/.goosehints"

# Claude Code asks before it uses a key from the environment; the placeholder is approved here.
install -o naos -g naos -m 0600 "$files/claude/claude.json" "$home/.claude.json"

rm -rf "$files" /root/.npm /var/cache/apk/*
