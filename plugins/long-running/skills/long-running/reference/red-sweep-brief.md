# The red-sweep lane

The coordinator starts `red-sweep` remote with `lr desks start`, model opus, effort high, beside the landing desk, and keeps one running until the drive ends. It owns every red drive PR as a set, so each one goes green or carries a named reason.

## Red register

`drive.py reds --drive <drive>` lists every open drive PR whose build reads red, showing its head, failing checks, class, owner, since when, and last action from the `red` objects the stall watch writes into `stack.json` each pass.

A lane classes a red head with `cci post --kind red --pr <n> --text "class=<inherited|flake|real|stale> head=<sha> owner=<lane> <what was done>"`. A head with no such record reads `unclassed`, and every new head starts unclassed. While dev reads `DEV-RED`, the landing runner posts that record itself with class `inherited` and dev's fix as the unblock for any red head whose failed Buildkite jobs all fail on dev too, so no lane classes it by hand. The dashboard's failing-tests count names the lane holding each red.

The stall watch raises the `unclassed`, `unfixed` and `sweep` alarms to the coordinator when `red-sweep` falls behind or is missing; their thresholds are in [watchers.md](watchers.md#run-the-stall-watch).

## Stalled-red sweep script

On a drive without a desk, `label-watch.sh watch` only reprints a line when a PR's result changes. `scripts/red-sweep.sh` reads the same list file `label-watch.sh` watches and re-alerts once per head on any entry stuck `CONFLICT`, `NOT-READY`, or `EVICTED` past `RED_SWEEP_AGE_MINUTES` (default 20).

```
STALLED-RED #<n> <sha> <status>/<mergeable_state> head <age>m old: <title>
```

A red head whose every failing status is a Buildkite build, where each failed step also failed on the trunk's latest finished build of that pipeline, prints `DEV-RED #<n> <sha> <step>,<step>` instead, once per head while the trunk stays red. Once the trunk's step passes, a head still red prints `STALLED-RED`. `RED_SWEEP_TRUNK` names the trunk, by default the checkout's `origin/HEAD`.

Run `scripts/red-sweep.sh` beside `label-watch.sh watch`, pointed at the same list file, as its own background sweep. It never edits the list, so `label-watch.sh` still removes an entry once it lands or gets skipped.

The coordinator classifies each output line first.
- Hold a `DEV-RED` line as `dev-red:<step>` instead of routing it.
- Route every `STALLED-RED` line to the lane that owns the PR by `SendMessage` in the same turn, with a 15-minute deadline, because an idle lane never sees CI. A red whose owner is gone gets a new lane at once, or goes to the train or red desk under D8 in [landing.md](landing.md).

## Spawn brief

Fill the angle brackets and paste the brief.

```text
ccx: role=desk
You are red-sweep, a standing desk in drive <drive> (coordinator <coordinator session>).
Checkout <path>; STATE=<state dir>. Never run tests locally: CI grades. Never merge.
Never edit a PR body: one PR comment per head at most. Never force-push another
lane's branch without its cci okay, unless `cci who` finds the lane gone.
Post every cci record with --lane red-sweep --drive <drive>.

Loop, forever, in the foreground: run `drive.py reds --drive <drive>`, act, then wait
with `lr wait 540 cci:<drive>:red-sweep` and run it again.
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
  stale      ask the coordinator to close it
Each new head that reads red again is a new row: class it again.

Every 20 minutes post one cci `state` record to the coordinator: the `drive.py reds` table and
the count red now against 20 minutes ago. The owner reads the dashboard, whose
failing-tests count names who holds each red; that count must fall.
```
