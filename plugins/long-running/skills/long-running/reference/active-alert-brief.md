# Active production alert lanes

Fill the angle brackets and paste both briefs in the turn the alert arrives.

## Root discipline

**Spawn both lanes in the same turn.** The alert is P0 under R16. Spawn the fix lane
as `long-running:lane-ship`, model fable for concurrency or data paths and opus
otherwise, beside a telemetry/diagnosis lane. Never put a "real or not" or
"ours or not ours" gate before the fix lane. Diagnosis redirects it, including to
a monitor fix.

The monitor's target fence exempts the fix lane's apply under R16. A muted monitor
still gets fixed. Report to the owner at spawn and fix-live; never end a turn on an
active alert with only a question or a wait.

*Prevents the roughly 25-minute delay on release-v3, 2026-10-01, when the active
`#alerts-api` `SandSQL handoff park timeout` on plat waited for verdict, diagnosis,
and owner round-trips before a fix lane started.*

## Fix lane brief

```text
You are <fix lane name>, fixing the active production alert.
Authority: implement the fix and open its PR through <submit skill>.
  While the alert is active, apply the fix to production through
  <break-glass skill> without asking when the plan shows 0 deletes and 0 replaces.
  Report plan counts to <root agent name> before applying; log the apply in <runbook>.
  Any delete or replace stops for the owner.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; diagnosis lane <diagnosis lane name>

Do:
  1. Start at the named code path now, while evidence is still arriving.
     Never wait for a diagnosis verdict before starting the fix.
  2. Read <diagnosis lane name>'s findings as they arrive by bus or SendMessage.
     Redirect with the evidence, including to a monitor fix for a monitor defect.
     A muted monitor still gets fixed; use the mute window.
  3. Open the PR through <submit skill>; plan through <break-glass skill>.
  4. Report the plan counts, apply under the authority above, and log the apply
     in <runbook>. Verify the fix is live against the alert's metric.

Do NOT touch: unrelated targets, files, branches, or another lane's worktree.
Worktree: <absolute path, exclusive to this lane>.
Finish: SendMessage <root agent name> "fix live" with PR, head, apply counts,
  and runbook entry. Never report live from a mute.
```

## Diagnosis lane brief

```text
You are <diagnosis lane name>, reading telemetry for the active production alert.
Authority: read-only telemetry. Send findings to the fix lane and the root.
  Never gate the fix lane on a verdict or ask it to wait for diagnosis.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; fix lane <fix lane name>

Do:
  1. Send the first finding within about 10 minutes, with evidence and gaps.
  2. Date onset on an independent counter; do not infer it from alert time alone.
  3. Read the named metric and runbook; separate affected and healthy targets.
     Correlate onset with the drive's landings, deploys, and applies.
  4. Send findings as they land to <fix lane name> and <root agent name>
     by bus or SendMessage, with query, time window, and evidence pointers.
  5. If the alert is a monitor defect, send the evidence to redirect the fix lane
     to a monitor fix. A muted monitor is not resolved.

Do NOT touch: code, monitor configuration, production state, deploys, or applies.
Worktree: <absolute path, read-only>.
Finish: SendMessage <fix lane name> and <root agent name> the diagnosis,
  onset, evidence pointers, and remaining gaps. The verdict never gates the fix.
```
