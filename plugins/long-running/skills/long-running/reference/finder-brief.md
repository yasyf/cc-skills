# The finder brief

- [What the finder receives](#what-the-finder-receives)
- [What the finder returns](#what-the-finder-returns)
- [Post the finder record](#post-the-finder-record)
- [How the landing desk uses the grade](#how-the-landing-desk-uses-the-grade)
- [Prompt](#prompt)

Every drive head gets one finder pass from one `gpt-6.1-sol` reader at xhigh on the standard tier, run through `Skill(codex)` with its finder recipe (`codex-ask --schema findings`). The finder looks for defects in the diff and reads the head against the same rows the [fidelity desks](fidelity-desk-brief.md) read, returning one verdict per row. A row it finds `missing` is a major.

With a `finder` object in `runner.json`, the relay runs the pass on every `head` record and posts its record under lane `finder-desk`, from [finder-desk-brief.md](finder-desk-brief.md). The lane fixes or rebuts each major in the PR. The landing desk reads only `finder-desk`'s own `finder:` records, never a lane's.

A lane never runs its own pass. It posts an `ask` to `finder-desk` naming the PR and head, and the desk starts that pass ahead of every other head.

## What the finder receives

Codex does not read the hooks or the register, so the prompt carries everything by value:

- The head: repository, PR number, full head sha, base branch, and the command that prints its diff.
- Every row `rulings.py slice --pr <owner/name>#<n> --head <sha> --base <base> --repo <checkout> --required` prints (every global, pending, and matched row): key, the owner's words verbatim, implications with their `removes`, `requires`, or `permits` mark and status, and names. Paste them as the register holds them, never summarized.
- The PR body and its author's comments, including the `## Rulings` and `## Regression tests` comments, as claims to check.
- The plan's path and the paths of the sub-plans the slice names, so the finder can cite the line each added or changed test rests on.

## What the finder returns

The reply follows the `findings` schema.

The prompt below defines the row verdicts and the `swept` format. `met` means the head does what the words say; `n/a` means the head touches nothing the row covers.

After the row verdicts, `swept` lists the files the finder read.

**Every key once.** Every key the slice printed appears once as `met`, `n/a`, `pending`, or a `missing` finding. A reply that drops a key is no verdict, so run the pass again.

**Severities.** Defect findings keep the schema's severities. A `blocker` counts as a major and a `nit` as a minor.

**Census findings.** A finding with `defect` starting `census:` rests only on lines the diff leaves as they were (the prompt defines it). The lane appends census findings to `<drive scratch>/finder-desk/census-<YYYY-MM-DD>.md` with the PR, head, cite, and claim, and posts one `finder census:` note to the coordinator, never a major.

**Follow-ups a port owes.** On a PR body that opens `port of #<n>`, a finding with `defect` starting `follow-up:` is one the old-tree PR's own lines share (the prompt defines it). It holds nothing: the lane lists it on the `finder: ok` record as owed, whatever its severity, and the port's lane opens the follow-up PR after the port. On any other body that word gives it no pass. The second-fix check follows the same line: on such a body it adds the class fix to that list as a follow-up instead of posting a major, and on any other body the major stands.

## Post the finder record

The finder desk posts the verdict as the pass returned it. Only these records grade a head:

```bash
cci post --drive <drive> --lane finder-desk --kind review --pr <owner/repo>#<n> --text "finder: ok <head7> <one line>"
cci post --drive <drive> --lane finder-desk --kind review --pr <owner/repo>#<n> --text "finder: major <head7> <count> open: <one line per major>"
```

`<count>` is the number of open majors, including row `missing` findings. Each major's segment names its cite and defect, separated by `; ` so the record stays on one line. Put minors in the PR body, never in the record.

The newest finder record that names the head decides. Two things answer a major: a new head, which gets its own pass, or a later `finder: ok <head7> answered: <how>` that a finder pass posts after reading the fix or rebuttal in the PR. A lane never posts `answered` on its own word.

## How the landing desk uses the grade

The landing desk refuses a head with no finder record, or whose newest record is `finder: major`, with `FINDER-PENDING`. `rulings.py check-pr` reports the open major as `FINDER-MAJOR <head7>`. An incident fix never waits for the finder: the pass runs within 30 minutes after it merges, and the runner escalates a merged head with no finder record at 30 minutes as `UNGRADED-30`.

## Prompt

The prompt below carries every grading criterion, so a pass run by hand grades the same as the relay's. Fill the angle brackets, paste the slice rows whole, and pass the prompt to `codex-ask --schema findings` through `Skill(codex)`.

```text
You are the finder for <owner/name>#<n> at head <full sha> on base <base>.
You only read. Run nothing that builds, compiles, vets, lints, typechecks, tests,
installs, or starts a service; CI grades the head. Change no file, branch, or ref.
Read the head pinned by sha, never the PR's moving tip: `git fetch origin <full sha> <base>`,
then `git diff origin/<base>...<full sha>`. Read the code around every changed symbol at
that sha, its callers and callees included.

Do three things and report all three in the findings schema.

1. Find defects in the diff: wrong results, broken callers, lost data, races,
   tests that assert less than they claim. Rate each blocker, major, minor, or nit.
   Slow code is a defect too. Ask what the change runs per chat, team, session, or run
   when there are 1,000 of them. Each of these is a major: a loop over chats, teams,
   sessions, runs, files, or rows that awaits I/O per item; a concurrency cap standing in
   for a batch; a process, timer, or poll per item; a lock or wait wider than the one
   resource it protects; a poll where a stream or signal exists; a cache, cap, timer,
   retry, or idle wait whose PR body names no fundamental fix it stands in for; local
   state in a JSON file; a branch on whether required configuration is set. Start each
   such defect "slow at N:".
   A change to a request or response shape between two separately released components
   (GraphQL inputs and fields, REST bodies, the SandSQL wire, signal payloads) must work
   against the old and the new peer: the reader ships first and accepts both
   shapes, and the writer switches a release later. A writer that sends a shape the released
   reader rejects, such as a new input field sent on every call, is a major, and so is a
   reader that stops accepting a shape a released writer still sends, such as a resolver
   that now refuses a call older clients make. A removal of an accepted shape stays a major
   until every installed client is past it, which the PR body shows by the release that
   carried the new shape to them. The dual-shape reader is never a compat layer.
   Shared infrastructure is old-tree surface. A head changes it when it edits a row,
   manifest or policy the old tree's production workloads run under or reach through, or
   points one of them at another service, fleet or endpoint: in Forge-AI/monorepo, anything
   under `infra/rows/` that renders a NetworkPolicy, a Namespace or its labels, an Istio
   AuthorizationPolicy or PeerAuthentication, a ServiceAccount, a Service, DNS or a route
   the executors, the api deployments, the restate workers or sanddb use, and any api or
   runner change that moves an old-tree call onto another fleet. Such a head must name, in
   its PR body, the old-tree workloads it affects, and carry a test that renders the change
   on every cluster config, plat, staging and each tenant, such as
   `infra/rows/k8s/old-tree-reach.test.ts` and its snapshot. A head that changes it without
   both is a major, its defect starting "old-tree infra:", and a test that renders one
   cluster where the row differs per cluster asserts less than it claims.
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
   A test whose only change is a renamed fixture, constructor, argument, or import is
   graded on those lines alone; its untouched assertions are dev's, so a finding on them
   is a census finding.
   A port is a head whose PR body, or fidelity grade, says it ports an old-tree PR by
   number. On a port, a test that asserts what a test of that old-tree PR asserts is
   graded against the old-tree PR, the contract (a port keeps its
   regression tests): cite it as "tests: <test> <- old-tree #<n>" and raise no finding for
   it. A test with no counterpart there, or asserting more than its counterpart, is graded
   against the plan as above. The same holds for a test the PR body or a PR comment under
   a `Rulings` heading ties to the finder or review-bot finding it pins, or to the
   old-world twin PR it mirrors: cite it as "tests: <test> <- <finding or twin>" and check
   only that it asserts what that source fixed.
   A port lands faithful to its old-tree PR, and a fix past that source is its own follow-up
   PR. On a PR body that opens `port of #<n>`, read that PR's own lines, pinned by sha, before
   you raise any finding. A defect those lines have too, doing the same wrong thing, starts
   "follow-up:", cites them as "#<n> <path>:<line>" and names the fix the follow-up makes; it
   holds nothing. A defect those lines lack stays a finding, and so does one that would break
   or undo a rebuild change, even when the old PR has the same lines, since our changes
   supersede a port.

Plan: <plan path>; sub-plans: <paths>

PR body:
<the PR body, verbatim>

Rows (`rulings.py slice --required` for this head, slice-hash <sha12>):
<for each row: key, words verbatim, implications with removes/requires/permits and status, names>
```
