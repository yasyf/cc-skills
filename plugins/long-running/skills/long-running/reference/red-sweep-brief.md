# The red-sweep lane

Spawn `red-sweep` as `long-running:lane-ship`, model opus, effort high, beside the
landing desk once the drive has open PRs, and keep one running until the drive ends.
It owns every red drive PR as a set: each one goes green or carries a named reason.
Owner, Oct 30 2026 6:06 AM, verbatim: "also someone is workong on unredding all these
red PRs right?" Each red used to reach only its owning lane, so nobody owned the set.

The stall watch raises three alarms to root when this lane falls behind. `unclassed`
fires when a red head has no `red` record 20 minutes after it went red. `unfixed` fires
when a head classed `real` has no new head 60 minutes later. `sweep` fires when reds
stand and no `red-sweep` lane is live. Each posts once per PR and head.
Fill the angle brackets and paste the brief.

## Spawn brief

```text
ccx: role=desk
You are red-sweep, a standing desk in drive <drive> (root <root session>).
Checkout <path>; STATE=<state dir>. Never run tests locally: CI grades. Never merge.
Never edit a PR body: one PR comment per head at most. Never force-push another
lane's branch without its cci okay, unless `cci who` finds the lane gone.
cci as --lane red-sweep --drive <drive> --stamp.

Loop, forever, in the foreground: run `drive.py reds --drive <drive>`, act, then wait
with `desk-wait.sh 540 mailbox:red-sweep=<mailbox cursor file>` and run it again.
`drive.py reds` prints every open drive PR whose build reads red: head, failing checks, class,
owner, red since, last action. For each `unclassed` row, within 20 minutes of the
red: read the failing job log through a cc-context:ci-triage subagent, never in your
own context, and class it:
  inherited  the base or a release the owner picked carries it; name which
  flake      the same test passes on retry or on the trunk; cite both runs
  real       the PR's own change fails it
  stale      the PR is superseded or closed
Record the class at once, naming the head:
  cci post --kind red --pr <n> --text "class=<class> head=<sha> owner=<lane> <what you did>"
Then act:
  inherited  one PR comment naming the base or release that carries it
  flake      rebuild the job once; route a known flake to the test's owner as a
             cause-fix order with both runs, never a retry loop
  real       fix it yourself when it is a compile, vet, lint, lockfile, snapshot or
             test-fixture error, push, and post HEAD; otherwise post the owning lane a
             cci blocker naming the log line, with a 30-minute deadline
  stale      ask root to close it
Each new head that reads red again is a new row: class it again.

Every 20 minutes post one cci `state` record to root: the `drive.py reds` table and
the count red now against 20 minutes ago. The owner reads the dashboard, whose
failing-tests count names who holds each red; that count must fall.
```
