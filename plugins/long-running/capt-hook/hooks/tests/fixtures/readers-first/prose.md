port of #51453

## Context

The parent PR, #51542, ports #51453's `// #runtime_flag pfor_limit <n>` and `js_result_nodes_limit <n>` to `go/sand/next`. It also teaches the snapshot reader to restore a fiber's stored limits. Fan-out and async fibers capture the limits when they spawn.

## Summary

The three snapshot writers on next now store each fiber's `pfor_limit` and `js_result_nodes_limit`. A pfor or async member restored after a pause keeps a raised limit instead of falling back to the default.

## Motivation

The owner picked "Port it, persisting the limits in next's snapshot reader-first" on card card-port-51453-integer-flags at 2:57 PM on Nov 5. This is the writer half. Under the reader-first rule, it lands a release after #51542's reader.

## Risk profile

- **Threat model:** Sand authors set the flags in their own source. Nothing new is reachable.
- **Availability:** a binary older than #51542 ignores the new fields, so a run it restores uses the default limits. That is why this PR waits a release.
- **Data:** snapshot fiber nodes and Iris chat thread pieces gain two optional JSON fields. They are written only when a fiber carries a non-zero limit.

## Design

- The snapshot replaced the old tree's `durable/vmstate.go`, so each of next's writers gets the field: `vm.exportFiber` carries it into the delta, `vm_run.snapshotter.sealFiber` writes run snapshots, and `iris/chatdb.VMState.snapshotThread` writes chat thread pieces.
- `omitempty` keeps a fiber without a raised limit byte-identical to today's node.

## Changes

- #51453's `exportFiber`, `sealFiber`, and test-helper `exportSnapshot` lines, plus the same line in `snapshotThread`.
- `TestRuntimeLimitsSurviveFanoutAndRestore` gets back its restore half from #51453: a pfor member spawned under raised limits is snapshotted, restored, and still carries both limits.
