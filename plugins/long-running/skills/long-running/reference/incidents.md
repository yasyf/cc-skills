# Handle an incident

An alert is the drive's in two cases only, and then the coordinator handles it in the turn it
arrives, before anything else:

- **He sends it or asks about it:** his message, or his link to a Datadog monitor, a Sentry issue, an
  `#outage` or `#alerts-*` report, or a page, which reaches the coordinator as an `owner` record.
- **The drive caused it:** a monitor goes bad on a target one of the drive's own releases or changes
  touched, during that release or after it.

Every other alert in the channels the drive holds is nobody's here: no line, no order, no job. He ruled
it ("stop wasting time with random non-issue incidents no one asked you to"), so before starting an
incident run `answer_search` for his incident rulings and follow them. The coordinator holds the
alert channels, each a `drive start --watch` named on its `Hold with slack_watch` line, so the drive
sees its own releases go bad. Once an alert is the drive's, nothing comes between it and its jobs: no
question to him, no real-or-ours verdict, no plan mode, no mute as a resolution.

## In the same turn

1. `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts>` of the message.
2. In one message, start two `codex:codex-wrapper` jobs, each running `codex-ask --incident`:
   - **fix**, with apply authority, starting at the code path the alert names;
   - **diagnosis**, which finds the mechanism and feeds it to the fix job; it gates nothing.
   Record each with `drive job add codex --order <o> --goal "<fix|diagnosis>: <alert>" --agent <id>`.
3. Send the communicator a `report` for the first of his three lines.

## While it runs

- Read both transcripts every 10 minutes. With no mechanism 15 minutes after spawn, start an Opus 5.5
  fast-mode `long-running:builder` on the same brief beside them; the first working fix wins. Never
  fable or astra.
- Diagnosis redirects the fix job through `SendMessage`; it never holds it back.
- Codex edits and Claude ships: once the fix job has its change, a builder job ships it from that
  worktree, lands it and proves it live.
- Fix forward. Roll back only when the previous build provably lacks the failing path.
- The fix PR carries the code change alone; its tests follow in a second PR once the fix is live.
- In the monorepo the fix skips the merge queue: `drive pr set <url> --exempt "incident fix"`, then
  `gh pr merge <n> -R Forge-AI/monorepo --squash` the moment it is approved and its required checks
  pass. Never `--admin`. Accept a reviewer-bot finding with `submit-pr`'s `pr-reviews accept <slug>
  --why "incident: <why the fix is right>"` unless it says the fix is wrong.
- Break-glass applies are authorized at 0 deletes and 0 replaces; report both counts.
- It ships in the next regular release, not an emergency one, unless that is too slow for the
  customers it hits.
- Claim, fence, hold, or freeze nothing unless he asks.

## What he hears

Three lines, nothing between them, each a `report` to the communicator, which posts it in the
alert's thread with the alert's Slack link and its Sentry or Datadog link, bare so they unfurl: **spawned** (the alert and that the fix is running),
**mechanism** (what broke, in plain words), **fix live** (what is live and the proof). Healthy means the
affected customers' runs succeed, not that a monitor went quiet; the last line says whose runs did.
