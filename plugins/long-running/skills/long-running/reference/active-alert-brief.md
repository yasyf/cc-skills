# Active production alert lanes

Fill the angle brackets and write both Orca worker briefs to
`<spec dir>/<lane>.full.md` with the shared contract from
[orca-lane-brief.md](orca-lane-brief.md). Route each launch through the orca-desk
inbox. The desk's script passes a pointer as `--spec`; never paste the full brief
into `worker-start`.

## Root discipline

**Run the incident checklist before anything else.** The alert is P0 under R16.

- (a) Spawn the fix lane with apply authority in its brief. PR through the repo's
  submit skill; break-glass or hand apply pre-authorized at `0 deletes` / `0 replaces`,
  counts reported first, runbook-logged. Start at the code path the alert names.
- (b) Spawn the evidence lane for telemetry, logs, and the deploy timeline,
  feeding the fix lane by name. It gates nothing.
- (c) Fence the target from further applies and deploys in the same inbox line as
  both launches. The fix lane's apply under (a) is exempt.
- (d) Send one line to the owner at spawn with the alert, both lane names, and authority given.
  Send one line at mechanism and one at fix-live. Never inside a status wall.

Inside a drive, append one line to the orca-desk inbox.

```text
R<n> prompt orca-desk: launch <fix lane name> NOW sol xhigh; orca-desk: launch <evidence lane name> NOW sol xhigh; fence <target> from further applies and deploys except the fix lane
```

The desk runs `scripts/orca-launch.sh <name> sol xhigh <brief>` for each lane on
Orca codex, `gpt-6.1-sol`, fast tier, `xhigh`, in a `--no-parent` worktree; the script
passes `-c service_tier=fast` on the codex command line.
O15 and [orca-workers.md](orca-workers.md#incident-lanes-gpt-61-sol-on-the-fast-tier)
hold the launch recipe and readiness fallback. Outside a drive, use inline
background `codex:codex-wrapper` agents (`codex-ask -m sol`) on the same model, tier,
and effort. On a miss, use Claude Opus 5.5 (`claude-opus-5-5`); inside a drive,
route it through the same inbox with `opus xhigh`. Never fable or astra on the incident path.

**Never between the alert and (a).**

- A "real-or-not" or "ours-or-not" verdict
- A mechanism-depth mandate
- An `AskUserQuestion`
- Treating a mute as resolution

Diagnosis redirects the fix lane; it never precedes it. An owner's "if it is real,
fix it" means fix lane plus evidence lane, not a verdict gate.

Check each incident lane every 10 minutes. At 15 minutes without a mechanism, start
a second lane on a different model in parallel (Opus 5.5 after sol); keep the first
running.

*Prevents the 2026-10-01 release-v3 failures, when the fix lane started 5.6 min late
behind a verdict gate and the owner's sol routing was applied 6.4 min late.*

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
  named code path <named code path>; evidence lane <evidence lane name>

If you were launched unsupervised, report through the inbox/bus file; Orca carries no worker_done for you.

Do:
  1. Start at the named code path now, while evidence is still arriving.
     Never wait for a diagnosis verdict before starting the fix.
  2. Read <evidence lane name>'s findings as they arrive by bus or Orca messages.
     Redirect with the evidence, including to a monitor fix for a monitor defect.
     A muted monitor still gets fixed; use the mute window.
  3. Open the PR through <submit skill>; plan through <break-glass skill>.
  4. Report the plan counts, apply under the authority above, and log the apply
     in <runbook>. Verify the fix is live against the alert's metric.

Escalate: the root checks your status every 10 minutes. If you have no mechanism
  15 minutes after spawn, the root starts a second lane on a different model
  (Claude Opus 5.5, claude-opus-5-5, xhigh) in parallel; keep working.
  If your fix misses, report the miss; the root routes the Opus 5.5 fallback lane
  through the orca-desk inbox with `orca-desk: launch <name> NOW opus xhigh`.
  Never fable or astra.

Do NOT touch: unrelated targets, files, branches, or another lane's worktree.
Worktree: <absolute path, exclusive to this lane>.
Finish: report "fix live" to <root agent name> with PR, head, apply counts, and
  runbook entry. Use the inbox/bus file when unsupervised, otherwise the preamble's
  Orca worker_done command.
  Never report live from a mute.
```

## Evidence lane brief

```text
You are <evidence lane name>, reading telemetry, logs, and the deploy timeline for the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Authority: read-only evidence. Feed <fix lane name> and <root agent name>.
  You gate nothing. Never ask the fix lane to wait for diagnosis.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; fix lane <fix lane name>

If you were launched unsupervised, report through the inbox/bus file; Orca carries no worker_done for you.

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
  gaps; report the same to <root agent name>. Use the inbox/bus file when unsupervised,
  otherwise the preamble's Orca send and worker_done commands. You gate nothing.
```
