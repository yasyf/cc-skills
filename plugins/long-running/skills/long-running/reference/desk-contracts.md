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
git show <twin-sha>:<path> | grep -q '<line only the newer head has>' && echo took-newer
```

**`git patch-id` cannot say whether the new head is the same work.** It hashes
surrounding context, so a rebase changes it even when the change is byte-identical.
Diff the two heads restricted to the pull request's own files.

```sh
git diff --name-only $(git merge-base $NEW origin/<trunk>) $NEW > /tmp/files
while IFS= read -r f; do git diff --stat $OLD $NEW -- "$f"; done < /tmp/files
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

So when the tree differs, ask the trunk again, never the forge. **Nothing the forge
asserts about itself is admissible here.** The closing actor proves nothing, because the
queue's bot also closes a stacked child when its base branch is deleted, landing nothing.
The queue's own merged label proves nothing either: it is applied to pull requests that
are still open and whose content never arrived, two of which carried it while their trees
differed and no commit named them.

Both of those were adopted as the fix for the previous one. The rule that survived is
that only the trunk answers, through its tree or through a commit it names:

```sh
git log origin/<trunk> --oneline -400 --fixed-strings --grep="(#<n>)"
```

One more trap sits under this. If the pull request's base branch has been deleted,
`origin/<base>` does not resolve, git diffs nothing, and an empty diff reads as a
landing. Verify the base ref exists before believing an empty result, or a stacked child
whose parent landed will read as landed itself while its payload is still missing.

Content still answers the one thing the forge cannot see: a stacked child carrying its
parent's payload, where the parent merges as a no-op and its own page shows only that
the queue closed something. Neither source is sufficient alone, and the order matters,
because the tree is cheap and certain when it agrees.

## Retarget a child before labelling its parent, never after

When a parent lands, the queue deletes its branch and the forge closes every pull request
based on it. Reopening is refused outright:

```
state cannot be changed. The <branch> branch has been deleted.
```

The tempting fix is to retarget the child the instant the squash appears. That is a race
measured in seconds, and a poll does not win it: one desk watched at thirty-second
intervals, got `HTTP 422` on its retarget, and lost a pull request whose one-line fix was
still absent from the trunk.

**So the ordering is the fix, not the reaction.** A child's base moves to the trunk before
its parent is ever labelled, at which point the parent's branch deletion touches nothing.
The desk enforces this by refusing to label a pull request whose branch is still the base
of an open one, naming the children in the refusal. Retargeting is cheap and reversible;
a closed child is neither.

Retargeting alone does not rebase. A child moved onto the trunk still carries its parent's
commits and will re-show that diff until it is rebased, so check the file count before
labelling: one file where one belongs, not sixteen.

## A neutral check is a held finding, not an abstention

`ai-review` reports `neutral` when it holds a pull request on a blocking finding. That is
not a failure, so a sweep for failed check runs misses it, and it is a check run rather
than a status, so the combined status misses it too. The pull request reads green
everywhere while `mergeable_state` sits at `blocked` indefinitely.

One desk labelled on that surface and the pull request sat unlandable for five hours,
having never entered the queue at all. Across that board the correlation was complete:
every pull request that landed had `ai-review` at success, and the only one at neutral was
the only one blocked.

Require success explicitly. The reason for a hold is always a review comment on the diff,
so surface that rather than the check's state, and never clear it by re-running the review.

## The label vanishing means enqueued, ejected, or nothing; the activity comment decides

The queue bot removes the label both when it accepts a pull request and when it rejects
one, within seconds either way, so the removal carries no information. Read the **Merge
activity** comment instead, which the queue writes on the pull request under the human
account rather than the bot account, so a filter on the bot author finds nothing and the
desk wrongly concludes there is no queue record at all.

- `added this pull request to the merge queue` and no later line: enqueued, waiting for a
  batch. A removal seconds after the label went on is the queue consuming it.
- `couldn't merge this PR because it had merge conflicts`: ejected, and the head needs a
  rebase before the label returns.
- the `detected` line with no `added` line, ever: **the label was never consumed.** The
  pull request is not in the queue and nothing will happen to it.

That third state is silent and indefinite. One green pull request sat in it for over
ninety minutes, mergeable clean, both builds success, review approved, in no twin, while
the queue enqueued others and produced a batch every one to two minutes.

**Contract: a labelled pull request with no `added` line after twenty minutes is wedged,
and the desk acts rather than waits.** In order, stopping at the first that works:

1. **Check how stale the head is, before anything else.** A branch far enough behind the
   trunk can be green on its own checks and still be un-enqueueable, and this is the most
   common cause by a wide margin. If `mergeable_state` is `dirty`, or the head is many
   commits behind, the answer is a rebase and none of the steps below are needed.
2. Re-request the queue's own mergeability check run over REST. Expect this to fail with
   404 when the check belongs to another GitHub App, because the token cannot re-run
   another App's check; that is not a misconfiguration, it is the normal answer.
3. Pull the label and re-add it once, then wait five minutes.
4. Pull the label, then ask the **owning lane** to rebase onto the trunk tip and re-record
   the Graphite parent. The desk does not push to a lane's head for a fault that is not in
   their pull request, and the label comes off first so nothing is consumed mid-push.

Step 1 is first because of how the one worked case actually resolved. A pull request sat
unenqueued for over ninety minutes; the label toggle did nothing, the check re-request
returned 404, and an empty commit did not move it either and left the branch `dirty` as
the trunk kept moving. A plain rebase onto the trunk tip fixed it: the queue's own
mergeability check then ran and passed on its own, and the queue accepted the label within
a minute. An empty commit is not a cheaper rebase; it re-runs CI against the same stale
base and tells you nothing the rebase would not have told you.

Escalate to the owner only if all three fail. A wedged queue entry is a mechanical lever,
not a decision.

## Prove a landing positively, never by the absence of a diff

Every check above compares the trunk against a head and reads an empty result as "the
same". That shape has a failure mode that fires on exactly the input it exists to catch,
because three ordinary things all produce empty output with a zero exit status: a pathspec
that matches no file, a command that errored with stderr redirected away, and genuinely
identical content. Only the third is a landing, and nothing downstream can tell them apart.

The pathspec case is the one that actually fired. A desk graded twelve pull requests with

```sh
FILES=$(gh api ".../files" --jq '.[].filename' | tr '\n' ' ')
[ -z "$(git diff origin/dev "$SHA" -- $FILES)" ] && echo LANDED
```

and reported a pull request as landed while all ten of its files differed and both of the
files it adds were absent from the trunk. The agent shell is zsh, which does **not**
word-split an unquoted `$FILES`, so git received one 216-character pathspec matching
nothing, compared nothing, printed nothing and exited 0. The identical line under bash
emits 14884 bytes. A `#!/bin/bash` script run as a file was unaffected; only the inline
command broke. Loop one path per call, or use `${=FILES}`, and never join paths into one
unquoted word.

`grep -c` has the mirror defect: it prints `0` **and** exits 1 on no match, so
`c=$(... | grep -c X || echo 0)` yields two lines, `[ "$c" = 0 ]` is false, and every
absent symbol reads as present. Use `grep -q` for presence.

So the desk does not conclude a landing from an emptiness test. It requires one of two
positive readings, both of which are impossible to fake by accident:

- a file the pull request **adds** exists on the trunk, `git cat-file -e origin/<trunk>:<new-path>`
- the trunk carries a squash naming the number, `git log origin/<trunk> --oneline --grep='(#N)'`,
  valid only once `git rev-parse --is-shallow-repository` reads false

Tree equality stays useful as corroboration and is still the only signal that catches a
stacked child carrying its parent's payload. It is no longer sufficient alone.

Before trusting any new check, feed it one input known to be absent and confirm it says
so. A check that has never been observed failing has not been tested.

## Refuse to grade rather than grade from a broken instrument

Two conditions make every answer about the trunk unreliable, and both are silent.

A **shallow clone** truncates history traversal at a depth that moves with each fetch, so
the same query answers differently minute to minute and succeeds on recent landings while
failing on older ones. `git rev-parse --is-shallow-repository` is one word and settles it.

A **failed fetch** leaves the previous state in place, so a pass that continues grades
yesterday's trunk while reporting it as today's. Worktrees share a ref lock, so a
concurrent fetch in another lane is enough:

```
error: cannot lock ref 'refs/remotes/origin/dev': is at <a> but expected <b>
```

In both cases the desk writes nothing and says why. A pass that grades nothing is
recoverable; a pass that grades wrongly is not.

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
