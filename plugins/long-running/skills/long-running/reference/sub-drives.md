# Sub-drives

A sub-drive is an option the coordinator holds and never a default. The coordinator
alone decides, and only for a big project plainly unrelated to the rest of the drive.
The test is five facts it can state: the project shares no PR, no file, no plan step
and no release target with the parent's work, and it needs lanes of its own. If any
one is doubtful, the work is a lane in the parent drive.

```sh
lr drive sub start <name> --objective <what it delivers> --reason <why it is separate> \
  --no-shared-prs <how you know> --no-shared-files <how you know> \
  --no-shared-steps <how you know> --no-shared-targets <how you know> \
  --own-lanes <the lanes it needs> [--repo <owner/name> --checkout <path>]
```

The verb refuses without the reason and every fact, and refuses in any window but a
coordinator's. It waits on a launch, so run it with `run_in_background`. It starts the
project as a drive of its own: a small coordinator launched the way `lr launch` starts
one, its own lanes, ledger, Orca Run and supervisor, and its own cci drive,
`<parent>--<name>`. It is a drive and not a child topic because cci binds a lane per
drive: a second session cannot hold `root` on the parent's drive, and every root hook,
stop check and cursor keys on lane and drive. The launch sets
`CLAUDE_LONG_RUNNING_DRIVE` to the child, so its coordinator runs
`/long-running:coordinator` and gets the same hooks, each acting on the child's
registry entry and bus. It starts no sub-drive of its own.

It reports to the parent's coordinator only, as `--to root@<parent>`, and never to the
communicator or the owner; the pack blocks a `cci post` to either from any session of
the sub-drive, and its supervisor sends a dead coordinator's blocker to the parent too.
The parent folds each report into the drive's record: a ledger row for the project, one
line in its progress doc, and a `report` to `owner-relay` when the owner must see it.
It carries the owner's answers and rulings down with `--to root@<parent>--<name>` or
`--re`. A PR tap the sub-drive waits on goes up the same way, as an `ask`.

`lr drive sub list` names each open sub-drive with what it still holds.
`lr drive sub end <name>` hands every open lane, record and PR back to the parent as
one `ask` it must close, posts the child's sessions a stand-down, removes its
supervisor and registry entry, and names the `orca-gc` line to run once they stop.
