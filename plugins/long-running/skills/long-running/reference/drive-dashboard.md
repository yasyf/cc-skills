# The drive dashboard's Overview contract

The Overview is one picture the owner reads in five seconds. Every number on it comes
from one model and one counting function, so no two numbers on the page can disagree.
Builders of the app, the progress desk that feeds it, and the user-test lane that grades
it all work to this page.

## One model, one stream

The Overview reads `GET /api/model` and nothing else, and re-renders on every
`/api/stream` push. Everything local (PR states, board verdicts, workers, steps) streams
with no API key; only plain-word record lines and Command-K answers call Cerebras.

| Field | Shape | Source |
|---|---|---|
| `drive.prs[]` | `{number, state, base, tree, part, step, worker}` | ledger rows, board verdicts, `go` records, `steps.json`, the config's parts |
| `steps[]` | `{id, label, title, state, blocker, next, prs[{number, state}], tone, workers[]}` | the progress desk's `steps.json` |
| `workers[]` | `{lane, step, prs[], at, boxes[12]}` | cci lanes and records, coordinators left out |
| `needs[]` | ordered `{kind: prs\|card\|ask, why, source{label, href}, ...}` | the cc-present store and the story's asks |
| `parts[]` | `{key, name, home, becomes, scopes[]}` | `dashboard.json` |
| `now` | `{release, releases[], goal, waiting[], blocker\|null}` | `steps.json` |

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
| `failing` | its own tests fail at its head; a red whose base PR also fails is `waiting`, with `base` naming that PR |
| `held` | a hold is set |
| `queued` | approved at its current or a carried head, waiting to land |
| `doing` | a worker wrote about it in the last 20 minutes, or checks are running |
| `waiting` | open, unapproved, nothing running |

Every need says why it is his, in one line, and links its source: a board card, the
board, or the bus record that asked (`GET /record/<seq>` serves its text). A `prs` need is
one merge group with any row waiting on him: the group's title and how its PRs relate,
its rows (number, head, title, change, plan, risk, review, blocker, block, state), and
the group's other rows. A question typed into Ask goes to the bus through
`POST /api/asked` as an `owner` record; one starting `[user-test]` posts as a `note` from
lane `dashboard-ask-test`, so a user-test round never reads as his words.

A PR is `you` when the board holds no approval at its current or carried head. A root
`go` at that head means reviewed and checked: the row says so and still waits on his tap.
Only a board approval, a `TAP-CARRIED` or `TAP-KEPT` go, or a CI-fix go outside the
excluded tree makes a board row `queued`; a go makes a PR with no board row `queued`. Verdicts are read live from the
cc-present store on every rebuild, and Approve or Note on the dashboard writes the
board's own `triage.decided` event, so no board page opens.

A step's tone takes the first match over its PRs: done when all landed, then any `you`,
any `failing`, any `held`, moving for any `doing` or a live worker, queued when every PR
is landed or queued, otherwise waiting; a step with no PRs keeps the tone `steps.json`
gives it, such as `later`. A worker's
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
  `next`, every PR of the step landed ones included, and the lanes working on it. A
  step whose blocker waits on the owner's answer to a board card names that card's
  block id in `card`.
- `now.release` and `now.releases[]`: the next release and every release with the parts
  it ships, in plain words; `tonight` marks the releases the release track shows.
- `now.goal`: the end state's checks, each (such as `enabled` and `old_app`) with a
  `state` that opens with its verdict before a colon, when it was read (`at`), its
  `source`, and the `check` that proves it. A check older than 30 minutes shows as
  "not rechecked since" its time, never as current.
- `now.waiting[]`: what the drive waits on outside any step: `what`, `owed_by`, `since`,
  `expected`, and what it `unblocks`. The Overview shows only items that unblock
  something, and one past its `expected` time reads overdue.
- `now.blocker`: a drive-wide stop only, such as a red trunk or a dead merge queue,
  with its start time; null otherwise. A step's own blocker stays on the step.

It writes no counts; the dashboard counts.

The moment he answers a step's `card`, the dashboard drops that step's blocker. A minute
after his answer, a step that still names the card gets one `defect` to `defects_to`
until the progress desk rewrites it.

## Layout

The Overview is the Cockpit, an app and not a page of tables. Every tap acts on the
picture in place, and only the code review opens a page of its own. It has four bands,
top to bottom.

1. Tonight. The current release or where it is blocked, and that release's state. New-app
   PRs merged, releases out, and who has the flag. Whether everyone else keeps today's
   app, what broke tonight, and how many PRs fail their own tests.
2. The plan and the destination, side by side on a wide screen.
   - On the left sit the readouts for landed, in flight and needs you, and the amber
     action card that pages `needs[]`. Below them is one track per active step, plus a
     dashed Unplanned track. A track holds the step's lights in state order, the
     twelve five-minute boxes of each live worker on it, landed over total, and a tone
     glyph. Done and later steps fold into one line.
   - In the center, every track runs into the gate of the first release not yet out. Its color
     comes from live state rather than wording. It is red when a drive-wide stop is set or a PR it
     waits on is red, amber when one waits on him, green when it is live or cutting.
   - On the right, the cloud sits above the ring and the Mac below it. Each machine is an outline traced
     green to its landed share, and each part holds one light per PR. The old tree sits
     in an "Old, kept" band. A tools runway and a replay axis sit below the machines.
3. The route shows R1 to R8 and cutover, the current release's state, and the exact
   plan's thumbnail.
4. Working now shows one tile per worker on a PR.

The picture answers each tap in place.

- A count filters the lights to its population, and a second tap or Esc clears it.
- A light opens a compact sheet for that PR: where it stands, its part, step and
  worker, and Approve when it waits on him. The review is one level deeper.
- A part label opens the part's sheet. A worker strip or tile opens that worker's
  history.
- A track opens its step card, and the gate or a release opens the release card.
- A replay tick shows the picture as of that hour, or as if the steps up to that one
  had landed.

Every `#number` in step, release and plan text renders as a live state chip, so a
blocker that has since landed reads as landed.

A phone puts the counts, the tracks and the gate first, then the machines one above the
other. A fixed thumb dock holds the action card, Plan and Jump. Command-K on a phone is
full screen, with the input at the bottom and a visible Close.

## Words at rest

The Overview at rest shows at most 40 words. Numbers, percentages, times, PR numbers,
release ids, hashes, and glyphs do not count, and lane names never appear. A card opened
by hover or tap holds at most 25 words; the plan sheet is structured, not capped.
Everything else moves to a drill-in: a step card, the PR page with its embedded
cc-review, the plan sheet, or Command-K.
