# Release lanes in a drive

The release tooling records nothing about drives; its freezes name only the setter, its watch posts no cci record when an apply starts, and a start claims nothing on cci. The orca desk runner couples the drive to its releases from the records its lanes post. In `runner.json`, `landing.release_owner` names the release owner (`reference/landing.md` D20) and `landing.release_cli` names the release command line.

## Post each release you start

**Post a `release` record right after a start prints its build.**

```sh
cci post --drive <drive> --lane <lane> --kind release --target <component> \
  --build <build url> --to root "CUT releases/<id> build <number>"
```

The runner records `<lane>@<drive>` as the build's owner under `releases` in the drive registry and runs `cci claim --for <lane>@<drive> --build <build url> --rel started`, which requires `cc-inbox` 0.23.0 or newer, so `cci who --build <build> --rel started` names your session; a failed claim logs `RELEASE-CLAIM-FAILED` to the root once. Only the session that started a release may cancel it, and no lane ever cancels a running apply. To stop a release another session started, find it with `cci who` and ask it:

```sh
cci post --drive <drive> --lane <lane> --kind ask --to peer:<its session prefix> --build <build> --text "<why it should stop>"
```

With `landing.applying` set, the runner polls each tracked build every 15 seconds until it finishes. The first time a job whose step key matches `landing.applying.step` reads running for a component in `landing.applying.components`, the runner posts one `release` record keyed by the job id to the root of every live drive with the text `applying <project>/<env> <commit, 8 chars> (<release id>)`. Peer drives pause their watches on it; a lane never posts an `applying` record itself.

## Hear every page while a release runs

**The runner relays on-call pages during and 30 minutes after a release.** With `landing.pages` set, the orca runner watches each release build named by a lane's `release` record through `--build` or a Buildkite URL in the text. The watch runs from the record's time until 30 minutes after the last such build finishes. Every 15 seconds during that window, it reads the newest top-level messages in `landing.pages.channels` and posts each new one to the root once as an `incident` record with `--url <permalink>`. The root treats each record as an active alert link and opens the incident in that turn with no owner round-trip (`reference/active-alert-brief.md`). Outside that window, the runner reads no channel.

## Own every freeze you set

**Include `<lane>@<drive>` in every freeze reason and post a `hold` record.** Put `--until` at the end of the guarded step in RFC 3339 format and freeze again to extend it:

```sh
<release cli> freeze <component> --until <end of the guarded step> \
  --reason "<why>; owner <lane>@<drive>"
cci post --drive <drive> --lane <lane> --kind hold --target <component> --to root \
  --text "froze <component> until <RFC 3339 time> as freeze <id>: <why>"
```

A rollback writes a `rollback-<build>` freeze; the lane that ran the rollback owns it the same way and posts the same `hold` record. To lift a freeze, run `<release cli> unfreeze <id>` and close the hold record with `cci post --drive <drive> --lane <lane> --kind lift --resolves <hold seq> --target <component>`. While the hold record is open, the runner posts a lift ask to the lane once `--until` passes, and to the root once the lane has no live session. Anyone needing a freeze lifted posts an `ask` to the `<lane>@<drive>` named in its reason.

## Approve an ordered release yourself

**The owner's order to release carries every approval the release asks for.** When the owner ordered the release, in his words or a root record naming it `RELEASE ORDER` or `owner-ordered`, the release lane approves each approval step itself: deletes, replaces and migrations the plan or the order names are never a second ask. An incident release without his order approves its hold but still sends a delete or replace to him. The lane never cancels it mid-apply; an apply that stops making progress is a `blocker` with the apply left running. Its done line counts every delete and replace it approved, as in `RELEASED plat 12151: 3 deletes, 0 replaces approved under owner-ordered #<seq>`. Only a delete or replace that neither the plan nor the order names goes to the root as a `blocker`, and the root asks the owner once. The `owner_order_is_the_approval` hook blocks a root question that asks him to approve a step of a release he ordered.

## Mute one cluster's steps, never a release

**A mute covers one cluster's own steps, never a whole release that names it.** When a planned change, such as a tenant teardown, makes a cluster's steps fail as expected, the lane watching releases mutes only that cluster's plan, prepull, apply and watch steps, by cluster and step, never every finding or release whose text names the cluster. A release whose failed steps include any other cluster's, or that the muted steps alone fail, still raises its failure as one finding naming the release, the clusters it blocked, and the muted steps that failed it. A mute ends when the planned change finishes, and the lane says so in one record.

## Hand back what you cannot move

**A release lane asks the drive's root, never the owner.**

| Situation | Release lane action |
|---|---|
| Owner presence is unclear before an off-hours start | Ask the root first. |
| An apply stops making progress | Send the root a `blocker` with the build URL and the last progress line, and leave the apply running. |
| The pipeline refuses and the release skill gives a hand path | Post the refusal verbatim as `--kind refuse --target <component>` with the build URL and what the hand path applies, then run the hand path (`reference/manual-release-brief.md`). |
| A step retries on a lost agent | Post one `defect` record per loss naming the step and the build. |

## An owner's release order is every approval

When the owner orders a release, the lane that runs it approves every step the release needs: the deletes, replaces and migrations its plan names are applied, counted and reported in the lane's done line (`RELEASED <component> <version>: <n> deletes, <n> replaces, <n> migrations`). Nobody returns to him for a second word, and the root's `owner_order_is_the_approval` hook refuses an ask that does. A delete or replace the plan did not name, or one on a target his order did not cover, stops the lane and goes to root as a `blocker` with the plan line. An incident release keeps the zero-delete, zero-replace bound unless he ordered the release itself. No lane cancels a release once its apply has started; a hold goes before the start or not at all.

## A regular release is the default

An emergency release (`--emergency`, a hotfix) runs only when the regular path is too slow for a live customer impact, and never twice for one fix: on Nov 2 two emergency releases carried one PR minutes before a full release of everything was ordered. A release that sits at its approval gate five minutes after an owner order is a blocker to root, never a wait (owner, 7:04 PM).

## Tear a cluster down in order

A cluster leaves the code before it leaves the cloud. The teardown lane first lands and releases the change that removes the cluster from every surface that names it: tenant config, release targets, monitors and their mutes, the router. Only when that release is live does the destroy run. A destroy ordered before the removal lands breaks every release that still names the cluster, and a mute written to hide those failures hides the other clusters' failures with them. A mute covers one cluster's own steps and never a whole release that names it.

## Check release order and GO rulings

- Require `Release-Order:` when a reader depends on a writer. A PR that reads data or a payload another component writes must carry `Release-Order: <writer> before <reader>`, since `Release-With:` only ships components together without ordering them. A PR that needs an order and carries only `Release-With:` is not ready, and the landing desk checks this at `READY` and answers the lane's report with a `blocker` naming the missing `Release-Order:` line (`reference/landing.md`).
- Cite the cc-notes answer on every ruling-backed `go`. A `go` record acting on an owner ruling cites `--ccn <answer id>`, and the PR cites the same id in its body (`reference/landing.md`). The `owner_facing` hook blocks a `go` written beside an unanswered `AskUserQuestion` or a board posted that turn.
