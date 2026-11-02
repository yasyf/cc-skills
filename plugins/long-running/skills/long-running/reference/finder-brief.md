# The finder brief

Every drive head gets one finder pass: one `gpt-6.1-sol` reader at xhigh on the
standard tier, run through `Skill(codex)` with its finder recipe
(`codex-ask --schema findings`). The finder looks for defects in the diff, the way
any finder does, and it also reads the head against the same rows the
[fidelity desks](fidelity-desk-brief.md) read, returning one verdict per row. A
row it finds `missing` is a major. With a `finder` object in `runner.json`, the orca
desk runner runs the pass on every `head` record and posts its record under lane
`finder-desk`, from [finder-desk-brief.md](finder-desk-brief.md). The lane fixes or rebuts
each major in the PR; the landing desk reads only `finder-desk`'s own `finder:` records,
never a lane's.

## What the finder receives

Codex reads neither the hooks nor the register, so the prompt carries everything by value:

- The head: repository, PR number, full head sha, base branch, and the command
  that prints its diff.
- Every row `rulings.py slice --pr <owner/name>#<n> --head <sha> --base <base> --repo <checkout> --required`
  prints (every global, pending, and matched row), each with its key, the owner's
  words verbatim, its implications with their `removes`, `requires` or `permits` mark and
  status, and its names. Paste them as the register holds them, never summarized.
- The PR body and its author's comments, the `## Rulings` and `## Regression tests`
  comments included, as claims to check.
- The plan's path and the paths of the sub-plans the slice names, so the finder can
  cite the line each added or changed test rests on.

## What it returns

The reply follows the `findings` schema, so the codex plugin needs nothing new:

- Each row with verdict `missing` is one finding of severity `major`, its `cite` the
  file and line, its `defect` starting `row <key> missing:` and then what the code
  keeps, offers, recommends, budgets, or tests that the words ban.
- `swept` names every other row: `rows met: <key>,<key>...; n/a: <key>,<key>...;
  pending: <key>,<key>...`, then the files it read. `met` means the head does what
  the words say; `n/a` means the head touches nothing the row covers; `pending` lists
  each `pending-owner` row, which gets no verdict of its own. A head that needs a
  pending row in force is read against the rows it would have excepted.
- Defect findings keep the schema's severities. A `blocker` counts as a major and a
  `nit` as a minor.
- Slow code is a defect. The finder asks what the change runs per chat, team, session,
  or run at 1,000 of them, and grades major, its `defect` starting `slow at N:`, a loop
  that awaits I/O per item, a concurrency cap standing in for a batch, a process, timer,
  or poll per item, a lock or wait wider than the one resource it protects, a poll where
  a stream or signal exists, a cache, cap, timer, retry, or idle wait whose PR body names
  no fundamental fix it stands in for, local state in a JSON file, or a branch on whether
  required configuration is set.
- A change to a request or response shape between two separately released components
  (GraphQL inputs and fields, REST bodies, the SandSQL wire, signal payloads) must work against the old and the new peer: the reader ships first and
  accepts both shapes, and the writer switches a release later (the owner's Oct 29 pick on
  card-wire-order, rule 7ab71e2). A writer that sends a shape the released reader rejects,
  such as a new input field sent on every call, is a major, and so is a reader that stops
  accepting a shape a released writer still sends, such as a resolver that now refuses a
  call older clients make. A removal of an accepted shape stays a major until every
  installed client is past it, which the PR body shows by the release that carried the new
  shape to them. The dual-shape reader the rule asks for is never a compat layer.
- Shared infrastructure is old-tree surface. A head changes it when it edits a row, manifest
  or policy the old tree's production workloads run under or reach through, or points one of
  them at another service, fleet or endpoint: in Forge-AI/monorepo, anything under
  `infra/rows/` that renders a NetworkPolicy, a Namespace or its labels, an Istio
  AuthorizationPolicy or PeerAuthentication, a ServiceAccount, a Service, DNS or a route the
  executors, the api deployments, the restate workers or sanddb use, and any api or runner
  change that moves an old-tree call onto another fleet, as #46834 moved run journals onto
  the system SandSQL fleet. Such a head must name, in its PR body, the old-tree workloads it
  affects, and carry a test that renders the change on every cluster config, plat, staging
  and each tenant, such as `infra/rows/k8s/old-tree-reach.test.ts` and its snapshot. A head
  that changes it without both is graded major, its `defect` starting `old-tree infra:`, and
  a test that renders one cluster where the row differs per cluster asserts less than it
  claims.
- A major cites at least one `path:line` inside a hunk the diff adds or changes
  (`git diff --unified=0 origin/<base>...<sha>`). A finding that rests only on lines
  the diff leaves as they were is a census finding, its defect starting `census:`: the
  lane appends it to `<drive scratch>/finder-desk/census-<YYYY-MM-DD>.md` with the PR,
  head, cite, and claim, and posts one `finder census:` note to root, never a major.
- Every assertion the diff adds or changes in a test asserts only behavior a plan
  line, sub-plan line, or owner ruling names (e20e623). `swept` cites that line for
  each such test as `tests: <test> <- <plan cite>; ...`. An assertion that pins
  drive-era behavior the plan removes, or behavior nothing names, is one finding of
  severity `major`, its `defect` starting `test <name> pins unplanned behavior:`. A
  test whose only change is a renamed fixture, constructor, argument or import is
  graded on those lines alone: its untouched assertions are dev's, so a finding on
  them is a census finding, never a major on the PR (root #208116). A port is a head whose PR
  body, or fidelity grade, says it ports an old-tree PR by number. On a port, a test
  that asserts what a test of that old-tree PR asserts is graded against the old-tree PR,
  the contract (plan 0.4; rule 784f1ab: a port keeps its regression tests), cited in
  `swept` as `tests: <test> <- old-tree #<n>`, and raises no such finding. A test with no
  counterpart there, or asserting more than its counterpart, is graded against the plan.
  The same holds for a test the PR body or a PR comment under a `Rulings` heading ties to
  the finder or review-bot finding it pins, or to the old-world twin PR it mirrors: it is
  cited as `tests: <test> <- <finding or twin>` and checked only against what that source
  fixed.

Every key the slice printed appears exactly once, as `met`, `n/a`, `pending`, or a
`missing` finding. A reply that drops a key is no verdict: run the pass again.

## The record

The finder desk posts the verdict as the pass returned it, exactly as cci #110240 item 3
fixed it; only these records grade a head (root #202594):

```bash
cci post --drive <drive> --lane finder-desk --kind review --pr <owner/repo>#<n> --text "finder: ok <head7> <one line>"
cci post --drive <drive> --lane finder-desk --kind review --pr <owner/repo>#<n> --text "finder: major <head7> <count> open: <one line per major>"
```

`<count>` is the number of open majors, row `missing` findings included. The one
line per major names its cite and defect, and `; ` separates majors so the record
stays one line. Minors go in the PR body, never in the record.

The newest finder record that names the head decides. Two things answer a major:
a new head, which gets its own pass, or a later
`finder: ok <head7> answered: <how>` that a finder pass posts after it reads the
fix or the rebuttal in the PR. A lane never posts `answered` on its own word.

## What the landing desk does with it

The landing desk refuses a head with no finder record, or whose newest record is
`finder: major`, with `FINDER-PENDING`, and `rulings.py check-pr` reports the open
major as `FINDER-MAJOR <head7>`. An incident fix never waits for the finder: the
pass runs within 30 minutes after it merges (d968e1d, option (a)), and the runner escalates a merged head with no
finder record at 30 minutes as `UNGRADED-30`.

## Prompt

Fill the angle brackets, paste the slice rows whole, and pass the prompt to
`codex-ask --schema findings` through `Skill(codex)`.

A lane never runs its own pass. It posts an `ask` to `finder-desk` naming the PR and
head, and the desk starts that whole pass ahead of every other head.

```text
You are the finder for <owner/name>#<n> at head <full sha> on base <base>.
Read the head pinned by sha, never the PR's moving tip: `git fetch origin <full sha> <base>`,
then `git diff origin/<base>...<full sha>`. Read the code around every changed symbol at
that sha, its callers and callees included.

Do three things and report all three in the findings schema.

1. Find defects in the diff: wrong results, broken callers, lost data, races,
   tests that assert less than they claim. Rate each blocker, major, minor, or nit.
   A change to a request or response shape between two separately released components
   (GraphQL inputs and fields, REST bodies, the SandSQL wire, signal payloads) must work against the old and the new peer
   (rule 7ab71e2): a writer that sends a shape the released reader rejects, or a reader
   that stops accepting a shape a released writer still sends, is a major, and a removal
   of an accepted shape stays one until every installed client is past it.
   Shared infrastructure is old-tree surface. A head changes it when it edits a row,
   manifest or policy the old tree's production workloads run under or reach through, or
   points one of them at another service, fleet or endpoint: in Forge-AI/monorepo, anything
   under `infra/rows/` that renders a NetworkPolicy, a Namespace or its labels, an Istio
   AuthorizationPolicy or PeerAuthentication, a ServiceAccount, a Service, DNS or a route
   the executors, the api deployments, the restate workers or sanddb use, and any api or
   runner change that moves an old-tree call onto another fleet, as #46834 moved run
   journals onto the system SandSQL fleet. Such a head must name, in its PR body, the
   old-tree workloads it affects, and carry a test that renders the change on every cluster
   config, plat, staging and each tenant, such as `infra/rows/k8s/old-tree-reach.test.ts`
   and its snapshot. A head that changes it without both is a major, its defect starting
   "old-tree infra:", and a test that renders one cluster where the row differs per cluster
   asserts less than it claims.
   A major cites at least one `path:line` inside a hunk the diff adds or changes; a
   finding on lines the diff leaves as they were starts its defect with "census:".

2. Read the head against each owner row below. For each row, ask whether any line
   of the head keeps, offers, recommends, budgets, or tests what the words ban,
   under any of its names, and whether the code does what the words say. Read the
   words and implications, never a summary of them; the PR body's claims are claims
   to check. A deletion is judged on what it deletes: read every caller of the
   removed path. A new refusal kind is not a fix. An edited named test is judged
   against the assertion its row states. Drive-era code is what the drive-era code
   does today, never a requirement.
   For each row give exactly one verdict:
   - missing: one finding, severity major, cite file:line, defect starting
     "row <key> missing:".
   - met or n/a: list it in `swept` as "rows met: <keys>; n/a: <keys>; pending: <keys>".
   - pending: a row marked pending-owner gets no verdict; list it under pending, and
     read a head that needs it in force against the rows it would have excepted.
   Every key below appears exactly once.

3. Read every test the diff adds or changes. A test may assert only behavior a
   line of the plan, a sub-plan, or an owner ruling names. Cite that line for each
   test in `swept` as "tests: <test> <- <plan cite>; ...". A test that pins
   drive-era behavior the plan removes, or behavior nothing names, is one finding,
   severity major, defect starting "test <name> pins unplanned behavior:".
   A port is a head whose PR body, or fidelity grade, says it ports an old-tree PR by
   number. On a port, a test that asserts what a test of that old-tree PR asserts is
   graded against the old-tree PR, the contract (plan 0.4; rule 784f1ab: a port keeps its
   regression tests): cite it as "tests: <test> <- old-tree #<n>" and raise no finding for
   it. A test with no counterpart there, or asserting more than its counterpart, is graded
   against the plan as above. The same holds for a test the PR body or a PR comment under
   a `Rulings` heading ties to the finder or review-bot finding it pins, or to the
   old-world twin PR it mirrors: cite it as "tests: <test> <- <finding or twin>" and check
   only that it asserts what that source fixed.

Plan: <plan path>; sub-plans: <paths>

PR body:
<the PR body, verbatim>

Rows (`rulings.py slice --required` for this head, slice-hash <sha12>):
<for each row: key, words verbatim, implications with removes/requires/permits and status, names>
```
