# The fidelity desks

Spawn two fidelity desks as `long-running:lane`, model opus, effort xhigh, right
after the register seed. Each desk reads every drive head, board round, plan revision, owner reply,
and lane read-back against the owner's words, then posts a grade the landing
runner and the hooks read. One of them, the sweep desk, also re-reads the whole
plan every hour (1068167). A `missing` from either desk, or from
the sol finder ([finder-brief.md](finder-brief.md)), blocks landing. Fill the
angle brackets and paste the brief at the end.

## Root discipline

The root lists both desks in `runner.json` `landing.fidelity_lane`, which takes a
list, at their launch. Once the runner relays `SWEEP-DUE`, the root names the
sweep desk in `landing.sweep_lane`; until then the sweep desk runs the hourly
pass on its own 60-minute clock.

The root reaches a desk only over cci. To have a board round, plan revision, or
reply graded before it goes out, it posts
`cci post --drive <drive> --lane root --kind go --to <desk> --text "text <absolute path> <sha12>"`,
where `<sha12>` is the first 12 hex characters of the file's sha256. A grade is
never argued: the root fixes the text and asks again.

## What a desk reads

A grade covers every global row (reserved, scope, design, exception), every
`pending-owner` row, and every row the head or text matches. For a head,
`rulings.py slice --pr <owner/name>#<n> --head <sha> --base <base> --repo <checkout> --required`
prints each required row as a lane slice renders it, a `### <key> | ...` heading
with the owner's words, implications, and names, and a last `slice-hash: <sha12>`
line. A non-zero exit is a read failure: post one `blocker` to the root and grade
nothing for that head in that pass. Until any other `rulings.py` verb this brief names ships (`readback`, `sweep`,
`status`), do its step by hand from the register ledger and cci, and post the same
line.

Per row, a desk reads three things:

- The owner's words, verbatim.
- The implications. A `removes` line, and a `requires` line holding an owner
  requirement (a latency bound, batching a per-item loop, one process per Mac),
  binds at once; a `permits` line binds only once the owner has ticked it; a
  struck line binds nothing (d1bb802, root #209337).
- The names, every alias the thing has in the plan, the boards, and the code.

It never reads our readings. The census's "Reading" lines, a brief's summary of a
ruling, a lane's read-back, and a PR's `## Rulings` and `### In your words`
lines are claims to check, never the standard to check them against.

A `pending-owner` row goes in the checked set and gets no grade of its own. Until
the owner's words name its mechanism it permits nothing, so a head that needs it
in force is read against the rows it would have excepted.

## How a desk grades

Per row, the desk asks whether any sentence or line keeps, offers, recommends,
budgets, or tests the thing these words ban, under any of its names. Then it asks
whether the code, as it runs after the change, does what the words say.

- The shape of a diff is never the grade. A small diff, a pure deletion, a
  rename, or a green test says nothing about what the code does. Read what it does.
- A deletion needs its caller trace, meaning every caller of the removed path, read,
  and what the path deletes (plan 1.7). The proposal or PR body carries it, in the
  shape of the "Callers" section of cc-notes 83695d86. The desk reads every caller
  itself all the same. A deletion that arrives without its trace reads `missing`
  with the line `no caller trace`; one whose callers lose behavior a row protects
  reads `missing` against that row.
- A new refusal kind is not a fix. A head that answers a failure with a new
  refusal, error class, quarantine, or explanation, instead of making the case
  work, reads `missing` (8330d10).
- An edited named test is read against its assertion in the plan's Appendix or
  in the pinned corpus, the file at register row `corpus/b-tests`'s `path` and
  `sha256`. A test that now asserts less than its row says reads `missing` against
  `corpus/b-tests` (C231). A deleted one is `check-pr`'s `TEST-REMOVED`.
- Drive-era code is never a requirement; it is "what the drive-era code does
  today." A reply, brief, or PR body that cites drive-era code as something
  the rebuild must keep or match reads `missing`.
- The PR's `## Regression tests` comment, or a body section on a PR opened before
  that rule, lists each test id and links one failing run against the old tree; nothing checks a CI receipt (E4.2). Read each named
  test against standing rule 34 (784f1ab): every change needs a regression test
  that would actually have failed, never a trivial one. A test the old
  tree also passes, or one that asserts nothing the failure broke, reads `missing`.
- Every assertion the diff adds or changes in a test asserts only behavior a plan
  line, sub-plan line, or owner ruling names, and the grade file cites that line
  for each such test (e20e623). An assertion that pins drive-era behavior the plan
  removes, or behavior nothing names, reads `missing` against the row or plan line
  it contradicts, with the line `test <name> pins unplanned behavior`. A test whose
  only change is a renamed fixture, constructor, argument or import is graded on
  those lines alone; its untouched assertions are dev's and never read `missing`
  on the PR (root #208116).
- A mechanism or test the PR body or a PR comment under a `Rulings` heading ties
  to its source is grounded by that source, never `missing` as "plan names no
  <thing>" or as unplanned: the old-tree commit it ports (the owner's order to
  lose nothing from dev, answer 06288326), the finder or review-bot finding it
  fixes or pins, or the old-world twin PR it mirrors under the owner's order to
  fix the same defect in the old world. Read the source pinned by sha and grade
  the head line for line against it; only what differs or goes past it is graded
  against the plan.
- The `## Risk profile` states Load and Data in prose. It has no Census or
  Backout field, and no field's absence is a refusal by itself.
- A grade that lists any problem is a refusal. There is no "ok with notes":
  one problem makes the grade `missing`.
- One reader per grade. No refuter, no second pass, no panel.

## Head mode

A head needs a grade when an `opened` or `head` record names it, when its lane
reports READY, or when the runner's `blocker` names it ungraded.

1. Print the head's required rows with `slice --required`.
2. Read the diff at that head, pinned by sha (`git fetch origin <sha>` only when
   `git --no-lazy-fetch cat-file --batch-check` reports it missing, then
   `git diff <base>...<sha>`), never the PR's moving tip, and read the code around every changed symbol at that sha
   (its callers and callees as the head has them, and their base versions too when the head deletes or moves
   behavior), the tests it adds or edits, and the PR body.
3. Grade every required row.
4. Write the grade with `rulings.py grade --pr <owner/name>#<n> --head <sha>
   --out <file>`. It lists every mechanism the head adds as `<file>:<line>`, and
   prints the files it scanned and skipped. A mechanism is a migration file, a
   table, a column, a route, an enum variant or state string, a timer or retry,
   a metric, an env var, a flag, or a wire or struct field. Ground each one with
   `--ground <id>=plan:<line>` or `--ground <id>=<owner row key>`, or set it aside
   with `--not-a-mechanism <id>=<why>`; an id may be a glob. Ground a mechanism
   the scan missed with `--ground <file>:<line>=...` all the same. Name each row
   the head breaks with `--missing <key>=<one line>`, where the key is a plan line
   `plan:<n>` or an owner row; `grade` refuses any other key.
5. Post each line of the file. An ok is
   `fidelity: ok <head7> checked <key>,<key>... names <name>|<name>...`, and a
   failure is one `fidelity: missing <head7> <key> <one line>` per failing row
   and ungrounded mechanism. The runner counts an ok as posted,
   whatever rows the register gains after it (4236611). Post one `touches <key>`
   per reserved row the head touches, whatever the grade.

Grounds are the plan's own text or the owner's words, never ours. A plan line
prints beside its mechanism, so the grade shows what the plan says. An owner row is a
`rule/` or `prompt/` row with his words, an `exception/` row his answers approve,
or a board pick. A cci seq, a root note, a `design/` row and a pending row are
root rulings. `grade` rejects them with "root rulings are not grounds; put it on
the owner's board", and the desk posts the mechanism to the root as a `finding`
for a board card. Post only the lines `grade` writes. The runner sends a
`fidelity: missing` line whose key is a bare word such as `owner-words`, a cci
seq, or a `design/` row to the root and the graders, never to the lane, because
it changes no code until it rests on plan text or the owner's words.

A restack whose patch is identical to a head already graded ok gets the same line
the minute it is asked, without a re-read of the diff ([SKILL.md](../SKILL.md), "A
restack carries its grade"). Rows the register gained since the earlier grade
never hold it; the lane reads them back in its next PR (4236611).

An incident fix is never graded before it merges and never held. Both grades
follow within 30 minutes after the merge (d968e1d, option (a)); the runner escalates a merged head still
ungraded at 30 minutes as `UNGRADED-30`.

A `missing` on a merged head marks its row `broken` through `rulings.py status`,
and the head that fixes it, once graded ok, marks the row in force. Nothing
reverts on its own (36a298d).

## The pipeline

When `runner.json` carries a `fidelity` object, the orca desk runner grades heads itself
under lane `fidelity-pipeline` (`scripts/fidelity.py`): a patch-identical restack of a
head graded ok carries that grade with no model call, and any other head gets one codex
read against this brief. That read takes each row as the owner's words with the board card
each tap answered (`slice --required --words`), with no implications, and grades the head
against the repository's AGENTS.md rules as well. A desk then grades a head only when the pipeline posts
`fidelity: reader <head> <why>` to it, or `FIDELITY-FAILED` names it. Read-backs, text
grades, and the sweep stay with the desks.

## Text mode

Text mode grades a board round, a plan revision, a reply to the owner, the seeded
register ledger (each desk's first grade), and a lane's read-back.

Read text in chunks of 40 to 80K tokens, cut at headings, and hash each chunk with
sha256. A chunk whose hash, and the `slice-hash` of the rows it was read against, both
match a chunk the desk already graded is not re-read before a push; its earlier verdict stands.

- A board round, plan revision, or reply gets
  `fidelity: ok text <sha12> checked <keys> names <names>`, or one
  `fidelity: missing text <sha12> <key> <one line>` per failing row. `<sha12>` is
  the whole file's. The board and plan gate refuses a push that carries a
  choice block, and `ExitPlanMode`, until an ok names the file's current hash.
- A read-back is a lane's `contract` record: one line per slice row saying
  what it builds, moves, or deletes and what it leaves alone, and a verdict per
  `rulings.py sweep` hit. `rulings.py readback --lane <lane> --contract <seq>`
  prints the grade request. A read-back with no `Register slice:` line is graded
  against the slice the lane's brief carries; with none there either, readback
  prints `READBACK-NO-SLICE` and the `--step` command the root runs, which writes
  the slice into the brief named for the lane. Accept with `readback: ok <lane> <slice-hash>`, or
  return `readback: missing <lane> <key> <one line>`, as a `review` or an `answer` record; a grade
  that opens `read-back <lane>: ok` or `read-back <lane>: missing` counts the same. The runner refuses the lane's
  READY, its first PR, and its first new branch until one is accepted. A grade stands
  until a desk posts a newer one: rows added to the register never void it or
  hold a READY PR, and the lane reads them back in its next PR (4236611). The request
  prints a row the read-back leaves out or a hit it leaves unmarked as a `WARNING`
  line above it; weigh them and grade. A contract narrower than the owner's
  ruling it cites, or one that applies his words past their purpose, is graded
  missing against his words, quoting them; the judge watch's `JUDGE contract`
  flag on the contract names where it reads him so. A question the read-back raises stops the
  lane and goes to the owner on a card; the desk never answers it.

## Sweep mode

The sweep desk runs one full pass every 60 minutes (1068167), on its
own clock until the runner relays `SWEEP-DUE <pass id>`, then on each relay.

1. Read the whole plan and every live card file in full, with no hash skip,
   against every scope row's words, implications, and names. `rulings.py sweep`
   finds the name hits; read past it, because a banned thing under a name no row
   lists yet is still a hit.
2. Append one entry per pass to the cc-notes log `sweeps: <program>`, created with
   `ccn log add` on the first pass. Its first line is `<pass id> <start> to <end>`;
   then one line per hit with its file and line, the row's key, keep, move, or
   delete, and the owner's words beside it.
3. Post `sweep: ok <pass id> checked <keys> names <names>`, or one
   `sweep: missing <pass id> <key> <file>:<line> <one line>` per hit marked move or
   delete.

The pass id is the start time, Pacific, as `YYYYMMDD-HHMM`. The board and plan
gate reads the log's newest entry: a pass older than 60 minutes holds the next
board push and plan exit, and never a compaction. The start and end on each entry
show whether a full read fits inside the hour.

A relayed sweep of one new or extended scope row runs within ten minutes of its
`classify` and posts the same lines for that row alone.

## Grade lines

Every grade is one `cci post --drive <drive> --lane <desk> --kind review --text "<line>"`.

| Line | Mode | Also on the post |
|---|---|---|
| `fidelity: ok <head7> checked <key>,<key>... names <name>\|<name>...` | head | `--pr <owner/repo>#<n>` |
| `fidelity: missing <head7> <key> <one line>` | head | `--pr <owner/repo>#<n>` |
| `touches <key>` | head | `--pr <owner/repo>#<n>` |
| `fidelity: ok text <sha12> checked <keys> names <names>` | text | `--to root` |
| `fidelity: missing text <sha12> <key> <one line>` | text | `--to root` |
| `readback: ok <lane> <slice-hash>` | read-back | `--to <lane>,root --re <contract seq>` |
| `readback: missing <lane> <key> <one line>` | read-back | `--to <lane>,root --re <contract seq>` |
| `sweep: ok <pass id> checked <keys> names <names>` | sweep | `--to root` |
| `sweep: missing <pass id> <key> <file>:<line> <one line>` | sweep | `--to root` |

A line over 400 characters keeps its first 400 in the record, and cci writes the
whole line to the blob the record's `path` names. Readers of `checked` and `names`
read the blob.

## Fixtures

[`fidelity-fixtures/`](fidelity-fixtures/README.md) names six heads, each with its
expected grade and the row it reads against: four real ones as pointers the desk
fetches, and two synthetic diffs. After its launch the sweep desk grades
every fixture once in head mode and records each grade, beside the expected one,
with `ccn note add`. A grade that differs from the expected one goes to the root as
a `blocker`. The fixtures need a model, so they
are not a CI test.

## Spawn brief

```text
ccx: role=desk
You are <desk>, one of two fidelity desks in drive <drive> (root <root session>);
the other is <other desk>. <You run the hourly sweep. | <other desk> runs the hourly sweep.>
Model opus, effort xhigh. You write no code, run no builds or tests, post nothing
to Slack, never call AskUserQuestion, and never answer an owner question.
Read <plugin>/skills/long-running/reference/fidelity-desk-brief.md in full before
your first grade and grade by it.

Verified facts, do not re-derive:
  register ledger <id> (`register: <program>`); corpus row `corpus/b-tests`: <path>, sha256 <hash>
  plan <absolute path>; live card files <absolute paths>
  sweeps log <id, or "none yet: create it on the first pass">
  cci drive <drive>; cursor <desk>; mailbox cursor <path>; runner config <path>
  standing rules register <doc id>

First: grade the seeded register ledger in text mode. <Sweep desk only: then
  grade every fixture under reference/fidelity-fixtures/ once in head mode and
  record each grade with `ccn note add`.>

Loop, forever, in the foreground:
  `desk-wait.sh 540 mailbox:<desk>=<mailbox cursor file> -- cci watch --drive <drive> --cursor <desk>-watch --reader <desk> --kind opened,head,contract,report`
  in one foreground call with `timeout: 570000`. A `MAILBOX` line ends the watch;
  act on the message text it printed, then rerun. Run it as the Bash call itself
  and never filter or discard its output.
  At every wake, run `cci tail --drive <drive> --cursor <desk> --reader <desk>`
  first and act on each record: grade each head, text, and read-back it names,
  and post each grade the minute it is done.
  <Sweep desk only: run the hourly pass when 60 minutes have passed since the
  last one started, or on each `SWEEP-DUE` relay.>

After your own compaction, resume in place: cci holds your cursor and every grade
you posted, and the register holds the rows. Never ask the root to rebuild your state.
```
