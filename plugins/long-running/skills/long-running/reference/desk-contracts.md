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

## A pending-owner stack with no state renders no plan, so do not gate on one

The desk's default bar is "grade the plan artifact, never the lane's reported op counts".
That bar assumes an artifact exists. For a stack whose enablement sidecar still reads
`pending-owner` **and** which is absent from the backend, one never will:

```ts
if (written.skipped !== undefined) {
  invariant(written.skipped === PENDING_OWNER, ...)
  return { stack, outcome: "pending-owner" }
}
```

The plan exits 0 and writes `skipped` with **no ops and no digest**. Demanding a plan
comment for a rows-or-records PR against such a stack demands something the pipeline
cannot produce. The lane then stalls, or hands over hand-run numbers, which is the input
the bar exists to reject.

Both halves of the condition are load-bearing. The skip is guarded on the enablement being
held **and** the stack not already existing in the backend, so a held stack that already
carries state previews normally, with full ops and a digest. Check the backend before
concluding that a hold is why no plan rendered, and never cite the hold to justify flipping
a stack that has state: the preview is already free there, and the flip buys the grade
nothing.

**So the enablement flip belongs IN the PR the desk grades, not in a follow-up.** The plan
is computed from the branch's own tree. A branch that carries `apply: enabled` renders a
plan for that stack on its own infra-report build, which is exactly what makes the rows
gradeable before anything lands.

Splitting them inverts that and is the mistake to avoid. Rows alone land ungraded, because
no plan can render while the sidecar holds; the later enable PR then renders a plan whose
ops describe content that is **already on the trunk**, so the import-class bar is applied
after the fact to something it can no longer refuse. Two small PRs feel safer and are
strictly worse.

Where a no-plan PR is unavoidable, the substitute bar is green CI, ai-review success, and a
diff carrying no `.apply.yaml` change, which is one `gh api .../files` away and is what makes
it unable to cause an apply. Treat that as the degraded case rather than the target.

Distinguish this from a plan that **errored**. `could not be previewed` with a real error is
evidence of a problem and a hold. `pending-owner` is evidence of nothing and is not.

## `protect` aborts the preview, so the state edit comes before the grep

A retained delete is caught by grepping a preview for `[retain]`. That works only when a
preview exists. If the resource also carries `protect`, the preview **aborts**:

```
error: Preview failed: resource "<urn>" cannot be deleted because it is protected.
Resources: 32 unchanged, 3 errored
```

There is no op list to grep, so a desk that says "grep first, then edit the state" has
given an impossible instruction, and a lane that reports "zero retained deletes" from an
aborted preview has reported a number that could not have been anything else.

The order is: clear `protect` and `retainOnDelete` in one state edit, re-preview, then grep.
**The edit is the operative step and the grep confirms it worked**, not the reverse. A zero
before the edit means the preview never ran; a zero after it means the ops really are plain
deletes.

The same abort is what a landing shows when an environment's rows leave while protected
state entries remain, so a desk triaging a stuck `land` job should check for `protect`
before assuming drift or a bar refusal.

## A build's colour is not a job's verdict, in either direction

The desk enforces one half of this constantly: a green landing build does not mean a stack
applied, so read the job's own output. The other half is the same rule and gets forgotten,
because it points the way you do not want to go.

**A red build is not evidence that a job failed, or that its reading is void.** Jobs are
per-stack and independent. A build that fails on one stack says nothing about a different
stack whose job passed and printed its plan.

A gate worded "a landing reads stack X same" is answered by X's job, not by the build. When
a lane asks whether a red build can satisfy it, the test is whether the failing job shares
anything with the question: same stack, same domain, same credential path, same cause. If it
shares nothing, the reading stands.

```
:pulumi: land plat-usw2-relay-dns   state=passed  soft_failed=false  exit=0
dns/plat-usw2-relay: same=6
```

That satisfied a hold on the relay stack while the build overall read `failing`, because the
build's only other failure was an unrelated stack blocked by two protected state entries.

Accepting a job's output when the build is green and refusing it when the build is red is
not a stricter gate. It is reading the build colour after all, selectively, which is the
thing the first half of the rule exists to stop. The practical cost is real too: a
build-level gate makes every hold hostage to any unrelated blocker in the same build.

## An op that removes access is graded on the line's provenance, not only its effect

The usual grade asks what a plan will do. For an op that removes or narrows
access, that question is not sufficient, because the same diff describes both a
hardening change converging and a hardening change being reverted. Only the
history tells you which.

A lane reported a plan removing federated trust from six roles in a privileged
account, traced it to a flag they believed was mis-ported, read live in four
accounts, and proposed clearing the flag. Every source agreed and the conclusion
was backwards: the flag had been added deliberately by a pull request that closed
exactly that hole, and the plan was the convergence that pull request promised.
The row was right and live was behind. One command settled it:

```sh
git log -L <line>,<line>:<file> <trunk>
```

**Three sources agreeing about what code does say nothing about who chose it or
why.** Mechanism is cheap to prove and answers the wrong question. So before
grading a plan whose ops remove trust, permissions, or policy statements, read
the commit that introduced the line the plan follows, and treat "the row says X
and live says Y" as an incomplete finding until someone has. The check costs
thirty seconds and inverts verdicts.

The desk runs this itself and does not rely on the lane having done it. A plan of
this class goes to the owner, not to a label: applying it is a decision about
intent, which no plan artifact settles.

**Make the trigger mechanical, because an access removal does not announce
itself.** The op that caused this reads `update`, on a role, and nothing in its
class says anything was taken away; the removal lives inside the document. A rule
that fires only when the grader recognises the shape fails on the night someone is
in a hurry, which is the night it matters. The op already carries the trigger: the
document appears by name in the `diffs` array. So fire the provenance check on any
op whose diffs name a trust or policy document, whatever the op's class and
however benign the content reads. Over-triggering costs thirty seconds; the
alternative depends on reading the content correctly first, which is the step that
failed.

Derive that key set from whatever map the engine already uses to recognise a
document input. Do not hand-copy one. The same map that decides an op is a
benign document rewrite then decides it needs the check, so the two cannot drift
and a newly added document-bearing resource type gets both behaviours at once.
Match on the key alone and not the key-plus-type pair the rewrite check uses: a
document key on a type nobody has classified yet is more reason to look, not less.

Know the boundary rather than trusting the trigger. Such a map covers policy
documents, so an access narrowing expressed another way, a principal list in a
plain input or a permission set, still passes silently. Keep those as a second watchlist
beside the derived set, labelled so it can never be mistaken for coverage.

## A check nobody has seen pass is indistinguishable from one that cannot

Before a verification is allowed to support a verdict, feed it an input whose
answer you already know and watch it give that answer. A check that has never
returned the passing result may be measuring nothing, and it fails silently in the
direction of whatever its broken form returns.

One night produced three of these on three unrelated surfaces. A document
comparison could never report equivalent, because the value under test mocks had
the wrong shape. An `AccessDenied` read as absence, so a resource that existed was
reported missing. And a `grep -c` with an `|| echo 0` fallback reported every
absent symbol as present, which is the input it existed to catch. One missing
habit behind all three.

So a check that gates a verdict runs its own control first. For an equivalence
check, compare a thing with itself and confirm it reports equal. For an existence
check, look for something known to exist. For a count, feed it a case whose count
you know. Say in the report that the control ran, because a reader cannot
distinguish a check that passed from one that could not fail.

**The same rule governs debugging a check, and that is the half people miss.**
Asked why a comparison returned a negative, the instinct is to enumerate the
reasons it might have and narrow by exhaustion. Each elimination buys one fact of
the form "this cannot be the cause". A single observed SUCCESS of the same
mechanism buys far more. It proves at once what the mechanism can parse, what
shape its inputs really take, and that it can return the passing answer at all.

One desk spent an hour on why a document comparison failed, eliminating six
candidates by reading both sides of the code, and never looked for an instance of
that comparison succeeding. One sat two builds away. It ruled out three standing
theories in a stroke and narrowed the question from the machinery to a single
resource. So before enumerating why a check failed here, find where it last
passed. If it has never passed anywhere, that is the finding.

## A plan's `diffs` and `digest` each hide something, in opposite directions

Two fields invite the same mistake, reading a summary as if it were the thing
summarised. Both cost a wrong verdict, one in each direction.

**`diffs` is the raw preview diff, not what the op applies.** A classifier that
subtracts the keys an adoption record pins in `ignoreChanges` before deciding the
op's class will leave those keys in the reported diff. So an op in an admitted
class can legitimately list a create-time argument the provider never returns.
That looks exactly like a non-tag change riding into an admitted class, which is
the thing to refuse, so read the classifier rather than the label: if the op could
only have been classified that way with the pin in place, the classification is
itself the evidence the pin took effect. Confirm the pin in the branch's own
record, then admit it.

**A matching `digest` is silent about provider ops, because the hash filters
them.** Where the digest is computed over the ops with `pulumi:providers:`
entries removed, two plans agreeing on it agree about resources and about nothing
else. This matters because a hand plan run under the writer role pins the
terraform role into the provider and reports those ops as `same`, while CI plans
under the planner and reports them as updates to the assumed roles. Same intent,
same digest, different provider classification. The filter is deliberate and
makes a hand plan and a CI plan of one intent agree regardless of role.

So when a lane's counts and the artifact's counts differ **only** on
`pulumi:providers:` entries, that is expected and not a discrepancy to chase.
Never argue a lane's counts down on the strength of a digest match: it cannot
arbitrate the provider rows, and doing so overrules someone who is right. Compare
provider ops by reading both lists.

## A landed tooling fix does not reach a branch that predates it

CI builds its own tooling from the branch under test, not from the trunk. So a bug fixed
and landed an hour ago is still live on every branch whose head does not contain the fix,
and those branches keep failing on it in exactly the shape they failed before.

This reads, from the desk, like a regression in the fix. Four PRs failing identically on a
defect the trunk no longer has is the signal, and the check is one command per head:

```sh
git merge-base --is-ancestor <fix-sha> <pr-head> && echo has || echo lacks
```

Run it before reporting a regression, before reopening the fix's own PR, and before
escalating. The remedy is a rebase by the owning lane, not a second fix.

One failure can also hide another. Where the pipeline asserts that every stack planned
before it renders the diagram, a stack that cannot preview fails the assertion and the
render step never runs. Clearing the earlier failure then surfaces the later one on the
same head. Tell the lane rebasing for one cause to expect the other, so a second error
does not read as a second problem.

## Destroying an environment before its rows leave reds every landing in between

A landing schedules a job per enabled stack and treats a stack with no state as one to
**import**. So when an environment is destroyed live while its rows are still declared in
the tree, every landing build after the destroy tries to import the whole environment back
from adoption records pointing at resources that no longer exist, and dies in preview:

```
aws:ec2:Vpc  <env>  import error: Preview failed: resource 'vpc-0c47b1e6127bcc069' does not exist
pulumi:pulumi:Stack  network-<env>  create error: preview failed
```

Nothing is at risk, because every one of these fails in preview and writes nothing. What is
lost is the pipeline: on 2026-09-17 the trunk landing pipeline went **40 builds with zero
passes** across 95 minutes, 27 outright failed, and every hard failure in the sample build
was a `land <staging stack>` job, nine of nine. No stack anywhere in the estate applied
during that window, so every other lane's landing silently waited too.

**The ordering rule: the row removal lands first, or in the same window as the destroy.**
Never destroy live and let the rows follow. The window between the two is exactly as long
as the outage.

Reading it from the desk: the give-away is a landing build whose hard failures are all
`land` jobs for one environment, all reporting `does not exist` on import. Count the
environment's jobs per build to see the clearing, since each row-removal PR that lands drops
its share. Recovery needs no intervention, only the row PRs; the first build after the last
one is the first that can pass.

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
