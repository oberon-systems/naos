#!/bin/sh
# The last build step. After it the only way into a Run is the runner's console.
set -eu

rc-update del sshd default
rm -f /etc/ssh/ssh_host_*
passwd -l root
passwd -l naos

printf 'auto lo\niface lo inet loopback\n' >/etc/network/interfaces

rm -rf /root/.ash_history /home/naos/.ash_history /var/cache/apk/* /tmp/*
