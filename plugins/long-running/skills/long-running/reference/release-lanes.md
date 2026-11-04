# Release lanes in a drive

The release tooling records nothing about drives. Its freezes name only the setter, its watch posts no cci record when an apply starts, and a start claims nothing on cci. The relay couples the drive to its releases from the records its lanes post. In `runner.json`, `landing.release_owner` names the release owner (`reference/landing.md` D20) and `landing.release_cli` names the release command line.

## Contents

- [Choose the release path](#choose-the-release-path)
- [Start a release and post it](#start-a-release-and-post-it)
- [Run an owner-ordered release](#run-an-owner-ordered-release)
- [Leave out what an open owner card gates](#leave-out-what-an-open-owner-card-gates)
- [Hand back what you cannot move](#hand-back-what-you-cannot-move)
- [Hear every page while a release runs](#hear-every-page-while-a-release-runs)
- [Own the rollback freeze](#own-the-rollback-freeze)
- [Mute and tear down a cluster](#mute-and-tear-down-a-cluster)
- [State the release and live time of a status he keeps asking](#state-the-release-and-live-time-of-a-status-he-keeps-asking)
- [Check release order and GO rulings](#check-release-order-and-go-rulings)

## Choose the release path

This is R11 in `SKILL.md`.

- Hands off deploys: nobody starts, approves, watches, or re-cuts a release unless the owner's word covers it. His order to release is that word for every approval step the plan shows. Without it the release stays a dry run, and its delete, replace and migration counts go to the coordinator.
- The one exception is the release operator's release. It has a standing authorization with no card and no ask: a lane runs it whenever the drive needs one, for example to learn a target's current state, and reports the start, the build link and the result to the coordinator.
- Only the release lane starts a release. A release order goes to the release lane, which is raised or relaunched if silent; no lane orders another lane to start one. A `LANDED-NOT-LIVE` line carries no authority to cut a target: grep the target on the release lane first.
- A regular release is the default. An emergency release (`--emergency`, a hotfix) runs only when the regular path is too slow for a live customer impact, and never twice for one fix.
- An incident fix rides the next regular release when one can cut within its urgency. It keeps the zero-delete, zero-replace bound unless the owner ordered the release.
- No lane cancels a release once its apply has started. A hold goes before the start or not at all.
- A release's apply stays under five minutes, watches excluded. A longer apply is a defect fixed at its cause.
- Land known fixes before a cut is made, so the cut does not fail on the next open bug.

## Start a release and post it

- Fast-forward the lane's checkout to the trunk head before every cut: `tools/ci` builds from the checkout, and a stale one makes the cut refuse.
- A drive release starts without `--manual`, so Platy posts its card at the top level of #releases. That card is what the owner sees of a release. `--manual` posts nothing to Slack and leaves no card to link, so it is only for the refusal recovery the release skill names. A manual release may have no approve gate, so read the build's approve block before any claim that it is parked at approval.

**Post a `release` record right after a start prints its build, and put the Platy card on it and on every record that follows.**

```sh
cci post --drive <drive> --lane <lane> --kind release --target <component> \
  --build <build url> --attr card=<Platy permalink> --to root \
  "<what users get, in plain words>. CUT releases/<id> build <number>"
```

- The permalink is in the build's `release-slack-root` annotation, as `Posted in #releases`. The card's What's new section is the source of the plain words, and it appears once the plan is read, so a start record may lead with the link alone.
- A record leads with the link and what users get, then the counts. owner-relay relays these records, so what a record leads with is what he reads. A release number always has plain words beside it, and a PR-number list never stands in for them.
- `owner_posts_carry_cards.py` warns on a `release` record, and on any record to owner-relay about a release build, that carries no permalink. A release word to him with no card link, whichever lane posts it, is held once by `owner_cards_name_their_subject.py`: send it again unchanged and it goes through.

- The runner records `<lane>@<drive>` as the build's owner under `releases` in the drive registry and runs `cci claim --for <lane>@<drive> --build <build url> --rel started`. That needs `cc-inbox` 0.23.0 or newer. `cci who --build <build> --rel started` then names your session. A failed claim logs `RELEASE-CLAIM-FAILED` to the coordinator once.
- Only the session that started a release may cancel it, and no lane ever cancels a running apply. To stop a release another session started, find it with `cci who` and ask it:

```sh
cci post --drive <drive> --lane <lane> --kind ask --to peer:<its session prefix> --build <build> --text "<why it should stop>"
```

- With `landing.applying` set, the runner polls each tracked build every 15 seconds until it finishes. The first time a job whose step key matches `landing.applying.step` reads running for a component in `landing.applying.components`, the runner posts one `release` record keyed by the job id to the coordinator of every live drive, with the text `applying <project>/<env> <commit, 8 chars> (<release id>)`. Peer drives pause their watches on it. A lane never posts an `applying` record itself.

## Run an owner-ordered release

**The owner's order to release carries every approval the release asks for.** Nobody returns to him for a second word. The `owner_order_is_the_approval` hook refuses a coordinator question that asks him to approve a step of a release he ordered.

The coordinator:

- Posts his order with `--attr order=<answer id> --attr lane=<release lane>`.
- Expects a release record carrying that `order` within five minutes, or a refusal. Otherwise, the coordinator gets a `CUT-SILENT` blocker.
- Asks the owner once about a `blocker` the lane sends for a delete or replace the plan did not name.

The release lane, when the owner ordered the release (in his words, or a coordinator record naming it `RELEASE ORDER` or `owner-ordered`):

- Starts it with `tools/ci release start --order <answer id>`. If the checkout's `release start` refuses `--order`, start without it and approve the gate in the same pass. The attached start or watch passes the gate the moment it is reached.
- Approves by hand, in the same pass, a release already running when he orders it.
- Applies, counts and reports every delete, replace and migration the plan or the order names, with no second ask. The done line is `RELEASED <component> <version>: <n> deletes, <n> replaces, <n> migrations`, for example `RELEASED plat 12151: 3 deletes, 0 replaces approved under owner-ordered #<seq>`.
- Sends a `blocker` to the coordinator, with the plan line, for a delete or replace the plan did not name or on a target his order did not cover, and stops.
- Never cancels mid-apply. An apply that stops making progress is a `blocker` with the apply left running.

An incident release without his order approves its hold but still sends a delete or replace to him, and keeps the zero-delete, zero-replace bound.

### Five minutes at the gate

A release at its approval gate five minutes after an owner order is a blocker to the coordinator, never a wait. The `lr` lanes mover raises it, naming the release, the gate and the lane, once per release and gate.

- It reads the order from any drive record carrying `--attr release=<releases/id> --attr order=<answer id>`. The release lane's `release` record names both. The coordinator's order on a release already running also passes `--attr lane=<release lane>`.
- `--attr ordered_at=<RFC3339>` gives his order's time when the record comes later than his words. A gate reached after his order gets its five minutes from the gate.
- The same check raises `RELEASE-STOP` when an ordered release's apply refuses a delete or replace outside its approved plan. `tools/ci` records the stop on the release, and this watch posts it.
- A `--manual` start answers its gate through Buildkite. To re-attach, run `tools/ci release watch <release>`; `release watch --manual` passes no gate.

## Leave out what an open owner card gates

**A drive lane's release start never ships a stack an unanswered owner card gates.** Before `tools/ci release start` or `hotfix` runs, the `release_cards` hook reads `tools/ci release plan --json` for the same selection, dependencies included, and the unanswered cards from `lr board waits`.

- A planned component that a card's slug or title names refuses the start, naming the stack and the card. Start again with `--without <stack>`, or wait for his answer.
- A dry run, an incident release, and a colleague's release go unread.
- A plan or board that does not read lets the start through with a warning.
- A component that cannot ship never holds the others. The rest release at once, and the left-out stacks stay declined and named.

## Hand back what you cannot move

**A release lane asks the drive's coordinator, never the owner.**

| Situation | Release lane action |
|---|---|
| Owner presence is unclear before an off-hours start | Ask the coordinator first. |
| An apply stops making progress | Send the coordinator a `blocker` with the build URL and the last progress line, and leave the apply running. |
| The pipeline refuses and the release skill gives a hand path | Post the refusal verbatim as `--kind refuse --target <component>` with the build URL and what the hand path applies, then run the hand path (`reference/manual-release-brief.md`). |
| A step retries on a lost agent | Post one `defect` record per loss naming the step and the build. |
| A concurrent release goes red | Send the coordinator one line within two minutes of the failure showing, then the log. |

## Hear every page while a release runs

**The runner relays on-call pages during and 30 minutes after a release.** With `landing.pages` set, the orca runner watches each release build named by a lane's `release` record through `--build` or a Buildkite URL in the text. The watch runs from the record's time until 30 minutes after the last such build finishes.

- Every 15 seconds in that window it reads the newest top-level messages in `landing.pages.channels` and posts each new one to the coordinator once as an `incident` record with `--url <permalink>`.
- The coordinator treats each record as an active alert link and opens the incident in that turn with no owner round-trip (`reference/active-alert-brief.md`).
- Outside that window the runner reads no channel.
- After a flip or cutover, the Slack lane reads the users' bug channel from the flip onward for hours. Passing every drive check does not show that users are unaffected.

## Own the rollback freeze

**A lane never freezes a component or holds a release target to keep a change from going live.** A freeze stops every person's release of that component, and a `hold` record with `--target` reaches no one outside the drive. A merged change the drive does not want live is reverted or put behind a code gate on dev itself. Dev stays releasable: never offer a fence or a bus-only hold that blocks others' releases, and a deploy pause never holds merges.

The one freeze a lane owns is the `rollback-<build>` freeze its rollback writes. Post a `hold` record naming it:

```sh
cci post --drive <drive> --lane <lane> --kind hold --target <component> --to root \
  --text "rollback froze <component> as freeze <id>: <why>"
```

- To lift it, run `<release cli> unfreeze <id>` and close the hold record with `cci post --drive <drive> --lane <lane> --kind lift --resolves <hold seq> --target <component>`.
- While the hold record is open, the runner posts a lift ask to the lane once its `--until` passes, and to the coordinator once the lane has no live session.
- Anyone needing the freeze lifted posts an `ask` to the lane that ran the rollback.

## Mute and tear down a cluster

- **A cluster leaves the code before it leaves the cloud.** The teardown lane first lands and releases the change that removes the cluster from every surface that names it: tenant config, release targets, monitors and their mutes, the router. Only when that release is live does the destroy run. A destroy ordered before the removal lands breaks every release that still names the cluster.
- **A mute covers one cluster's own steps, never a whole release that names it.** When a planned change, such as a tenant teardown, makes a cluster's steps fail as expected, the lane watching releases mutes only that cluster's plan, prepull, apply and watch steps, by cluster and step. It never mutes every finding or release whose text names the cluster; that hides the other clusters' failures.
- A release whose failed steps include any other cluster's, or that the muted steps alone fail, still raises its failure as one finding naming the release, the clusters it blocked, and the muted steps that failed it.
- A mute ends when the planned change finishes, and the lane says so in one record.

## State the release and live time of a status he keeps asking

A row under **You asked more than once** in his update names its record. When the release carrying it is live, post:

`cci post --drive <drive> --lane <lane> --kind answer --re <seq> --attr release=<n> --attr live=<time> --text <what is live>`

The row leaves the lead only on those attrs.

## Check release order and GO rulings

- **Require `Release-Order:` when a reader depends on a writer.** A PR that reads data or a payload another component writes must carry `Release-Order: <writer> before <reader>`, since `Release-With:` only ships components together without ordering them. A PR that needs an order and carries only `Release-With:` is not ready. The landing desk checks this at `READY` and answers the lane's report with a `blocker` naming the missing `Release-Order:` line (`reference/landing.md`).
- **Cite the cc-notes answer on every ruling-backed `go`.** A `go` record acting on an owner ruling cites `--ccn <answer id>`, and the PR cites the same id in its body (`reference/landing.md`). The `owner_facing` hook blocks a `go` written beside an unanswered `AskUserQuestion` or a board posted that turn.
