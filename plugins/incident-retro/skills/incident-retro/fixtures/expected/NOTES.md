# Working notes

## Import report

Imported from `gdoc-export.md` with the Docs API export (title `[2026-03-14] Checkout API returned 502s and the order queue backed up (#7)`, document `acme-checkout-502s-doc`, revision `acme-rev-1`), default zone `America/Los_Angeles`.

### Header

- Title: Checkout API returned 502s and the order queue backed up (from the H1, stripped `[2026-03-14]`, `(#7)`)
- Date: 2026-03-14 (from the title bracket; the `Date:` field read `Date`)
- Status: resolved (from `In Progress`, then `Resolved; follow-ups in progress`; the last wins)
- Authors: Jordan Rivera (from `mailto:` link texts in the `Authors:` field)
- Attendees: Jordan Rivera, Sam Okafor
- Incident: number 7, severity sev-2 (severity from a header link)
- Header note: Confirm the timezone of the Slack transcript before publishing.
- Sub-incidents: 2 from the `Summary (Multiple incidents)` list, ids I1..I2; `[N]` prefixes elsewhere became `(IN)` citations
- Monitor 2 under Detection registered with role `caught`; change it if the monitor missed the incident
- Zone hint `All times Pacific, 2026-03-14 (PDT, UTC-7).` agrees with `--tz America/Los_Angeles`
- Repo: acme/monorepo (every pull request link names it)
- Every `h` handle and `p` plain twin is empty: the Draft pass writes them.
- Every time without an explicit zone was read in `America/Los_Angeles`; confirm the zone before publishing.

### Headings

| # | heading | destination |
| --- | --- | --- |
| 1 | `### Summary (Multiple incidents)` | summary.text |
| 2 | `### (empty)` | skipped (empty heading) |
| 3 | `### Impact` | impact.text |
| 4 | `### Root Causes` | causes[] kind root |
| 5 | `#### Confirmed root cause` | causes[] kind root |
| 6 | `#### Contributing factors` | causes[] kind contributing |
| 7 | `### Trigger` | causes[] kind trigger |
| 8 | `### Resolution` | resolution.text |
| 9 | `### Detection` | detection.text and detection.monitors[] |
| 10 | `## Action Items (Linear tickets)` | actions[] |
| 11 | `## Lessons Learned` | lessons.* by bold lead-in |
| 12 | `### What went well` | lessons.well |
| 13 | `### What went wrong` | lessons.wrong |
| 14 | `### Where we got lucky` | lessons.lucky |
| 15 | `## Timeline` | timeline[] and windows[] |
| 16 | `## Supporting information` | evidence.* by host, leftovers to notes[] |
| 17 | `### Pool configuration` | notes[] |
| 18 | `## Retro meeting notes` | notes[] |

### Timeline

| source | parsed | zone | kind | actor |
| --- | --- | --- | --- | --- |
| `All times Pacific, 2026-03-14 (PDT, UTC-7).` | running date 2026-03-14 |  | day header |  |
| `10:08` (table line 96 row 1) | 2026-03-14T10:08:00-07:00 | assumed | deploy (`build 301`) |  |
| `10:12` (table line 96 row 2) | 2026-03-14T10:12:00-07:00 | assumed | hypothesis (fallback) |  |
| `10:14` (table line 96 row 3) | 2026-03-14T10:14:00-07:00 | assumed | report (`report`) | Sam Okafor |
| `10:15` (table line 96 row 4) | 2026-03-14T10:15:00-07:00 | assumed | alert (`monitor 2 alert`) |  |
| `10:20` (table line 96 row 5) | 2026-03-14T10:20:00-07:00 | assumed | hypothesis (`suspect`) | Jordan |
| `10:41` (table line 96 row 6) | 2026-03-14T10:41:00-07:00 | assumed | mitigation (`rollback`) |  |
| `10:53` (table line 96 row 7) | 2026-03-14T10:53:00-07:00 | assumed | resolution (`stop`) |  |
| `11:30` (table line 96 row 8) | 2026-03-14T11:30:00-07:00 | assumed | allclear (`all-clear`) |  |
| `12:48` (table line 96 row 9) | 2026-03-14T12:48:00-07:00 | assumed | hypothesis (fallback) |  |
| `1:10` (table line 96 row 10) | 2026-03-14T13:10:00-07:00, read as PM to keep order | assumed | deploy (`deploy`) |  |
| `2026-03-15` | running date 2026-03-15 |  | day header |  |
| `09:00` (table line 111 row 2) | 2026-03-15T09:00:00-07:00 | assumed | hypothesis (fallback) |  |
| `17:30Z` (list line 116) | 2026-03-15T10:30:00-07:00 | explicit UTC, shown in America/Los_Angeles | mitigation (`restart`) |  |
| `11:05` (list line 117) | 2026-03-15T11:05:00-07:00 | assumed | action (`posts`) | Sam |

### Windows

- W1: 2026-03-14T10:12:00-07:00 to 2026-03-14T10:53:00-07:00 (impact) from `Checkout was unavailable for every customer between 10:12 and 10:53.`
- duplicate of an existing window skipped (timeline (Timeline)): `Outage window: from 10:12 to 10:53.`

### Timestamps

- onset 2026-03-14T10:12:00-07:00 from W1 start
- detected 2026-03-14T10:14:00-07:00 from the first alert or report entry
- resolved 2026-03-14T10:53:00-07:00 from the first resolution entry
- allClear 2026-03-14T11:30:00-07:00 from the first all-clear entry
- engaged and mitigated are never inferred; set them from the timeline

### Actions

| id | item | state | owner | links |
| --- | --- | --- | --- | --- |
| AI1 | Alert on pool saturation | todo (default) | Jordan Rivera (`mailto:` link) | none |
| AI2 | Canary deploys for `api` | done (state cell `done ENG-1`) | none | https://linear.app/acme/issue/ENG-1/checkout-502s-sev-2 |
| AI3 | Restart the queue consumer automatically after a 502 burst | done (struck through) | Sam Okafor (trailing `(Name)`) | none |
| AI4 | Raise the pool size ceiling in config (priority `[p1]` dropped) | todo (unchecked) | Jordan (trailing `[Name]`) | https://linear.app/acme/issue/ENG-2/raise-pool-ceiling |
| AI5 | Add a runbook for queue drain | todo (unchecked) | Sam (owner cell) | none |
| AI6 | Review the on-call rotation | done (`✅` in the text) | none | none |
| AI7 | Pool metrics dashboard | todo (unchecked) | none | https://linear.app/acme/issue/ENG-3/pool-metrics-dashboard |

### Links

| url | class | where | note |
| --- | --- | --- | --- |
| <https://linear.app/acme/issue/ENG-1/checkout-502s-sev-2> | linear | header | label `Linear Ticket` |
| <https://github.com/acme/monorepo/pull/12> | prs | causes (Trigger) | role cause |
| <https://app.graphite.com/github/pr/acme/monorepo/13?panel=timeline> | prs | resolution | role fix, rewritten from Graphite |
| <https://buildkite.com/acme/release/builds/301> | builds | resolution |  |
| <https://app.datadoghq.com/monitors/2> | monitors | detection |  |
| <https://acme.slack.com/archives/C01ACME0001/p1773480900000000> | slack | detection |  |
| <https://linear.app/acme/issue/ENG-1/checkout-502s-sev-2> | linear | actions | seen again, label `ENG-1` |
| <https://linear.app/acme/issue/ENG-2/raise-pool-ceiling> | linear | actions | label `ENG-2` |
| <https://linear.app/acme/issue/ENG-3/pool-metrics-dashboard> | linear | actions |  |
| <https://app.datadoghq.com/notebook/1> | notebooks | evidence (Supporting information) |  |
| <https://app.datadoghq.com/monitors/2> | monitors | evidence (Supporting information) | seen again |
| <https://github.com/acme/monorepo/pull/12> | prs | evidence (Supporting information) | role cause, seen again |
| <https://app.datadoghq.com/dashboard/abc-123> | docs | evidence (Supporting information) | label `Dashboard` |
| <https://status.acme.example/incidents/7> | docs | evidence (Supporting information) | label `Status page` |
| <https://github.com/acme/monorepo/pull/12> | prs | timeline (Timeline) | role cause kept (also seen as followup) |
| <https://acme.slack.com/archives/C01ACME0001/p1773480900000000> | slack | timeline (Timeline) | seen again |
| <https://app.datadoghq.com/monitors/2> | monitors | timeline (Timeline) | seen again |
| <https://app.datadoghq.com/notebook/1> | notebooks | timeline (Timeline) | seen again |
| <https://acme.sentry.io/issues/100/> | sentry | timeline (Timeline) |  |
| <https://github.com/acme/monorepo/pull/13> | prs | timeline (Timeline) | role fix kept (also seen as followup) |

### Images

- `evidence/images/image-2.png` from `[image2]`, alt from the section heading: Pool configuration (notes (Pool configuration))
- `evidence/images/image-1.png` from `[image1]`, alt from the surrounding text: Sam posts the drain graph (timeline (Timeline))

### Notes

- `Supporting information`: text under a supporting-information heading that is not a link
- `Pool configuration`: heading outside the mapping
- `Retro meeting notes`: heading outside the mapping

### Unplaced

Blocks the converter could not place, verbatim:

**Action items** (not a table, checklist or list):

> General nice to have: a staging environment.

**Timeline** (no leading time):

> During mitigation — the on-call rotation was reviewed.

### Finish by hand

1. Write an `h` handle (2 to 5 words) on every cause, action, window and sub-incident.
2. Write a `p` plain twin for summary, impact, every cause, resolution and detection.
3. Confirm the timezone of every converted time above, and the inferred timestamps.
4. Retitle every cause whose `t` is its first sentence, and check every kind and actor guess.
5. Replace customer names with deployment codenames in prose, and fill `impact.teams` and `meta.teams`.
6. Run `retro.py evidence fetch` for the notebooks and monitors, and `evidence slack new` for each Slack permalink.
7. Run `retro.py check` and clear what it reports.
