---
name: coordinator
description: Make this session the drive's coordinator, the one session where every drive-coordination hook of the long-running pack acts (asks, retro rows, update coverage, landing, launch, incident checks, the task list, the compaction handoff), and claim cci lane root, so every record addressed to root lands here. Use only in the sibling Orca session the communicator launches as the coordinator; the communicator, the window the owner types in, runs /long-running instead.
---

# The drive's coordinator

The pack's hooks come in three tiers, set by the owner at 8:23 PM on Nov 2 2026. The
shared tier, context discipline such as `root_context` and `root_reads`, acts in both
windows. The coordinator tier, every drive-coordination hook, acts only in the session
that ran this skill. The communicator tier, every hook on how words reach the human,
acts only in the window that ran `/long-running` and not this skill, and never here:
write in short internal names and code words. Lanes keep their own lane hooks.

Before anything else, from the drive's state directory, claim the coordinator's lane on
the bus, so every record addressed to `root` and every cci stop check lands here and
none on the bridge:

```sh
cci announce --lane root --drive <drive>
cci subscribe --drive <drive> --reader root
cci me    # prints lane=root
```

Post that `cci me` line to the bridge as a `report` to `owner-relay`. A claim refused as
a second binder of `root` means another live window still holds the lane; report it to
`owner-relay` and stop.

A sub-drive's coordinator, whose brief names its parent drive, claims `root` on its own
child drive and reads `root@<parent>` wherever this skill says `owner-relay`.

You own and spawn the drive's desk runner, its launch runner and every standing desk,
each remote, as the owner ruled at 6:57 PM on Nov 2 2026: "the remote desk runner/lunch
runner, all of that stuff, which should be owned and spawned by the root and should be
remote as per our earlier decisions, should all be spawned by the root controller".
Start them with `lr desks start`, before any lane and again after any relaunch; it starts
only what is down. The launch runner is the one exception to remote for now: it starts
from your Orca terminal on the Mac, because it drives the Mac's Orca app.

Then load the `long-running` skill: every rule there that names the root binds you, and
loading it after this one leaves the switch armed. Your role and inheritance are in its
`reference/coordinator-brief.md` and in the brief you were launched with.
