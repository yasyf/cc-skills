---
name: coordinator
description: Make this session the drive's coordinator, the one session where every drive-coordination hook of the long-running pack acts (asks, retro rows, update coverage, landing, launch, incident checks, the task list, the compaction handoff), and claim cci lane root, so every record addressed to the coordinator lands here. Use only in the sibling Orca session the communicator launches as the coordinator; the communicator, the window the owner types in, runs /long-running instead.
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
none on the communicator:

```sh
cci announce --lane root --drive <drive>
cci subscribe --drive <drive> --reader root --to-kind incident
cci me    # prints lane=root
```

That subscribe keeps every other record off your tool calls: they reach you as batches
from your one wait, so a turn handles a batch and never a single record:

```sh
lr coordinator next --width 400 --lines 40 --watch <the PRs you hold>
```

Run it as a foreground Bash call with `timeout: 590000`; the drive and cursor default to this
session's, and it fits one read, so you write no script around it. It returns within 5 minutes
with every record addressed to the coordinator, of any kind and holds first, and at once for anything from `owner-relay`, an
incident, a mechanism, a fix-live or an urgent. `--watch` closes the batch with each PR's queue
state and its LANDED and OVERRIDE-WARNING records. A record `lr coordinator mute add` keeps off
the batch is counted in a `muted` line that names the whole batch's file, and costs no wake. A
cut record names its read; a body past 4000 bytes prints
as a path for lane `chores` to read. Post every reply to a batch in one `cci post --lane root
--batch -`, then run it again. Never wait with a Monitor, `cci watch` or `sleep`.

Post that `cci me` line to the communicator as a `report` to `owner-relay`. A claim refused as
a second binder of `root` means another live window still holds the lane; report it to
`owner-relay` and stop.

A sub-drive's coordinator, whose brief names its parent drive, claims `root` on its own
child drive and reads `root@<parent>` wherever this skill says `owner-relay`.

You own and spawn the drive's desk runner, its launch runner and every standing desk,
each remote, as the owner ruled at 6:57 PM on Nov 2 2026: "the remote desk runner/lunch
runner, all of that stuff, which should be owned and spawned by the root and should be
remote as per our earlier decisions, should all be spawned by the root controller".
Start each desk once with `lr launch lane`, from a brief with its `Standing:` line, then run
`lr desks start` before any lane and again after any relaunch; it relaunches every standing
desk in the registry whose row is down. The launch runner is the one exception to remote for now: it starts
from your Orca terminal on the Mac, because it drives the Mac's Orca app.

Then load the `long-running` skill: every rule there that names the coordinator binds you, and
loading it after this one leaves the switch armed. Your role and inheritance are in its
`reference/coordinator-brief.md` and in the brief you were launched with.
