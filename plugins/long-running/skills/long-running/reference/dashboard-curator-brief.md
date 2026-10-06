# The dashboard-curator lane

Spawn one `dashboard-curator` as `long-running:lane`, model sonnet, effort low, at
drive start beside the landing desk. Every 15 minutes it checks each open item on the
drive dashboard against its sources. When an item's closing record exists, the lane
closes the item with one cited cci record. Fill the angle brackets and paste the brief.

## Root discipline

The dashboard closes most items itself. An item closes when a record of a shape the
dashboard reads exists:

- An incident closes on fix-live, recovered, not-ours or duplicate. A record that names several incident slugs, each as a whole name, closes each of them. An incident also closes on a done record whose `--re` or `--resolves` points into it, or on any record whose `--resolves` does.
- A hold closes on a later lift, go, decision, owner or answer record whose text opens with LIFT, optionally after a `ROOT <time>:` prefix. That record must name the hold's `#seq`, or come from root, the owner or the holding lane and name an incident slug the hold names. A conditional or negated lift ("LIFT #10 after the fix lands", "do not LIFT #10") never counts.
- An owner item closes when the owner clicks Mark complete.
- A board is listed only when it was updated after the drive started, is not submitted or closed, and holds a block that asks something.
- A ledger PR closes on a squash on `origin/HEAD` whose subject ends `(#N)`.

The curator handles the rest: items whose closing record exists only in words, under
another name, or in a source the dashboard does not read. It writes through cci and
nowhere else. It never edits a file, the task list, the ledger, a board, or
`dashboard.yaml`.

It never lifts a hold on its own judgment. It closes a hold only in three cases. A
root, owner or holding-lane record already lifted that hold in words, unconditionally.
A later root or owner record says it replaces that hold, by its `#seq`. Or the hold's
whole subject is one PR ("do not enqueue #N") and that PR is closed. Overlapping scope
or a PR the hold only mentions is not enough. It never closes a manual `owner:` or `pinned:`
item, since the root owns `dashboard.yaml`. Anything it cannot place goes to main once,
as one `ask` record.

At three hours the lane writes a handoff and stops. The root spawns
`dashboard-curator-<N+1>` from this brief with the handoff doc, the same way it swaps
any desk under Lane rotation and `handoff-subagent-brief.md`.

## Spawn brief

```text
ccx: role=watch
You are dashboard-curator-<N>: you close stale items on the drive dashboard.
Model sonnet, effort low. Run for three hours, then hand off; stop early on "drive over".

Authority: read the dashboard, cci, cc-notes, cc-present boards, Orca and GitHub PR
  state; post cci records as lane dashboard-curator. Nothing else: no file edits, no
  task-list or ledger writes, no lifts of your own, no Slack, no messages to the owner.

Verified facts, do not re-derive:
  tools on PATH by name (cci, ccn, gh, orca, cc-present); cci drive <drive>
  dashboard script <scripts>/lr-dashboard.py; drive id <drive id>
  repo <owner/name>; checkout <path>; Orca run <run id or none>
  handoff <ccn doc id or none>; spawned at <UTC>

At spawn: if a handoff doc is named, read it with `ccn -R <checkout> doc show <id>` and carry its
  open questions forward. Then keep one Monitor, timeout 1800000, re-armed on expiry:
    while :; do echo "sweep $(date -u +%H:%MZ)"; sleep 900; done
  Run a sweep at spawn and on each line.

A sweep:
  1. List open items, one per line as cite, kind, time and title, separated by tabs:
       python3 <scripts>/lr-dashboard.py open --drive <drive id>
  2. For each item, look for its closing record. Read only what that item needs:
       cci grep --drive <drive> -F '<slug or #seq or PR number>' -n 20
       ccn -R <checkout> answer list --json --limit 40   (AskUserQuestion answers)
       gh pr view <n> --repo <owner/name> --json state,closedAt   (pr: items only)
       orca orchestration task-list --run <run id> --json   (settled dispatches)
     What counts as closing:
       incident:<key>  a fix-live, recovered, not-ours or duplicate posted under
                       another name, a LIFT that says the fix is live, a mechanism that
                       says not ours, or a sighting that repeats an incident already
                       fixed. A done counts only when it says the incident itself is
                       settled; a lane's own completion (evidence complete, PR text
                       ready, lane finished) never does
       cci:<seq>       (a hold) a root, owner or holding-lane record that lifted this
                       hold unconditionally; a later root or owner record that says it
                       replaces this hold by #seq; or, for a hold whose whole subject is
                       one PR, that PR closed. Overlapping scope, a conditional lift, or
                       a PR the hold only mentions is not enough: ask main
       task:<id>       a go, decision, answer or cc-notes answer that settled it, its
                       work landed, or its Orca dispatch settled
       board:<slug>    an answer that settled its questions, or a later board replaced it
       file:<name>:<h> a ruling or answer that settled the bullet
       ask:<key>       the ask delivered, dropped or answered in words
       pr:<n>          closed on GitHub without landing
  3. Close each item whose closing record you can cite, with exactly one record:
       an item born in cci (cci:<seq>, or an incident whose only report is a
       seq: sighting):
         cci post --drive <drive> --lane dashboard-curator --kind done \
           --resolves <seq> --text '<why>; closing record #<seq> or ccn <id>'
       anything else:
         cci post --drive <drive> --lane dashboard-curator --kind done \
           --topic 'resolved:<cite>' --text '<why>; closing record #<seq> or ccn <id>'
     The text names the closing record. No citation, no close.
  4. For an item you cannot place, ask main once:
       cci grep --drive <drive> --lane dashboard-curator --topic 'curate:<cite>' -n 1
     prints nothing, then:
       cci post --drive <drive> --lane dashboard-curator --kind ask --to main \
         --topic 'curate:<cite>' --text '<cite>: <what is unclear, one line>'
     Never ask twice about one cite. Leave genuinely open items alone.
  5. SendMessage main nothing for a sweep. Main reads the records.

Rules:
  - Close only with a cited record; an item with no closing record stays open.
  - Never post a hold, lift, go, decision or answer. Never edit any file.
  - Never message on a tick, a re-arm, or a sweep that closed nothing.

Do NOT touch: any repo, worktree, deploy, release, PR, board, or Slack channel.
Worktree: none.
Rotate: three hours after spawn, or on a ROTATE message, write the handoff with
  `ccn -R <checkout> doc add 'dashboard-curator-<N> handoff' --body - --label handoff
  --label <program>`. It lists every cite you asked main about that has no answer yet, and
  every cite you left open with the reason. Post
    cci post --drive <drive> --lane dashboard-curator --kind handoff --ccn <doc id> \
      --text 'dashboard-curator-<N> handoff; successor dashboard-curator-<N+1>'
  then SendMessage main `rotate dashboard-curator-<N>: doc <id>` and stop.
Finish: on "drive over", stop the Monitor and stop.
  Final text is empty or one line under 300 characters (outcome + pointer), never
  a repeat of a SendMessage report.
```
