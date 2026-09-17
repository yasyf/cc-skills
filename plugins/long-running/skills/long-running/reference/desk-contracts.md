# Desk message contracts

Every message between a lane, the landing desk, and the root has one shape. A message
in any other shape costs a turn to decode and a turn to answer, and on a drive with
forty lanes those turns are the orchestrator's window. The desk types each inbound
message into `ledger.py` as it arrives, so the shapes below are also the tool's inputs.

## The lane report, three lines

A lane sends this to `landing-desk`, and only to `landing-desk`, when it ships a PR,
pushes a new head, or reaches a terminal state on one. Nothing else goes to the desk.

```
PR #<n> <head sha, full> <clean|red|conflicting|held>
<what changed on this head, one line>
<what is next, one line, or "none">
```

The desk records it as `ledger.py report --pr <n> --head <sha> --lane <name> --verdict
<v> --text "<line 2>"`, which is also the only way a PR row is opened by hand. The same
PR, head, and verdict twice is one report: the second is dropped and never answered. A
new head is a new report.

Rules the report carries:

- The head is the sha the lane pushed, in full. The desk re-reads the forge before it
  acts on it, so a stale sha is caught, but a short sha cannot be compared.
- Handing a PR to the desk as `clean` is the label going on. From that line onward the
  branch is not the lane's to amend; further work is a new branch off a fresh trunk.
- A lane mid-rebase reports `conflicting` and pushes nothing labelled. It reports
  `clean` again only on the rebased head.
- Prose goes to a cc-notes note the report names on line 2, never into the message.

## RULING NEEDED, one line

The only message that reaches the root outside the hourly summary. Sent by the desk,
on its own behalf or relayed for a lane, when a decision needs the owner or the root.

```
RULING NEEDED: <the question, one line>; options: A <..> / B <..> / C <..>
```

`ledger.py ruling --text "<question>" --options "A <..>|B <..>|C <..>"` records it and
prints the line to forward. The root answers with the letter. Two rulings with the same
question on the same PR are one ruling.

## Reconcile before reporting, over every row

`ledger.py reconcile --repo --ledger --checkout` settles **every non-terminal row** against the
trunk and the forge, and `ledger.py summary` runs it first whenever it is given `--repo` and
`--checkout`. A row nobody has touched for an hour is exactly the one that has gone stale, so the
sweep's input is the whole board rather than the rows the desk just changed.

A pass that walks only what the desk changed cannot find what the desk failed to do: it reports
zero movement truthfully every time, because it is iterating the wrong set. One desk reported a
board as 131 merged with three rows open and needing lanes; walking every row found seventeen
stale and the real count at 148, with two of the three "open" rows closed by their own lanes hours
earlier. A lane closing its own pull request never reaches the desk as an event, which is why a
`held` row decays silently.

## The desk to root summary, at most ten lines, once an hour

`ledger.py summary` prints it; the desk sends it unchanged. Line one is always the
counts; the lines after it exist only when they carry something.

```
desk <stamp> | open N | merged/h N | labelled N | held N | rulings N | p0 N | routed N
merged: #a #b
labelled: #c #d
P0 #e <lane>: <text>
RULING NEEDED: <question>; options: A / B / C
held #f: <reason> until <stamp>[ EXPIRED]
routed, awaiting a new head: #g #h
```

`merged/h` counts squash commits on the base branch inside the window, read from the
git log by `ledger.py landed`. It never reads a PR's `merged` field, which the Graphite
queue leaves false on every PR it lands. An eleventh line is replaced by a pointer at
`ledger.py show`.

## Idle notices: record once, answer never

Every lane emits two or three idle notifications per real report. The desk records the
first as `ledger.py enqueue --kind idle` and the tool drops the rest as duplicates. No
idle notice is ever answered: silence costs nothing, an acknowledgement costs a turn,
and a reply to a finished lane resumes it with its whole original brief.

## Routing, desk to lane

When a head reads red or conflicting, the desk sends the owning lane exactly this,
once per PR, head, and failing job, printed by `ledger.py route`:

```
DESK #<n> <head9>: <failing job, or "rebase onto <base>">
<the first failing line from the Buildkite log, when the desk read one>
Push the fix as a new head on the same branch, then send landing-desk the 3-line report
(PR, head, verdict); the merge label waits on that head reading green and merge-clean.
```

A second red on the same head with the same job is not routed again. A new head that
is still red is, and so is a different job on the same head.

## The custody check: content, never ancestry or patch identity

A lane force-pushing seconds after a label is the most expensive race at this desk, and
the two questions it raises both have a wrong instinct attached. Answer them with
content, in this order, and pull the label before diagnosing. Pulling is one reversible
call; landing a head whose plan was never graded is not.

**Ancestry cannot say which head the queue took.** The queue squashes, so the trial
merge has neither candidate head as an ancestor and `git merge-base --is-ancestor`
answers no for both. Find a line that exists in exactly one of the two heads and look
for it in the trial merge's tree.

```sh
git show <twin-sha>:<path> | grep -c '<line only the newer head has>'
```

**`git patch-id` cannot say whether the new head is the same work.** It hashes
surrounding context, so a rebase changes it even when the change is byte-identical.
Diff the two heads restricted to the pull request's own files.

```sh
FILES=$(git diff --name-only $(git merge-base $NEW origin/<trunk>) $NEW)
git diff --stat $OLD $NEW -- $FILES      # empty means the payload is unchanged
```

An empty result means a pure rebase, so re-label the current head and nothing was at
risk. A non-empty result means the payload moved, so the head needs grading again
before the label returns.

**The pull request page cannot say whether it landed.** It reads `closed` with `merged`
false on everything the queue lands, and the head is not an ancestor of the trunk.

**The trunk log cannot say it either, and it fails silently.** Searching the trunk log
for the number is the instinct, and it produces false negatives from two independent
mechanisms. A stacked child can land its parent's payload, after which the parent merges
as a no-op and no commit ever carries its number: one desk read a parent as unlanded for
half an hour after the child's squash had already delivered every byte of it. And a
shallow clone truncates the traversal without an error, at a depth that changes with
whatever the last fetch happened to deepen, so the same grep answers differently minute
to minute.

Ask the tree, then the forge. An empty two-dot diff over the pull request's own files
means the trunk holds that content, whatever any log, page or ancestry says.

```sh
files=$(gh api "repos/<repo>/pulls/<n>/files?per_page=100" --jq '.[].filename')
printf '%s\n' "$files" | tr '\n' '\0' |
  xargs -0 git diff --numstat origin/<trunk> <head> --      # empty means it landed
```

Two-dot, never three-dot: three dots diff against the merge base and would show the
payload as present on the branch side no matter what the trunk received. Restrict to the
pull request's own files, because the trunk moves under everything else.

**A difference is not an answer.** Tree equality proves a landing; nothing proves the
absence of one from content alone. Two pull requests that had already landed read as
unlanded minutes later, because the trunk moved on one of their files in between, and
because a squash onto a moved trunk merges the branch with the trunk, so the result
equals neither side for a file both touched and the head's blob never appears in the
trunk's history at all. Checking whether the head's blob ever appeared does not rescue
it; that was tried and it failed on the same pair.

So when the tree differs, ask the forge, and ask for the queue's own mark rather than
for the closing actor. The actor proves nothing: the queue's bot also closes a stacked
child when its base branch is deleted, landing nothing. One desk read such a child as
landed while its one-line fix was still absent from the trunk, which retires the row and
guarantees nobody reopens the pull request.

```sh
gh api "repos/<repo>/issues/<n>/labels" --jq '.[].name' | grep -qx externally-merged
```

A squash the trunk log names by number counts too. Nothing else does.

Content still answers the one thing the forge cannot see: a stacked child carrying its
parent's payload, where the parent merges as a no-op and its own page shows only that
the queue closed something. Neither source is sufficient alone, and the order matters,
because the tree is cheap and certain when it agrees.

**Pulling a label is a request, not a stop.** The queue may already hold the entry, and it
lands on its own schedule minutes later. So the desk pulls, then reads the trunk tree, then
says what happened, and never reports a pull as an outcome. The desk also re-labels nothing
until that content check passes, because the pull request it is about to re-label may have
landed while it was diagnosing. One desk pulled at 01:10, reported that nothing landed, and
re-labelled at 01:13, one minute after the queue merged the payload it had tried to stop.

Two rules follow from the same mechanism. A pull request body is corrected before the
label goes on and never after, because the squash takes the body as it stands at merge
time and discards the branch commit message. A body edit does not move the head, so it
never conflicts with leaving the branch alone. And when two open pull requests touch the
same lines, the hold goes on immediately and the ownership question is asked from behind
it, because the queue does not wait for a ruling.
