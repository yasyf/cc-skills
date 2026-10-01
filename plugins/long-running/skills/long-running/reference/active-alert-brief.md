# Active production alert lanes

Fill the angle brackets and write both Orca worker briefs to
`<spec dir>/<lane>.full.md` with the shared contract from
[orca-lane-brief.md](orca-lane-brief.md). Pass each file to the launch script, which
passes a pointer as `--spec`; never paste the full brief into `worker-start`.

## Root discipline

**Spawn both lanes in the same turn.** The alert is P0 under R16. The fix lane and
telemetry/diagnosis lane are Orca codex workers on `gpt-6.1-sol`, `xhigh`, fast tier.
Sol is faster and wastes less time than Claude on incident work. Launch each with:

```sh
scripts/orca-launch.sh <lane> sol xhigh <brief>
```

Only in a session with no Orca run, use inline background `codex:codex-wrapper`
agents (`codex-ask -m sol`) on the same model, effort, and tier. When sol's fix
misses, the root starts a Claude Opus 5.5 (`claude-opus-5-5`) lane with
`scripts/orca-launch.sh <lane> opus xhigh <brief>`. Never use fable or astra on the
incident path; both are too slow. Fable stays astra's fallback elsewhere.

No `AskUserQuestion`, "real or not" or "ours or not ours" verdict, or owner
round-trip before the fix lane exists. Diagnosis redirects it, including to a
monitor fix. The root checks each incident lane's status every 10 minutes. If a
lane has no mechanism 15 minutes after spawn, the root starts a second lane on a
different model in parallel (Opus 5.5 after sol) and keeps the first running.

The monitor's target fence exempts the fix lane's apply under R16. A muted monitor
still gets fixed. Report to the owner at spawn and fix-live; never end a turn on an
active alert with only a question or a wait.

*Prevents the roughly 25-minute delay on release-v3, 2026-10-01, when the active
`#alerts-api` `SandSQL handoff park timeout` on plat waited for verdict, diagnosis,
and owner round-trips before a fix lane started.*

## Fix lane brief

```text
You are <fix lane name>, fixing the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
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
  2. Read <diagnosis lane name>'s findings as they arrive by bus or Orca messages.
     Redirect with the evidence, including to a monitor fix for a monitor defect.
     A muted monitor still gets fixed; use the mute window.
  3. Open the PR through <submit skill>; plan through <break-glass skill>.
  4. Report the plan counts, apply under the authority above, and log the apply
     in <runbook>. Verify the fix is live against the alert's metric.

Escalate: the root checks your status every 10 minutes. If you have no mechanism
  15 minutes after spawn, the root starts a second lane on a different model
  (Claude Opus 5.5, claude-opus-5-5, xhigh) in parallel; keep working.
  If your fix misses, report the miss; the root starts the Opus 5.5 fallback lane
  with scripts/orca-launch.sh <lane> opus xhigh <brief>. Never fable or astra.

Do NOT touch: unrelated targets, files, branches, or another lane's worktree.
Worktree: <absolute path, exclusive to this lane>.
Finish: use the preamble's Orca worker_done command to report "fix live" to
  <root agent name> with PR, head, apply counts, and runbook entry.
  Never report live from a mute.
```

## Diagnosis lane brief

```text
You are <diagnosis lane name>, reading telemetry for the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
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
     by bus or the preamble's Orca send command, with query, time window,
     and evidence pointers.
  5. If the alert is a monitor defect, send the evidence to redirect the fix lane
     to a monitor fix. A muted monitor is not resolved.

Escalate: the root checks your status every 10 minutes. If you have no mechanism
  15 minutes after spawn, the root starts a second lane on a different model
  (Claude Opus 5.5, claude-opus-5-5, xhigh) in parallel; keep working.
  Never fable or astra.

Do NOT touch: code, monitor configuration, production state, deploys, or applies.
Worktree: <absolute path, read-only>.
Finish: send <fix lane name> the diagnosis, onset, evidence pointers, and remaining
  gaps through the preamble's Orca send command; report the same to <root agent name>
  through its worker_done command. The verdict never gates the fix.
```
