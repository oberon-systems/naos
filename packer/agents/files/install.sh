#!/bin/sh
# Runs as root inside the agents image build.
set -eu

files=/tmp/naos-files
home=/home/naos

apk add --no-cache bash git libgcc libstdc++ nodejs npm ripgrep tmux

npm install -g \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "@google/gemini-cli@${GEMINI_CLI_VERSION}"
claude --version
gemini --version

install -d -m 0755 /etc/claude-code
install -m 0644 "$files/claude/managed-settings.json" /etc/claude-code/managed-settings.json
install -d -m 0755 /etc/naos /etc/gemini-cli
install -m 0444 "$files/AGENTS.md" /etc/naos/AGENTS.md
ln -sf /etc/naos/AGENTS.md /etc/claude-code/CLAUDE.md
install -m 0644 "$files/gemini/settings.json" /etc/gemini-cli/settings.json

install -m 0755 "$files/naos-session" /etc/init.d/naos-session
install -m 0644 "$files/naos-console.sh" /etc/profile.d/naos-console.sh
rc-update add naos-session default

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

rm -rf "$files" /root/.npm /var/cache/apk/*
