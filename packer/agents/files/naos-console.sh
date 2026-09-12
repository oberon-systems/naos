# shellcheck shell=sh
# ttyS0 is the console the runner exposes: land on the agent session, not a bare
# shell. Waits briefly because the login can win the race against naos-session.

if [ "$(tty 2>/dev/null)" = /dev/ttyS0 ] && [ -z "${TMUX:-}" ]; then
    naos_wait=0
    while ! tmux has-session -t agent 2>/dev/null && [ "$naos_wait" -lt 30 ]; do
        sleep 1
        naos_wait=$((naos_wait + 1))
    done
    unset naos_wait
    if tmux has-session -t agent 2>/dev/null; then
        exec tmux attach-session -t agent
    fi
fi
