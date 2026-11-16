# Staging slots as a first-in, first-out lease

Status: built. Testers and after-merge passes claim the slot themselves, and the `staging-slots` ask to root is retired. An overrunning lease extends once, and a priority or incident pass jumps the queue.

## The problem

One staging env, which the repository's staging provider names, serves every staging pass the drive runs. Today each pass asks for its window with an `ask` on topic `staging-slots` to `root@<drive>`. The top coordinator then does every step by hand:

1. Read the open asks.
2. Pack the testers' asks into cuts.
3. Start each cut.
4. Wait for the `FREE` lines.
5. Write the staging claim.
6. Wait out the 5-minute `DUE` line.
7. Close each ask.
8. Resume each job.

Each of those is a root turn. Only step 2, packing PRs that change different files into one cut, is judgment, and his picks 0c33b7b and ef0ae96 already rule it.

## The lease

A slot is a lease on one staging env, held by one job at a time, with an end. Waiting jobs queue first in, first out.

State, one row per env under the drive's `slots` collection:

```json
{
  "env": "staging",
  "holder": {"job": "j7033", "prs": ["acme/app#57094"], "until": "2026-11-15T22:40:00-08:00", "since": "2026-11-15T22:05:00-08:00", "claim": null},
  "queue": [
    {"job": "j7101", "prs": ["acme/app#57120"], "minutes": 25, "cases": "smoke, failover", "asked": "726411", "at": "2026-11-15T22:07:12-08:00"}
  ]
}
```

Verbs:

- `drive slot claim <env> --job <j> --minutes <n> [--pr <url>]... [--cases "<…>"]`
  - Free slot: the job takes it now, with `until` set `n` minutes plus the claim rule's 5 minutes ahead, and the verb prints `GO`.
  - Held slot: the job joins the queue, and the verb prints its place and the holder's end.
  - A second claim by the same job is refused. The job's `staging:` wait stays as it is today.
- `drive slot release <env> --job <j>`: the holder gives the slot back, or a queued job leaves the queue.
- `drive slot queue [<env>]`: prints the holder, its end, and the queue in order.

## What the watch does

Each pass, for each slot:

1. **Extend an overrunning lease once.** The holder's `until` passed while its job still runs: the lease extends by its `--minutes`, once, and the watch says one `SLOT` evidence line.
2. **End a lease.** The holder's job finished, stopped or failed, or its extended `until` passed. The watch releases the slot, says one `SLOT` evidence line, and wakes nobody.
3. **Hand the slot on.** When a slot is free and its queue is not empty, the head job takes it, and the watch resumes it with `GO` and its steps:
   - post the staging claim as a `POST` report to `communicator@<drive>`, at the root of the channel the repository's `staging-claim` rule names, with `until` set to the lease's end;
   - a tester whose PR has not merged starts its own cut of its branch onto the staging env with the repository's release start verb, and returns `WAITING staging:<target>/<env>@<head>`;
   - it runs its cases once staging serves its commit, which is past the claim's 5 minutes;
   - a reply in the claim's thread goes to root as today's `DUE … with replies` line, which does wake root.

## The top coordinator's cuts

Every staging start holds the slot, the top coordinator's included. `drive job add --release` whose start names one staging `--env` takes that env's slot for the job it adds, with a 60-minute lease that ends with the job. While another cut holds the slot it queues, and the watch starts its release with `drive job release` when its turn comes. Its release names its own branch, so it holds a cut alone.

## Packing

When a slot frees, the head of its queue takes it together with up to five later passes whose PRs change none of the cut's files and restart none of its stacks, as the repository's release provider reads them, since passes restarting one stack take turns, as the repository packs dangerous changes into one staging cut. The head's `GO` names the packed PRs and starts one cut carrying them all; each packed pass's `RIDE` line says to start no cut. One lease, its longest pass's minutes, covers the cut, and it frees once every pass in it finished or gave the slot back. Only the head may start a release on that env.

## Riders of another job's cut

A tester whose WAITING says another job starts its cut never takes a `GO`. Such a tester waits on another drive job beside staging serving a commit, waits on the top coordinator's `START` or `APPLIED`, or waits on staging serving a commit that heads none of its tested PRs. It keeps its queue place, and no lease ever starts on it. Once the cut of the job its waits name starts (that job kept a release build or waits on staging serving its cut), the watch adds it to that cut and sends it a `RIDE` line. A rider of the top coordinator's cut, or of a commit, gets its `RIDE` from the top coordinator's `START`. The watch never auto-queues a rider.

A hold from `drive slot hold` names one job, never an order. Another job of the held order may ride a cut, and an order hold would give it a `GO`.

## The queue

Claims queue first in, first out. A pass serving a priority order or an open incident goes ahead of every pass that does not, behind only the passes that jump too, as their builders jump the builder-slot line. A holder is never cut short.

## What stays with a model

Nothing in the sequence. The lease keeps one cut at a time, so root no longer packs testers' PRs into shared cuts or starts them. Replies to a claim still go to root.

## Worked example

| Time | Event | Watch action |
| --- | --- | --- |
| 22:05 | j7033 runs `drive slot claim staging --job j7033 --minutes 30` | Slot free: j7033 holds it until 22:40, posts its claim and starts its cut. |
| 22:07 | j7101 claims for 25 minutes | j7101 queued first behind j7033 (ends 22:40). |
| 22:09 | j7150, an incident's tester, claims for 10 minutes | j7150 queued first, ahead of j7101. |
| 22:40 | j7033 still runs | Lease extended once, to 23:10. |
| 22:52 | j7033 returns DONE | Slot released. j7150 takes it until 23:07 and is resumed with `GO`. |
| 23:04 | j7150 returns DONE | j7101 takes it until 23:34 and is resumed with `GO`. |

Root makes no turn in this sequence unless a reply comes in a claim's thread.
