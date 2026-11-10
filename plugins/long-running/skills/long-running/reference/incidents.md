# Handle an incident

An incident is the drive's in three cases only, and then the coordinator handles it in the turn it
arrives, before anything else:

- **He orders it:** his words call something an incident, ask for it urgently or with incident
  priority, or tell you to unbreak a live build.
- **He sends an alert or asks about one:** his link to a Datadog monitor, a Sentry issue, an
  `#outage` or `#alerts-*` report, or a page, which reaches the coordinator as an `owner` record.
- **The drive caused it:** a monitor goes bad on a target one of the drive's own releases or changes
  touched, during that release or after it.

Every other alert in the channels the drive holds is nobody's here: no line, no order, no job. He ruled
it ("stop wasting time with random non-issue incidents no one asked you to"), so before starting an
incident run `answer_search` for his incident rulings and follow them. The coordinator holds the
alert channels, each a `drive start --watch` named on its `Hold with slack_watch` line, so the drive
sees its own releases go bad.

The goal is that users stop being blocked within minutes, by a small fix or a rollback. Nothing waits
on finding the cause: no question to him, no real-or-ours verdict, no plan mode, no reproduction, no
mute as a resolution.

## In the same turn

1. `answer_add` his words verbatim, labels `drive:<name>` and `incident`: that answer approves the
   incident's own release and rollback, so no card goes to him for either.
2. `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts> --thread <ts>
   --parts "stop the harm" "rollback" "cause" --due +10m`.
3. In one message, start three `codex:codex-wrapper` jobs, each running `codex-ask --incident`, each
   brief carrying the answer id from step 1:
   - **fix**, with apply authority. Its first deliverable is the smallest change that stops users
     being blocked: degrade, disable, or revert the failing path, starting at the code path the
     alert or his words name. The real fix comes later.
   - **rollback**, which proves or disproves that the previous build lacks the failing path, and
     either starts the rollback or returns the evidence that it cannot work. In the monorepo the
     rollback is the `release` skill's; for a tool this Mac runs it installs the previous release.
   - **cause**, which reads Sentry, logs and code and sends what it finds to the other two. It gates
     neither: diagnosis redirects the fix, never precedes it.

   Record each with `drive job add codex --order <o> --goal "<fix|rollback|cause>: <incident>"
   --agent <id> --part "<its part>"`.
4. A `report` to the communicator for his first line: the jobs started.

## Clocks

Each clock counts from his order or the alert, is set with `drive order set <o> --due`, and is checked
at its `DUE` by reading the job's transcript, never by waiting for its message.

| Clock | Due | Missed |
|---|---|---|
| Rollback answer: started, or the evidence it cannot work | 10 minutes | A second job on the rollback |
| Fix PR open, the change that stops the harm | 15 minutes | A second job on the fix |
| Every later step, until live | at most 10 minutes out | A second job on that step |

A second job is an Opus 5.5 fast-mode `long-running:builder` on the same brief, started in the turn
the clock is missed, beside the first; the first to deliver wins. Never fable or astra. A missed clock
also sends him a line in that turn, before he asks: what is late and the new time.

## Ship it

- Codex edits and Claude ships: the turn the fix job has its change, a `long-running:builder` ships it
  from that worktree, lands it and proves it live.
- The fix PR carries the code change alone; its tests follow in a second PR once the fix is live.
- An approved and green incident PR lands, and its release starts, in the same turn the watch reports
  it: on its `checks passed` or `approved` event, resume its job with "land it and start its release
  now". In the monorepo the fix skips the merge queue: `drive pr set <url> --exempt "incident fix"`,
  then `gh pr merge <n> -R Forge-AI/monorepo --squash`. Never `--admin`. Accept a reviewer-bot
  finding with `submit-pr`'s `pr-reviews accept <slug> --why "incident: <why the fix is right>"`
  unless it says the fix is wrong.
- The incident order's words approve its own release's gate and its rollback, and only those: the job
  that starts the release answers the gate with `--order <the answer id>`, or `tools/ci release
  approve <id> --via <agent>` where it holds at approve. Every other release follows
  [landing.md](landing.md).
- Roll back only when the previous build provably lacks the failing path; the rollback job proves or
  disproves that inside its clock.
- Break-glass applies are authorized at 0 deletes and 0 replaces; report both counts.
- Claim, fence, hold, or freeze nothing unless he asks.

## After live

The cause, the regression test and any redesign come after the build is live: each is a job on the
order, started on the fix's `DONE`, and the cause in plain words goes into the order's next summary.

## What he hears

Five lines, nothing between them but a missed clock's line, each a `report` to the communicator, which
posts it in his thread or the alert's thread with the alert's Slack link and its Sentry or Datadog
link, bare so they unfurl. They go out while he is away too.

1. **Jobs started:** the fix, the rollback check and the cause hunt are running.
2. **PR open:** the fix's title, linked, and what it stops.
3. **Merged.**
4. **Release started:** the fix's release, or the rollback when it starts first.
5. **Live:** what is live and the proof. Healthy means the affected users' runs succeed, not that a
   monitor went quiet; the line says whose runs did.
