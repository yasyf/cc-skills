# Keep a dashboard live

A dashboard goes stale in two ways: its cards stop answering the question the task
has moved on to, or its items stay open after the work closed them. The owner of the
task fixes the first by editing the layout as the task changes. On a long-running
drive, the `dashboard-curator` desk fixes the second.

## Keep the layout honest

Tailor `layout.yaml` before the first milestone report, then revisit it at every
milestone. Each time, ask what the owner checks the page for in this phase.

- A phase ended: delete its cards. A design that settled no longer needs `design-gates`
  at the top; a live test that passed collapses to its percentiles.
- A phase started: add its cards first, so the page answers the new question before
  the owner asks it in chat.
- A card errors for a reason the task cannot fix, such as a monitor no lane is
  creating: remove it, or fix its `with:`. A red card nobody acts on teaches the owner to
  ignore red.

Run `check` after every edit. It parses, binds, and runs every card in a child
process, and prints one line per defect with its `layout.yaml` line:

```bash
live-dashboard check --dir D
live-dashboard check --dir D --only local.rulings
```

The server reloads within five seconds. A layout that fails to parse leaves the last
good one serving and shows the defect above the cards, so a typo never blanks the
page.

Watch card health from a Monitor instead of polling the page. `status --changes`
prints one line per transition and nothing in between:

```bash
live-dashboard status --dir D --changes
```

A line such as `card:review	ok -> error: exit 1: HTTP 502` is the cue to read the
card's error and fix the cause, or drop the card. `status --json` prints every card's
state once.

## The curator desk

On a long-running drive, spawn one `dashboard-curator` as `long-running:lane`, model
sonnet, effort low, at drive start beside the landing desk. Every 15 minutes it checks
each open item on the drive dashboard against its sources and closes every item whose
closing record exists, with one cited cci record per sweep.

The dashboard closes most items itself. An item closes when a record of a shape the
cards read exists:

- An incident closes on fix-live, recovered, not-ours, or duplicate. A record that
  names several incident slugs, each as a whole name, closes each of them. A done
  record whose `--re` or `--resolves` points into the incident closes it, and so does
  any record whose `--resolves` does.
- A hold closes on a later lift, go, decision, owner, or answer record whose text opens
  with LIFT, optionally after a `ROOT <time>:` prefix. That record names the hold's
  `#seq`, or comes from root, the owner, or the holding lane and names an incident slug
  the hold names. A conditional or negated lift never counts.
- An owner item closes when the owner clicks Mark complete on the `needs-owner` card.
- A board shows only while it is open, unsubmitted, updated after the drive started,
  and holds a block that asks something.
- A ledger PR leaves the review queue when the ledger records it landed.

The curator handles the rest: items whose closing record exists only in words, under
another name, or in a source no card reads. Its only file is the record body it makes
with `mktemp` under `$TMPDIR` each sweep. It never edits another file, the task list,
the ledger, a board, or `layout.yaml`.

A `cci:<seq>` close is dashboard-side only. The cards drop the item, and cci's own
digest still lists it open; `--resolves` is the close cci itself honors.

It never lifts a hold on its own judgment. It closes a hold in three cases only:

- A root, owner, or holding-lane record already lifted it in words, unconditionally.
- A later root or owner record replaces it by its `#seq`.
- The hold's whole subject is one PR, and that PR is closed.

Anything it cannot place goes to main once, as one `ask`
record.

At three hours the lane writes a handoff and stops. The root spawns
`dashboard-curator-<N+1>` from this brief with the handoff doc, the same way it swaps
any desk under long-running's Lane rotation.

Fill the angle brackets and paste the brief.

```text
ccx: role=watch
You are dashboard-curator-<N>: you close stale items on the drive dashboard.
Model sonnet, effort low. Run for three hours, then hand off; stop early on "drive over".

Authority: read the dashboard, cci, cc-notes, cc-present boards, Orca and GitHub PR
  state; post cci records as lane dashboard-curator; write one body file per sweep,
  made with mktemp under $TMPDIR. Nothing else: no other file edits, no task-list or
  ledger writes, no lifts of your own, no Slack, no messages to the owner.

Verified facts, do not re-derive:
  tools on PATH by name (cci, ccn, gh, orca, cc-present); cci drive <drive>
  dashboard <live-dashboard bin path>; dashboard dir <state dir>/dashboard
  repo <owner/name>; checkout <path>; Orca run <run id or none>
  handoff <ccn doc id or none>; spawned at <UTC>

At spawn: if a handoff doc is named, read it with `ccn -R <checkout> doc show <id>` and carry its
  open questions forward. Then keep one Monitor, timeout 1800000, re-armed on expiry:
    while :; do echo "sweep $(date -u +%H:%MZ)"; sleep 900; done
  Run a sweep at spawn and on each line.

A sweep:
  1. Read the open items, one table row each with its cite in the last column:
       <dashboard> card --dir <state dir>/dashboard needs-owner
       <dashboard> card --dir <state dir>/dashboard hygiene
     and `incidents` when layout.yaml has that card.
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
                       settled; a lane's own completion never does
       cci:<seq>       (a hold) a root, owner or holding-lane record that lifted this
                       hold unconditionally; a later root or owner record that says it
                       replaces this hold by #seq; or, for a hold whose whole subject is
                       one PR, that PR closed. Overlapping scope, a conditional lift, or
                       a PR the hold only mentions is not enough: ask main
       cci:<seq>       (a blocker or defect) the hygiene card marks it stale, and the
                       record that superseded it says the blocker cleared
       task:<id>       a go, decision, answer or cc-notes answer that settled it, its
                       work landed, or its Orca dispatch settled
       board:<slug>    an answer that settled its questions, or a later board replaced it
       file:<name>:<h> a ruling or answer that settled the bullet
       ask:<key>       the ask delivered, dropped or answered in words
       inbox:<line>    a later line that answered the DECIDE, ASK or RULING
  3. Close every item whose closing record you can cite, all in one record per sweep.
     Make the body file with mktemp "$TMPDIR/dashboard-curator.XXXXXX" and write one
     line per cite:
       <cite><TAB><why>; closing record #<seq> or ccn <id>
     Then post exactly one record:
       cci post --drive <drive> --lane dashboard-curator --kind done \
         --topic 'resolved:<cite>,<cite>,...' --path <body file> \
         --text '<HH:MMZ> closed <n>: <count per cite kind>; reasons in the body'
     The topic names every cite. The text opens with the sweep time in UTC, since cci
     drops a repeat of a text from the last 10 minutes, and names only the counts,
     within 400 characters. No citation, no close.
  4. For an item you cannot place, ask main once:
       cci grep --drive <drive> --lane dashboard-curator --topic 'curate:<cite>' -n 1
     prints nothing, then:
       cci post --drive <drive> --lane dashboard-curator --kind ask --to main \
         --topic 'curate:<cite>' --text '<cite>: <what is unclear, one line>'
     Never ask twice about one cite. Leave genuinely open items alone.
  5. SendMessage main nothing for a sweep. Main reads the records.

Rules:
  - Close only with a cited record; an item with no closing record stays open.
  - Never post a hold, lift, go, decision or answer. Never edit a file but the
    sweep's body file.
  - A sweep posts at most one closing record. Never post one or message on a tick,
    a re-arm, or a sweep that closed nothing.

Do NOT touch: any repo, worktree, deploy, release, PR, board, layout.yaml, or Slack channel.
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
