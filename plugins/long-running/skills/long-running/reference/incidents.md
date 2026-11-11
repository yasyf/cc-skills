# Handle an incident

An incident is the drive's in three cases only, and the coordinator handles it in the turn it arrives, before anything else:

- **He orders it:** his words call something an incident, ask for it urgently or with incident priority, or tell you to unbreak a live build.
- **He sends an alert or asks about one:** his link to a Datadog monitor, a Sentry issue, an `#outage` or `#alerts-*` report, or a page, which reaches the coordinator as an `owner` record.
- **The drive caused it:** a monitor goes bad on a target one of the drive's own releases or changes touched, during or after that release, or one of the drive's releases fails partway through its rollout. The coordinator holds the alert channels, each a `drive start --watch` named on its `Hold with slack_subscribe` line, so the drive sees its own releases go bad.

Every other alert in the channels the drive holds gets no line, order or job, to avoid wasting time on alerts nobody asked for; before starting an incident, run `answer_search` for his incident rulings and follow them.

The goal is to unblock users within minutes—by releasing the default branch when the first check shows its newest build works, else by rolling back—and then ship the proper root fix. Start the fix job at the alert in the same message as the diagnosis job, with no question to him, real-or-ours verdict, plan mode, reproduction, cause hunt, or mute in between, because diagnosis redirects the fix job rather than delaying it.

## One incident, one record

A failing service is one incident, however many alerts, threads and questions of his it draws, with one cc-notes investigation record and one job that owns it, so threads get one coordinated diagnosis:

- **The owner** is the first check, or the cause job when none runs; when the first check's build fails too, resume that same job as the cause job. In its first 10 minutes it appends each release, merge, flag flip and config change on the failing service since an hour before the start: `ccn investigation append <id> "<time> <change> <link>"`. It records each suspect with `ccn investigation finding add <id> "<change, PR>: <how it could fail this way>"`, settles it by `finding confirm` or `finding clear` with `--why "<what was observed, its link>"`, and sets `ccn investigation root-cause <id> "<change, PR, what in it fails>"` once a test machine reproduces the failure and that change explains it.
- **Every other job** (a reader, the rollback or the fix) appends what it observed and names no cause. The coordinator reads `ccn investigation show <id>` before sending any line about the cause.
- **A later alert or question** about an open incident's service joins it: run `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts> --thread <ts> --joins <o>` (`drive order add` refuses a non-open or non-incident `--joins`), give it the parts his words ask for with `drive order set <new> --parts "<part>"`, and carry it on the owner with `drive job set <owner> --carries <new> --part "<part>"`.

## In the same turn

1. `answer_add` his words verbatim with labels `drive:<name>` and `incident`, which approves the incident's own release and rollback without a card.
2. Check the open incident orders on the dashboard's top strip: if one covers the same failing service, join it by [One incident, one record](#one-incident-one-record) and stop here. Otherwise run `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts> --thread <ts> --incident --parts fix "first check" --due +5m`, or `--parts rollback fix cause` when no release or new build preceded the break, to put the order on the top strip and create a codex job per part (`drive order add` and `drive order set <o> --incident` refuse an incident order without a `fix` part). Open the record with `ccn investigation open "<service> on <cluster>: <symptom>, since <time>" --body "<the alert or his words, with its link>" --label drive:<name> --label incident`, then run `drive order set <o> --note "record <id>"`.
3. In the next message, start every job the order printed, plus the freeze when a release broke the service by [Freeze the broken service](#freeze-the-broken-service). Start each job as a `codex:codex-wrapper` running `codex-ask --incident` whose brief carries the step 1 answer id, the record id, the full `Incident:` line, and the `Drive:` line naming the job so the watch sees its agent recorded:
   - **fix**, with apply authority. Start at the code path the alert or his words name, or at the failing release step, log and code. Build the whole root fix and its required tests in one PR—no split-offs since the rollback stops the harm, and never a revert or a stopgap (pin, hard-coded flag, header, prefix rule, cap, retry, timeout raise, timer, wait, cache or special case)—following repository performance and root-cause rules (in the monorepo, AGENTS.md's Performance section). If the cause is a design, put questions on cards by [Design first](design.md) while building the recommended option by [His card on an incident](#his-card-on-an-incident). Forward each finding from the first check or cause job via `SendMessage` in the turn it arrives to redirect the fix without holding it back.
   - **first check**, the diagnosis job beside `fix` when a release or new build preceded the break; start no cause hunt or rollback before it returns. Within 10 minutes it checks the live build's commit (`tools/ci release live` in the monorepo) and how far the default branch is ahead on the failing path, installs the newest default-branch build on a test machine (a test Mac in the monorepo), and replays the failing action against state the bad build refused, so a solved bug is fixed by releasing rather than hunting.
   - **rollback** and **cause**, started beside `fix` when no release or new build preceded the break:
     - **rollback**, with apply authority, rolls back to the newest build that provably lacks the failing change (`git merge-base --is-ancestor <change> <target>` exits 1, or while the change is unknown, replaying the failing action on a test machine succeeds) and provably opens state written by the bad builds since. If a build passes both within the clock, it starts the rollback (via the `release` skill in the monorepo, or by installing the release for a tool on this Mac); otherwise it returns the evidence that only the root fix can unblock users.
     - **cause**, the diagnosis job that owns the record and sends `fix` each confirmed suspect.
   - If a first check or cause job needs Sentry, codex lacks a Sentry sign-in, so run `drive job add reader --order <o> --goal "Sentry read: <what>" --part "<its part>"` and start a `long-running:reader` on `model` `opus` with `run_in_background: true` in the same message to read Sentry, append to the record, and send the result to codex via `SendMessage`.
4. Send a `report` to the communicator for his first line: the fix job and where it started, the diagnosis job beside it, and any freeze.

## Freeze the broken service

When a release breaks a service, freeze that service's other releases in the alert turn (step 3) and never in a later turn, because freezing after the fix job starts can only slow the fix. Run `drive job add builder --order <o> --part fix --goal "freeze <service> until the fix merges"` and start a `long-running:builder` on Opus fast mode to run `tools/ci release freeze <component> --reason "<the incident>: <what fails>, held until its fix merges" --until <RFC 3339 time 12 hours out>` on the bare component and record the returned freeze id in the order's note. This is his standing request, which the monorepo `release` skill requires whenever a release breaks a service.

- Because only the session that set a freeze (or the owner himself, or a rollback) can release through it, a coordinator-started builder sets it so any builder in that session can release the fix through it.
- The job that merges the fix lifts the freeze with `tools/ci release unfreeze <id>` in that turn before starting the build, and lifts it immediately if it still refuses a fix release.

## His card on an incident

When a question card blocks an incident job, post in the incident's thread linking the card, stating what it blocks and naming the recommended option, then record `--card <link>` or `--waits <card>` with `--told <link>`; `drive job set` refuses without `--told`. In the same turn, resume the job from its `ASK` with "build the recommended option meanwhile" and `drive job set <j> --state running` so it never waits on the card.

## When the first check returns

- **The default branch's build works:** send a `report` to the communicator with what the test machine showed; stop the fix job with `SendMessage` and `drive job set <j> --state stopped --said "the default branch's release is the fix"`; and run `drive job add builder --order <o> --part fix --goal "release the default branch"` and start a `long-running:builder` to release the default branch's head by [Ship it](#ship-it), with no hotfix, rollback or cause hunt.
- **It fails too:** run `drive order set <o> --parts fix "first check" rollback cause`, resume the first check as the cause job with `drive job set <j> --part cause`, and run `drive job add codex --order <o> --part rollback --goal "rollback: <incident>"` to start a step 3 rollback job while the fix job continues.

## Clocks

Set each clock with `drive order set <o> --due`—counting the first check from his order or alert, and the rollback from the first check's verdict or the order—and check it at `DUE` by reading the job's transcript rather than waiting for its message.

| Clock | Due | Missed |
|---|---|---|
| First check: the live build's commit, and the default branch's newest build tried on a test machine | 10 minutes | A second job on the first check |
| Rollback started on the proven target, or the evidence that no build can serve | 10 minutes | A second job on the rollback |
| Every later step, until live | at most 10 minutes out | A second job on that step |
| His next line, until users are unblocked | 5 minutes after his last | A line of what is running and when the next lands |

When a clock is missed, start an Opus 5.5 fast-mode `long-running:builder` (never fable or astra; the `agent_brief` hook denies non-designer agents on a Fable model) on the same brief writing to the same record so the first to deliver wins, and send him a line with what is late and the new time.

## Ship it

- **Claude ships codex's edit:** in the turn the fix job has its change, start a `long-running:builder` in that worktree to ship, land and prove it live.
- **Merge on green and approved:** in the turn the watch reports `checks passed` or `approved`, resume the job with "land it and start its build now". In the monorepo, skip the merge queue with `drive pr set <url> --exempt "incident fix"` and `gh pr merge <n> -R Forge-AI/monorepo --squash` (never `--admin`), and accept non-blocking bot findings with `submit-pr`'s `pr-reviews accept <slug> --why "incident: <why the fix is right>"`.
- **Start the build immediately:** lift the freeze, start the build at the fix commit, and report its link with no dry run, comparison or question first; the release's approve gate is the only check, and any reads happen while it builds.
- **State the bug before releasing:** before telling a rollback or default-branch release job to start, send a `report` quoting the bug (and any verdict's change and PR) from the record; a fix release already has this from its PR-open line.
- **Use the emergency cut:** ship a sand or sand-next incident fix alone rather than through the normal platform release:
  - Merged fix or proven default-branch head: `tools/ci release start sand --only sand --emergency`.
  - Fix cherry-picked onto the live commit on a pushed branch (only after the first check shows the default branch fails too, since Platy refuses commits off `dev`): `tools/ci release start --manual sand-next --env core-usw2-auto --commit <sha> --emergency`.
  - Every other form is in the monorepo `release` skill.
- **Approve the gate from the order:** answer the incident release's or rollback's gate (or any other release he ordered, per [landing.md](landing.md)) with `--order <the answer id>`, or `tools/ci release approve <id> --via <agent>`.
- **His approval is required for:**
  - **A revert** (taking a change out in whole or part, the monorepo `release` skill's reserved revert): he fixes forward, so a revert lands only on his tap on its card stating the bug, PR and evidence from the record.
  - **Touching his build or release:** never cancel, retry, reorder or approve a build or release he started, or start another while it runs; report its steps if it carries the fix. To stop one of ours, send a `report` to the communicator first.
  - **Break-glass deletes or replaces:** break-glass applies are authorized only at 0 deletes and 0 replaces; report both counts.
  - **Any claim, fence or hold** beyond the broken service's freeze in [Freeze the broken service](#freeze-the-broken-service).
- **Data repairs:** run any repair of a team's data as a service account inside that team's cluster from a pipeline or in-cluster job, never from break-glass, admin `kubectl exec`, a user sign-in, or copied credentials, so passes never run from a user role.

## After users are unblocked

While the rollback unblocks users, the fix job finishes the root fix and its regression tests in the same PR (after [Design first](design.md) if the cause is a design). If releasing the default branch fixed the incident, record the commits past the live build as the verdict and start no hunt.

## What he hears

Send each update as a `report` to the communicator for his or the alert's thread, even while he is away: at least every 5 minutes until users are unblocked, then once per root-fix step, with each link inline once where named.

- Omit "not proven" or "unproven" paragraphs ([messages.md](messages.md)) and release side facts (other changes, counts, parts); jobs return unknowns to the coordinator, and he hears only results.
- Quote every word about the cause from a fresh read of the record, sending its verdict once without revision and never guessing from alert timing. Before a verdict, state there is none yet alongside what the fix job is changing or its open PR from its transcript; when he asks what the bug is, list each open suspect from the record with its PR and evidence.

The steps, in order:

1. **Running:** the fix job and where it started, the diagnosis/rollback jobs beside it, and any freeze.
2. **The fix is a release:** the test machine proved the default branch's build fixes it.
3. **Rollback started:** the target build and proof it lacks the failing change.
4. **Users unblocked:** whose runs succeed again on a real machine.
5. **Root fix PR open:** its linked title and the record's verdict it fixes.
6. **Merged.**
7. **Release started:** the root fix's or default branch's release.
8. **Live:** what is live and the real-machine proof that affected users' runs succeed (not just a quiet monitor).
