# The drive dashboard's Overview contract

The Overview is one picture the owner reads in five seconds. Every number on it comes
from one model and one counting function, so no two numbers on the page can disagree.
Builders of the app, the progress desk that feeds it, and the user-test lane that grades
it all work to this page.

## One model, one stream

The Overview reads `GET /api/model` and nothing else, and re-renders on every
`/api/stream` push. Everything local (PR states, board verdicts, workers, steps) streams
with no API key; only short names and Command-K answers call Cerebras.

| Field | Shape | Source |
|---|---|---|
| `drive.prs[]` | `{number, state, tree, part, step, worker}` | ledger rows, board verdicts, `go` records, `steps.json`, the config's parts |
| `steps[]` | `{id, label, title, state, blocker, next, prs[{number, state}], tone, workers[]}` | the progress desk's `steps.json` |
| `workers[]` | `{lane, step, prs[], at, boxes[12]}` | cci lanes and records, coordinators left out |
| `needs[]` | ordered `{kind: prs\|card\|ask, ...}` | the cc-present store and the story's asks |
| `parts[]` | `{key, name, home, becomes, scopes[]}` | `dashboard.json` |
| `now` | `{release, releases[], blocker\|null}` | `steps.json` |

The client has one counting function, `tally(list)`, returning the seven state counts
and the total for any list of `{state}`. The progress ring is `tally(drive.prs)`, a
part's percent is `tally` over its PRs, and a step's fill is `tally(steps[i].prs)`. The
server sends no precomputed counts. The denominator is every ledger PR open or landed,
including one registered minutes ago with no state yet.

## Seven states, one each

The server gives every PR exactly one state, first match wins; the client never derives
one.

| State | Means |
|---|---|
| `landed` | merged |
| `you` | waiting on his tap at its current head |
| `failing` | tests failing at its head |
| `held` | a hold is set |
| `queued` | approved at its current or a carried head, waiting to land |
| `doing` | a worker wrote about it in the last 20 minutes, or checks are running |
| `waiting` | open, unapproved, nothing running |

A PR is `you` when the board holds no approval at its current or carried head and no root
`go` names that head; either one makes it `queued`. Verdicts are read live from the
cc-present store on every rebuild, and Approve or Note on the dashboard writes the
board's own `triage.decided` event, so no board page opens.

A step's tone takes the first match over its PRs: done when all landed, then any `you`,
any `failing`, any `held`, moving for any `doing` or a live worker, queued when every PR
is landed or queued, otherwise waiting. A worker's
five-minute box takes the most severe record kind it posted in that window: red for
`blocker`, `defect`, `incident`; amber for `ask`, `owner`; green for `landed`, `go`,
`fix-live`; blue for any other; empty when silent.

## What the drive supplies

`dashboard.json` carries the drive's `parts`: each a key, a one-word name, a home (the
cloud, the Mac, or tools), the one line it becomes when done, and the PR title scopes
that belong to it (a title's scope is the text before its first colon). A PR in the old
tree sits in its home's "old, kept" band instead of a part.

The progress desk rewrites `steps.json` atomically on every pass:

- `steps[]` in plan order, each with an `id`, a label of at most two words, a plain
  title with no PR numbers, its `state`, `blocker` (null when nothing blocks it),
  `next`, every PR of the step landed ones included, and the lanes working on it.
- `now.release` and `now.releases[]`: the next release and every release with the parts
  it ships, in plain words.
- `now.blocker`: a drive-wide stop only, such as a red trunk or a dead merge queue,
  with its start time; null otherwise. A step's own blocker stays on the step.

It writes no counts; the dashboard counts.

A row or record the owner reads says he approved or tapped a PR only when the root's
board holds his approval at that head (`cc-present outcomes --no-doc`), and gives its
time. A root `go` alone reads "cleared to land by the coordinator at <time>", never "you
approved it".

## Words at rest

The Overview at rest shows at most 40 words. Numbers, percentages, times, PR numbers,
release ids, hashes, and glyphs do not count, and lane names never appear. A card opened
by hover or tap holds at most 25 words; the plan sheet is structured, not capped.
Everything else moves to a drill-in: a step card, the PR page with its embedded
cc-review, the plan sheet, or Command-K.
