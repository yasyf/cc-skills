# Release cards

The `release.*` cards read a Platy release pipeline on Buildkite, the drive's census
reports, and its cci records. Every card binds `checkout`, `repo`, `state_dir`, and
`ledger` from `context.json`; set `census` to the glob of the census reports under the
state dir.

```yaml
- use: release.stacks
  id: stacks
  width: 3
  with: {census: 'done-board/iac-drift-*.md', trunk: origin/dev, release_code: go/ci/internal/release/}
```

## Builds

`release.builds`, `release.stacks`, and `release.tiles` share one build cache per
dashboard and pipeline, `builds.json` in the dashboard dir, refreshed from
`bk api` at most once a minute across every card.

- The first refresh backfills the pipeline's whole history, 100 builds a page, until a
  short page. A failed read resumes at the next page.
- After that, each refresh asks for builds in a live state: creating, scheduled,
  running, blocked, canceling, or failing. That set picks up retried and unblocked
  builds too.
- When a cached live build leaves that set, and at least every five minutes, it also
  asks for builds finished since the last such sweep.
- The file's mtime records the last fetch, so a restart resumes from it. A failed read
  keeps the cached builds and errors the card that read; a 429 backs that card off
  for five minutes.

A launcher build folds into the build it started and keeps its Slack link. Platy
release, hotfix, and rollback starts carry a Slack thread in `RELEASE_START`; CLI
starts do not. A CLI deploy credits every component, stack, and environment it names.

## Deployability

`release.stacks` reads the newest census report, `release/targets.yaml` in the
checkout, the build cache, and the newest commit touching `release_code` on `trunk`.
Each stack carries its last included Platy release, last Platy pass, last successful
CLI deploy, and its census state: `0/0`, `drift`, or `unplanned`. Deselection never
counts as a release.

| `deployable` | When |
|---|---|
| `blocked` | An open cci blocker, defect, or hold since the drive started names the stack in `refs.stacks` or its target in `refs.targets` |
| `unproven` | No blocker, and the target's last Platy release did not pass, no Platy release converged the stack, or its last converged release predates the newest pipeline change |
| `proven` | The target's last Platy release passed, the stack's last converged release contains the pipeline change, and nothing blocks it |

A stack-only record blocks only that stack. The newest matching record supplies
`blocked_by` and `reason`, its seq `blocked_seq`, and `refs.url` the `reason_url`.
A cite a curator `resolved:` record names no longer blocks. For an unproven stack,
`reason` names the failed attempt, the missing convergence, or the pipeline change's
SHA and subject, and `unproven_since` records that SHA when a prior pass exists.
`proven_at` is the converged release commit.

`doing`, `doing_url`, and `doing_lane` come from the newest go, opened, updated,
claim, ready, landed, released, or fix-live record after the blocker, from or naming
its lanes, or naming the target or stack. Matches use whole target names, and
components only in stack form: `data/plat` counts, bare `data` never does.

A target is `blocked` when any of its stacks is, `proven` only when all are, and
`unproven` otherwise.

## The other cards

| Card | Shows |
|---|---|
| `release.tiles` | Stacks at 0/0, releases today, the median passed release, PRs landed today against open, open incidents, lanes working |
| `release.census` | Stacks at 0/0 and the total over 48 hours, from cci state records carrying census fields |
| `release.landed-per-hour` | PRs whose landed record arrived in each of the last 24 Pacific hours |
| `release.incidents` | Incident threads from 72 hours of records, open first, grouped by slug and fix lane |
| `release.lines` | Go, opened, updated, claim, ready, landed, released, and fix-live records since the drive started |

An incident closes on fix-live, recovered, not-ours, or duplicate, or on a done whose
`--re` or `--resolves` points into it. A sighting stamped with a clock range such as
`4:09-4:19 PM` joins the incident named for its start. An unanswered report older
than a day reads quiet, not open.
