from __future__ import annotations

NOT_AN_INCIDENT = """# flappy-monitors-2 — fix every flapping monitor

ccx: tooling-lane=flappy-monitors-2 role=fix
Not an incident. No Slack writes, no applies; PRs land now and apply with the next observability deploy.

Inputs: the census of every monitor with ≥2 transitions in 72 h. Start on the known set: 100000001 replication lane
failing (a PR moving it to last_10m is landing from the lane-failing ship lane; build on it), 100000002 runs crashed by
executor destroy, 100000003 namespace ingress (a real outage; keep its sensitivity). Evidence: incidents/*/evidence.md.

Bar per monitor: fires only when a human must act now. Never loosen a monitor whose flap was a real outage.

Fix: one observability stack, one PR per monitor, chained; report READY to the landing desk per PR."""

INCIDENT_ADJACENT = """ccx: tooling-lane=ignore-protect-preview role=fix

# ignore-protect-preview (opus xhigh)

orca-desk: launch ignore-protect-preview NOW. `--ignore-protect` in infra/engine.ts reaches `up`, but the deploy PLAN
step still fails in preview with "resource … cannot be deleted" on the protected params. Deliver the mechanism with
file:line cites and the minimal fix plus a unit test as one lone PR via submit-pr. Incident-adjacent: enqueue ahead of
parity. No applies."""

INCIDENT_TURN = """ccx: tooling-lane=lane-failing role=fix

# lane-failing-fix (incident xhigh)

## R810 — INCIDENT
Datadog 100000001 "replication lane failing" OK → ALERT. orca-desk: launch lane-failing-fix NOW.
Authority: Incident Turn — start at the monitor's query, then the handoff in flight at that minute.
If a lane is genuinely failing: mitigation per the monitor runbook as a PR/apply at 0d/0r pre-authorized with counts in
the runbook. Evidence lane: lane-failing-evidence. FENCE: the deploy desk pauses further pod rolls until the fix lane
reports mechanism."""
