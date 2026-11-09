# Handle an incident

An active alert is handled in the turn it arrives, before anything else: a Datadog monitor, a Sentry
alert, an `#outage` or `#alerts-*` report, a page, or his link to one. Alerts arrive as `slack.message`
events from the alert channels the drive holds, each a `drive start --watch` and named on the `Hold
with slack_watch` line of `drive status`, or as his message in his DM. A drive holding only his DM
reads only the alerts he forwards. Nothing comes between the alert and its jobs: no question to him,
no real-or-ours verdict, no plan mode, no mute as a resolution.

## In the same turn

1. `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts>` of the message.
2. In one message, start two `codex:codex-wrapper` jobs, each running `codex-ask --incident`:
   - **fix**, with apply authority, starting at the code path the alert names;
   - **diagnosis**, which finds the mechanism and feeds it to the fix job; it gates nothing.
   Record each with `drive job add codex --order <o> --goal "<fix|diagnosis>: <alert>" --agent <id>`.
3. Send him the first of his three lines.

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

Three lines, nothing between them, each in the alert's thread with the alert's Slack link and its
Sentry or Datadog link, bare so they unfurl: **spawned** (the alert and that the fix is running),
**mechanism** (what broke, in plain words), **fix live** (what is live and the proof). Healthy means the
affected customers' runs succeed, not that a monitor went quiet; the last line says whose runs did.
