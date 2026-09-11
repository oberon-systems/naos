# 04 — Runner Design

## Prompt

Implement `naos-agent` as a Rust reconciliation/execution daemon.

## Modules

```text
api-client
lease-manager
scheduler
run-manager
qemu-manager
overlay-manager
mount-manager
gate-manager
console-server
diff-manager
reconciler
audit-log
```

## Responsibilities

Register, renew lease, receive Runs, create/manage QEMU, configure overlays/mounts/gates, proxy console, collect overlay, report lifecycle, reconcile after restart.

The API provides declarative desired state. Runner executes it exactly and never broadens permissions.

## Reconciliation

After restart:

1. register/authenticate;
2. renew lease;
3. fetch assigned active Runs;
4. inspect local VMs;
5. compare desired/actual state;
6. recover valid Runs;
7. terminate orphaned VMs;
8. report state.

Duplicate work must not create duplicate VMs.

## Acceptance

Runner survives API/queue restart, cleans orphan resources, and never invents permissions.
