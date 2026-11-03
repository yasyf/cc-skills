# The owner update

Every update the owner gets, whether a status answer, a milestone report or the pre-compaction doc, is one render of `owner_update.py --render --drive <drive>` (`lr update render` once the Go verb is live). The bridge's update desk runs it and the bridge sends its output unchanged; nobody types a row. The format comes from the owner's own words on updates, Oct 1 to Nov 2 2026, quoted with their times in cc-notes doc `Owner update format` (label `owner-update-format`). How the bridge produces and sends each one is in [owner-bridge-brief.md](owner-bridge-brief.md).

## When one goes out

- When he asks for status in any words, within two minutes.
- When he returns after 30 minutes or more of silence, as the reply to his first message.
- At a milestone: a plan step lands or goes live, a release goes live or fails, an incident opens or closes.
- Before every compaction, inside the progress record ([compaction.md](compaction.md)).
- Never on a clock and never to Slack.

## Sections, in order

1. **Since you last looked.** Each row that is new, or whose owner, blocker or doneness moved, since the last update he was sent.
2. **Decisions made for you (FYI).** The bridge's own FYIs since the last update, grouped. Each is a plain-words synthesis of something the coordinator decided or solved, or of what is going right or wrong. Each is a `note` record from `owner-relay` on topic `fyi`, and the group is the words before its first colon. The render never turns coordinator records into FYIs.
3. **Your words since the last update.** Each message the bridge queued, quoted, with the record that carries it: an answer, a resolution, or a later record naming its seq.
4. **Unowned work.** Every open row with nobody on it, as a table. Each also sits in its own section.
5. **Pinned for you.** One row per entry of the drive entry's `pinned_rows`, each a `name`, a `delivers` line and the `words` that pick its lanes by name, step or deliverable. It is on every update, with **nobody** when no lane holds it. For durable-agents-rebuild that is devbox speed and bug hunting, and the next build.
6. **Standing duties.** One row per duty at the newest lane of its successor chain.
7. **The plan.** One table per plan step, its step row first and then its lanes. A plan with no numbered steps whose title lists pieces, such as the five services, gets one table per piece, so no piece can drop out.
8. **Workstreams and lanes.** One table per other step a brief names, then the lanes on no step.
9. **Owner asks, Incidents today, Releases today, Open PRs.** One row each; landed-but-not-live asks come first.
10. Registry lanes quiet over a day, what waits on the coordinator, board picks, and what lanes delivered in the last two hours.
11. **Gaps in this update.** Present only when a message of his has no record carrying it.

## The row

| Row | What it delivers | State | Who is on it right now | What blocks it | What we are doing to unblock it, and by when | What went wrong or slowed it |
|---|---|---|---|---|---|---|

- **Who is on it right now**: each lane on the work that was heard within two hours, or **nobody**.
- **What blocks it**: each such lane's open ask or blocker to the coordinator, with its seq and time, or a plan task's wait.
- **What we are doing to unblock it, and by when**: each lane's newest record, and the time its next record is due by the supervisor's stale line. With nobody on it, it says the coordinator starts a lane this turn.
- **What went wrong or slowed it**: each open retro-table row its lanes own, with what prevents a repeat, its state and due time; lanes gone quiet; lanes that took over. "None recorded" only when there is none.

## The coverage check

The render exits 1 and prints to stderr each section, working lane, open ask and open PR it lacks. It also prints each message of his that no record carries, and that list is the update's last section, so the owner sees the gap and the coordinator gets it as a `blocker` from the bridge. `--check FILE` reads an update someone else wrote and exits 1 naming each section, lane, ask and PR it leaves out.

## The dashboard reads the same rows

`--render --publish` writes `<state dir>/progress-desk/steps.json` from the pinned, plan-step and piece rows of the same render, and its `fyi` list holds the bridge's FYIs of the last day, newest first, for the dashboard's main view. Each step carries `title`, `section`, `state`, `tone`, `delivers`, `who`, `blocker`, `next`, `unblocking`, `went_wrong`, `prs` and `lanes`, and `now.release` and `now.releases[]` come from today's releases. `--render --sent` does the same for an update the owner is sent, and also saves the snapshot the next update's "Since you last looked" diffs against. The dashboard's Overview, its step sheets, and `drive.py overview` read that file, so the dashboard and the written update never disagree. Between updates, the dashboard follows PR and worker state live from the bus and the board ([drive-dashboard.md](drive-dashboard.md)).
