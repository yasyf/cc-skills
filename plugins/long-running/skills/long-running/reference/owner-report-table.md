# The owner update

Every update the owner gets, whether a status answer, a milestone report or the pre-compaction doc, is one render of `lr update render --drive <drive>`. The communicator's update desk runs it and the communicator sends its output unchanged; nobody types a row. The format comes from the owner's own words on updates, Oct 1 to Nov 2 2026, quoted with their times in cc-notes doc `Owner update format` (label `owner-update-format`). How the communicator produces and sends each one is in [communicator-brief.md](communicator-brief.md).

## When one goes out

- When he asks for status in any words, within two minutes.
- When he returns after 30 minutes or more of silence, as the reply to his first message.
- At a milestone: a plan step lands or goes live, a release goes live or fails, an incident opens or closes.
- Before every compaction, inside the progress record ([compaction.md](compaction.md)).
- Never on a clock and never to Slack.

## Sections, in order

Under its title, first of all, a status he has asked more than once leads as **You asked more than once**: one row per `--topic`, with the release number and live time of the PRs its record names. The communicator tags the record it queues for the ask with `--attr asked=<n>` and `--pr`. Whoever states the fact clears the row, usually the release lane: a record with `--re <seq>` or `--resolves <seq>`, `--attr release=<n>` and `--attr live=<time>`. An answer with no such attrs leaves it first. A status no release answers is withdrawn by the communicator with `--re <seq> --attr asked=0`. He re-asks and the communicator posts a higher count, which leads again.

Next, before any section, the update opens with how many of the coordinator's own items in the drive's store are past due when it renders. One line per item follows, oldest due first, naming it and its due time. With none past due it prints nothing there.

The first section is **Workstreams**, after only his repeated asks: one row per workstream of the drive with what it delivers, its live lanes, its state, what changed since his last read, what blocks it and what he must do. The progress desk's `<state dir>/progress-desk/workstreams.md` holds only what names a workstream, in a table headed `| Workstream | Delivers | Lanes | Steps | Note |`. The Lanes cell lists the lane stems it holds, comma-separated, and a stem also holds every longer stem under it, so `durable-agents` holds `durable-agents-fix-2`; a standing desk or a short-lived lane joins its row by stem. The Steps cell lists the plan step numbers whose lanes it also holds, the Note cell is one optional sentence, and the render computes every other cell when it runs, from what the update already reads.

The lanes cell names the live lanes, or says none is live and names the last lane and when it was heard. The state carries the open PRs with their merge state, the steps' task counts, any owner order it owes and its lanes' newest record, then the note. Since his last read, the changed cell carries each of its PRs that landed, a release record carrying one, and its newest delivery record. The blocker cell carries a lane waiting on the coordinator or whose newest record is an open blocker, a held, red or refused PR, and a blocked task. His action cell carries each open tap or pick on his board for one of its PRs or naming one of its stems, and the read error when the board is unread. An empty cell says nothing was recorded and since when.

Under the table, one line names each live lane no row holds, so a gap shows the moment it opens. A missing or empty list renders one line saying so, and an unreadable one, including one still headed with hand-kept state columns, renders its error; either way the update still renders.

1. **Since you last looked.** Each row that is new, whose owner, blocker or doneness moved, or that left its table because a PR landed, an ask was answered or an incident closed, since the last update he was sent.
2. **Decisions made for you (FYI).** The communicator's own FYIs since the last update, grouped. Each is a plain-words synthesis of something the coordinator decided or solved, or of what is going right or wrong. Each is a `note` record from `owner-relay` on topic `fyi`, and the group is the words before its first colon. The render never turns coordinator records into FYIs.
3. **Your words since the last update.** Each message the communicator queued, quoted whole, with the record that carries it: a later record that answers it with `--re` or resolves it. A record that only names its number does not count, since `#<n>` is also a PR.
4. **Unowned work.** Every open row with nobody on it, as a table. Each also sits in its own section.
5. **Pinned for you.** One row per entry of the drive entry's `pinned_rows`, each a `name`, a `delivers` line, the `words` that pick its lanes by name, step or deliverable, and the `prs` it delivers, set with `lr update pin <name> --pr <n> --word <word> --delivers <text>`, never by hand. A row with `prs` reads from the release ledger, one read per render through the code `lr where` uses, for drive-lane PRs only: `in release <n>, live at <time>`, `release <n> running, ETA <time>`, `landed, no release has cut it yet`, or `still open`. If the read fails, the row reads as it does with no `prs` and the failure is logged. It is on every update, with **nobody** when no lane holds it. For durable-agents-rebuild that is devbox speed and bug hunting, and the next build.
6. **Standing duties.** One row per duty at the newest lane of its successor chain.
7. **Drive tooling.** One row per meta program (the cci token cut, the coordinator kept minimal for Fable, hook tiers, the dashboard, Sprites, launch latency, the Go loop and any other) with its state, its blocker and what comes next, in plain words. The rows come only from the meta lane's `<state dir>/meta/programs.md`, a table headed `| Program | State | Blocker | Next |`, one row per program; a program nothing blocks says `nothing`.
8. **The plan.** One heading per plan, in plain words. Under it a step with no changed row since the last update he was sent is one line, and a step with a changed row gets its own table, step row first and then its lanes. A plan with no numbered steps whose title lists pieces, such as the five services, gets one table per piece, so no piece can drop out. An open step no live lane holds names the last lane that worked it and when.
9. **Lanes by workstream.** One table per other step a brief names in words, then the lanes on no step, which include any lane whose brief names a raw code such as `S5` or a step the plan lacks.
10. **Owner asks, Incidents today, Releases today, Open PRs.** One row each; landed-but-not-live asks come first.
11. Registry lanes quiet over a day, what waits on the coordinator, one line linking his open taps on the dashboard's Needs me tab, and what lanes delivered in the last two hours.
12. **Gaps in this update.** Present only when a message of his has no record carrying it, a live lane is in no workstream, the workstream list is missing, empty or unreadable, a tooling program has no blocker, or the meta lane's list has no programs.
13. **One closing line.** Present only when the render folded or dropped something, and it names each thing it folded or dropped.

## Detail where it matters

Every item is named, and a full row goes where a decision or blocker lives: pinned rows, plan steps and pieces, standing duties, open incidents, and any lane that is blocked or carries a failure. A step's other live lanes appear in its Who. A live lane on no step with nothing blocking it is one line, its newest record. Lanes quiet over two hours, incidents closed today, and open PRs grouped by state are one line each, naming every item. Unowned work names each row once per section. Cells clip at 100 characters. The dashboard's drill-ins hold the rest.

## Plain words and the owner's later rulings

A heading never carries a plan id, a row id or a commit SHA. The render strips a step's number and unit label, any parenthetical citing the plan, a later clause carrying a code, and any stray code or SHA from each plan and step title. An owner ask's row reads as his ask, without its ledger key.

The render takes plan wording from the register, not only the plan. An in-force register row with `plan_was` and `plan_now` fields replaces that phrase wherever the plan's titles and task titles render, in the update and in `steps.json`; [owner-rulings.md](owner-rulings.md) says how to write one.

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
- **What blocks it**: each such lane's open ask or blocker to the coordinator, with its seq and time, or a plan task's wait.
- **What we are doing to unblock it, and by when**: each lane's newest record, and the time its next record is due by the supervisor's stale line. With nobody on it, it says the coordinator starts a lane this turn.
- **What went wrong or slowed it**: each open retro-table row its lanes own, with what prevents a repeat, its state and due time; lanes parked on a gate that has not fired, with the gate; lanes that took over. "None recorded" only when there is none.

## The coverage check

The render prints the update and writes `steps.json` before it exits, whatever its code. It exits 0 when nothing is left out. It exits 1 and names, on one `missing: <count>: <name>; <name>` line on stderr, each section, working lane, open ask and open PR it lacks. Its gaps are each message of his that no record carries, each tooling program with an empty Blocker cell as `<program>: no blocker line`, a missing or empty `meta/programs.md`, the live lanes in no workstream as one `Live lanes in no workstream: <lane>, <lane>` gap, and a missing, empty or unreadable `progress-desk/workstreams.md`. When gaps are all that is wrong it exits 3, naming them on one `gaps: <count>: <gap>; <gap>` line, and the update is complete and ready to send. The gaps are the update's last section, so the owner sees them and the coordinator gets them as a `blocker` from the communicator. `lr update check FILE` reads an update someone else wrote and exits 1 naming each section, workstream, lane, ask and PR it leaves out, each tooling program it never names, and each program whose blocker is on no line that names it; the coordinator's Stop hook runs it on every long reply to the owner.

## The dashboard reads the same rows

`--render --publish` writes `<state dir>/progress-desk/steps.json` from the pinned, plan-step and piece rows of the same render, and its `fyi` list holds the communicator's FYIs of the last day, newest first, for the dashboard's main view. Each step carries `title`, `section`, `state`, `tone`, `delivers`, `who`, `blocker`, `next`, `unblocking`, `went_wrong`, `prs` and `lanes`, and `now.release` and `now.releases[]` come from today's releases. `--render --sent` does the same for an update the owner is sent, and also saves the snapshot the next update's "Since you last looked" diffs against. The dashboard's Overview, its step sheets, and `drive.py overview` read that file, so the dashboard and the written update never disagree. Between updates, the dashboard follows PR and worker state live from the bus and the board ([drive-dashboard.md](drive-dashboard.md)).
