# The owner update

Every update the owner gets, whether a status answer, a milestone report or the pre-compaction doc, is one render of `lr update render --drive <drive>`. The communicator's update desk runs it and the subagent the communicator spawns sends its output unchanged; nobody types a row. The update base is never carried by hand across days: `lr update render` replaces it. How the communicator produces and sends each one is in [communicator-brief.md](communicator-brief.md). The owner's words behind the format are in cc-notes doc `Owner update format` (label `owner-update-format`).

Contents:
- [When one goes out](#when-one-goes-out)
- [Sections, in order](#sections-in-order)
- [You asked more than once](#you-asked-more-than-once)
- [Workstreams](#workstreams)
- [Pinned for you](#pinned-for-you)
- [Detail where it matters](#detail-where-it-matters)
- [Plain words](#plain-words)
- [Staying under 40 KB](#staying-under-40-kb)
- [The row](#the-row)
- [The coverage check](#the-coverage-check)
- [The dashboard reads the same rows](#the-dashboard-reads-the-same-rows)

## When one goes out

- When he asks for status in any words, within two minutes.
- When he returns after 30 minutes or more of silence, as the reply to his first message.
- At a milestone: a plan step lands or goes live, a release goes live or fails, an incident opens or closes.
- Before every compaction, inside the progress record ([compaction.md](compaction.md)).
- Never on a clock and never to Slack.

## Sections, in order

1. **You asked more than once** ([below](#you-asked-more-than-once)), first of all under the title.
2. A line counting the coordinator's own items in the drive's store that are past due when the render runs, then one line per item, oldest due first, naming it and its due time. With none past due it prints nothing.
3. **Workstreams** ([below](#workstreams)).
4. **Since you last looked.** Each row that is new, whose owner, blocker or doneness moved, or that left its table because a PR landed, an ask was answered or an incident closed, since the last update he was sent.
5. **Decisions made for you (FYI).** The communicator's own FYIs since the last update, grouped. Each is a plain-words synthesis of something the coordinator decided or solved, or of what is going right or wrong. Each is a `note` record from `owner-relay` on topic `fyi`, and the group is the words before its first colon. The render never turns coordinator records into FYIs.
6. **Your words since the last update.** Each message the communicator queued, quoted whole, with the record that carries it: a later record that answers it with `--re` or resolves it. A record that only names its number does not count, since `#<n>` is also a PR.
7. **Unowned work.** Every open row with nobody on it, as a table. Each also sits in its own section.
8. **Pinned for you** ([below](#pinned-for-you)).
9. **Standing duties.** One row per duty at the newest lane of its successor chain.
10. **Drive tooling.** One row per meta program (the cci token cut, the coordinator kept minimal for Fable, hook tiers, the dashboard, Sprites, launch latency, the Go loop and any other) with its state, its blocker and what comes next, in plain words. The rows come only from the meta lane's `<state dir>/meta/programs.md`, a table headed `| Program | State | Blocker | Next |`, one row per program. A program nothing blocks says `nothing`.
11. **The plan.** One heading per plan, in plain words. Under it a step with no changed row since the last update he was sent is one line, and a step with a changed row gets its own table, step row first and then its lanes. A plan with no numbered steps whose title lists pieces, such as the five services, gets one table per piece, so no piece can drop out. An open step no live lane holds names the last lane that worked it and when.
12. **Lanes by workstream.** One table per other step a brief names in words, then the lanes on no step, which include any lane whose brief names a raw code such as `S5` or a step the plan lacks.
13. **Owner asks, Incidents today, Releases today, Open PRs.** One row each; landed-but-not-live asks come first.
14. Registry lanes quiet over a day, what waits on the coordinator, one line linking his open taps on the dashboard's Needs me tab, and what lanes delivered in the last two hours.
15. **Gaps in this update.** Present only when one of these holds: a message of his has no record carrying it; a live lane is in no workstream; the workstream list is missing, empty or unreadable; a tooling program has no blocker; or the meta lane's list has no programs.
16. **One closing line.** Present only when the render folded or dropped something, and it names each thing it folded or dropped.

## You asked more than once

A status he has asked more than once leads as **You asked more than once**: one row per `--topic`, with the release number and live time of the PRs its record names.
- The communicator tags the record it queues for the ask with `--attr asked=<n>` and `--pr`.
- Whoever states the fact clears the row, usually the release lane: a record with `--re <seq>` or `--resolves <seq>`, `--attr release=<n>` and `--attr live=<time>`. An answer with no such attrs leaves the row first.
- A status no release answers is withdrawn by the communicator with `--re <seq> --attr asked=0`.
- When he re-asks, the communicator posts a higher count, which leads again.

## Workstreams

One row per workstream of the drive with what it delivers, its live lanes, its state, what changed since his last read, what blocks it and what he must do. It comes after only his repeated asks.

The progress desk's `<state dir>/progress-desk/workstreams.md` holds only what names a workstream, in a table headed `| Workstream | Delivers | Lanes | Steps | Note |`.
- The Lanes cell lists the lane stems it holds, comma-separated. A stem also holds every longer stem under it, so `durable-agents` holds `durable-agents-fix-2`. A standing desk or a short-lived lane joins its row by stem.
- The Steps cell lists the plan step numbers whose lanes it also holds. The Note cell is one optional sentence.
- The render computes every other cell when it runs, from what the update already reads.

How the render fills the computed cells:
- **Lanes:** the live lanes, or a line saying none is live and naming the last lane and when it was heard.
- **State:** the open PRs with their merge state, the steps' task counts, any owner order it owes and its lanes' newest record, then the note.
- **Changed:** each of its PRs that landed since his last read, a release record carrying one, and its newest delivery record.
- **Blocker:** a lane waiting on the coordinator or whose newest record is an open blocker, a held, red or refused PR, and a blocked task.
- **His action:** each open tap or pick on his board for one of its PRs or naming one of its stems, and the read error when the board is unread.
- An empty cell says nothing was recorded and since when.

Under the table, one line names each live lane no row holds, so a gap shows the moment it opens. A missing or empty list renders one line saying so, and an unreadable one, including one still headed with hand-kept state columns, renders its error. Either way the update still renders.

## Pinned for you

One row per entry of the drive entry's `pinned_rows`, on every update, with **nobody** when no lane holds it. Each entry is a `name`, a `delivers` line, the `words` that pick its lanes by name, step or deliverable, and the `prs` it delivers, set with `lr update pin <name> --pr <n> --word <word> --delivers <text>`, never by hand. For durable-agents-rebuild the pinned rows are devbox speed and bug hunting, and the next build.

A row with `prs` reads from the release ledger, one read per render through the code `lr where` uses, for drive-lane PRs only. It reads `in release <n>, live at <time>`, `release <n> running, ETA <time>`, `release <n> queued` or `held`, `landed; release <n> cut it and failed, so no release carries it now`, `landed, no release has cut it yet`, or `still open`; a release that leaves out a target the change touches adds `; it leaves out <targets>`. If the read fails, the row reads as it does with no `prs` and the failure is logged.

## Detail where it matters

- Every item is named. A full row goes where a decision or blocker lives: pinned rows, plan steps and pieces, standing duties, open incidents, and any lane that is blocked or carries a failure. A step's other live lanes appear in its Who.
- A live lane on no step with nothing blocking it is one line, its newest record.
- Lanes quiet over two hours, incidents closed today, and open PRs grouped by state are one line each, naming every item.
- Unowned work names each row once per section.
- Cells clip at 100 characters. The dashboard's drill-ins hold the rest.

## Plain words

- A heading never carries a plan id, a row id or a commit SHA. The render strips a step's number and unit label, any parenthetical citing the plan, a later clause carrying a code, and any stray code or SHA from each plan and step title. An owner ask's row reads as his ask, without its ledger key.
- When the owner asks what a code term means, the definition comes from the owning lane's record or the code, never an invented paraphrase.
- The render takes plan wording from the register, not only the plan. An in-force register row with `plan_was` and `plan_now` fields replaces that phrase wherever the plan's titles and task titles render, in the update and in `steps.json`; [owner-rulings.md](owner-rulings.md) says how to write one.

## Staying under 40 KB

An update never exits 1 for its size. Over 40 KB, the render folds in this order and stops as soon as it fits:

1. Done and unchanged rows, to one line per section. Pinned rows, owner asks and the five-services piece rows do not fold here.
2. What waits on the coordinator, deliveries and owed deletions with no live owner, to short lines, and "Since you last looked" to one line per changed row with names only.
3. Open PRs, to their numbers.
4. Lanes, to their blockers and names.
5. Every plan step, to one line.
6. Unowned lanes, to counts.
7. Quiet lanes, to their names.
8. FYIs, to the newest three per group, each cut at 240 characters.
9. Standing duties, to one line each.
10. Blocked lanes, to their names. What they wait on stays in "Waiting on the coordinator".
11. The messages quoted from him, to their first 240 characters, keeping what carries each.

If that is still over, the render drops whole lines from the end of the body, keeping the gaps, and the closing line says how many it dropped and after which heading. A dropped item no longer counts as missing. The workstreams and tooling tables never fold.

## The row

| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |
|---|---|---|---|---|---|---|

- **Who is on it right now**: each lane on the work that was heard within two hours, or **nobody**.
- **State** of a plan step: its task counts. With **nobody** on it, its tasks in progress count as stale, and the cell names the last lane whose record named the step, with that record's kind and time. A standing desk is never on a plan step, by its brief's step or by its records.
- **What blocks it**: each such lane's open ask or blocker to the coordinator, with its seq and time, or a plan task's wait. Every status row carries how long it has been blocked; a state without its duration is a defect. Write "blocked N min since <time> on <what>".
- **What we are doing to unblock it, and by when**: each lane's newest record, and the time its next record is due by the supervisor's stale line. With nobody on it, it says the coordinator starts a lane this turn.
- **What went wrong or slowed it**: each open retro-table row its lanes own, with what prevents a repeat, its state and due time; lanes parked on a gate that has not fired, with the gate; lanes that took over. "None recorded" only when there is none.

## The coverage check

The render prints the update and writes `steps.json` before it exits, whatever its code.

| Exit | Means | Output |
|---|---|---|
| 0 | nothing is left out | the update |
| 1 | something is left out | one `missing: <count>: <name>; <name>` line on stderr naming each section, working lane, open ask and open PR it lacks |
| 3 | gaps are all that is wrong; the update is complete and ready to send | one `gaps: <count>: <gap>; <gap>` line naming them |

The gaps are:
- each message of his that no record carries;
- each tooling program with an empty Blocker cell, as `<program>: no blocker line`;
- a missing or empty `meta/programs.md`;
- the live lanes in no workstream, as one `Live lanes in no workstream: <lane>, <lane>` gap;
- a missing, empty or unreadable `progress-desk/workstreams.md`.

 The gaps are the update's last section, so the owner sees them, and the coordinator gets them as a `blocker` from the communicator.

`lr update check FILE` reads an update someone else wrote and exits 1 naming each section, workstream, lane, ask and PR it leaves out, each tooling program it never names, and each program whose blocker is on no line that names it. The coordinator's Stop hook runs it on every long reply to the owner.

## The dashboard reads the same rows

- `--render --publish` writes `<state dir>/progress-desk/steps.json` from the pinned, plan-step and piece rows of the same render. Its `fyi` list holds the communicator's FYIs of the last day, newest first, for the dashboard's main view.
- Each step carries `title`, `section`, `state`, `tone`, `delivers`, `who`, `blocker`, `next`, `unblocking`, `went_wrong`, `prs` and `lanes`. `now.release` and `now.releases[]` come from today's releases.
- `--render --sent` does the same for an update the owner is sent, and also saves the snapshot the next update's "Since you last looked" diffs against.
- The dashboard's Overview, its step sheets, and `drive.py overview` read that file, so the dashboard and the written update never disagree. Between updates, the dashboard follows PR and worker state live from the bus and the board ([drive-dashboard.md](drive-dashboard.md)).
