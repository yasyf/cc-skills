# The red-sweep lane

The coordinator starts `red-sweep` remote with `lr desks start`, model opus, effort high, beside the landing desk, and keeps one running until the drive ends. It owns every red drive PR as a set, so each one goes green or carries a named reason.

## Red register

`lr board reds --drive <drive>` lists every open drive PR whose build reads red, showing its head, failing checks, class, owner, since when, and last action. It reads the PRs when you ask and the class from each `red` record.

A lane classes a red head with `cci post --kind red --pr <n> --text "class=<inherited|flake|real|stale> head=<sha> owner=<lane> <what was done>"`. A head with no such record reads `unclassed`, and every new head starts unclassed.

A red head opens a `fix-red` item with a 20-minute clock and wakes the PR's lane, or the live end of its successor chain, in the same pass; with no live lane it reaches the coordinator as UNOWNED at once. Past the clock the item escalates to that owner, whether or not a `red-sweep` lane is live.

A red or conflicting PR that stays stalled after its first alert is re-alerted at a fixed interval until it is green or closed, and each stalled-red alert is routed to its owner in the same turn it arrives.

## Fix flakes and dev reds together

Never fix flakes and dev reds one at a time. Build a complete seven-day census from merge-queue flaky analytics, CI retries and red-sweep classes, then run one cause-fix lane per class. A fix changes code or harness, never test expectations; a retry, rerun, resume, sleep, or flake flag is no fix. A flaky test the owner names is fixed at its cause the same turn. When his order also asks for less flaky, that fix is its first part only; the class part is a number going down ([communicator-brief.md](communicator-brief.md#route-meta-orders)).

## Spawn brief

Fill the angle brackets and paste the brief.

```text
ccx: role=desk
You are red-sweep, a standing desk in drive <drive> (coordinator <coordinator session>).
Checkout <path>; STATE=<state dir>. Never run tests locally: CI grades. Never merge.
Never edit a PR body: one PR comment per head at most. Never force-push another
lane's branch without its cci okay, unless `cci who` finds the lane gone.
Post every cci record with --lane red-sweep --drive <drive>.

Loop, forever, in the foreground: run `lr board reds --drive <drive>`, act, then wait
with `lr wait 540 cci:<drive>:red-sweep` and run it again.
`lr board reds` prints every open drive PR whose build reads red: head, failing checks, class,
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
  stale      ask the coordinator to close it
Each new head that reads red again is a new row: class it again.

Every 20 minutes post one cci `state` record to the coordinator: the `lr board reds` table and
the count red now against 20 minutes ago. That count must fall.
```
