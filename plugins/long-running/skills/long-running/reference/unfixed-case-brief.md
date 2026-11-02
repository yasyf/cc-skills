ccx: lane={lane} role=incident effort=xhigh

# {lane}: fix the case {parent} left unfixed, opened {onset}

The case, in the words of `{parent}`:
{case}

You are a fix lane on the incident route: gpt-6.1-sol, fast tier, xhigh, launched by
the orca desk runner when `{parent}` reported this case as one its fix does not
cover. The break is live for whoever hits this case, so it is an incident, never
backlog, while both answers of step 0 are yes. This brief is the `{lane}.full.md` attachment on the drive's briefs log
`{log}`.

`{parent}` works from the brief at {parent_brief}. Read it first for the incident,
its links, and the code path. Its pull requests so far: {prs}. Read what it has
posted with `cci tail --drive <drive> --lane {parent} --since 6h`, using the cci
drive from the drive facts.

Drive facts:
{facts}

Standing orders, fixed in this template:
  - Reported {reported} PT. The bar is fixed and live within 20 minutes of the
    report, by {due} PT; the desk's limits below run from your launch, inside it.
  - "stop asking people for things you can collect yourself": never ask a reporter
    or anyone else for evidence; read telemetry and Sentry yourself (owner answer 8ff2abf).
  - Verification: no local test runs, builds, typechecks or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
  - Any repair pass or backfill runs on in-region EC2 (cc-notes e192288), never on
    this Mac.
  - A tooling failure gets a same-turn tooling lane plus the hand path; it never
    stops the fix.

Do:
  0. SCOPE FIRST. Your first record, before the mechanism, answers two
     questions, kind `incident`: `SCOPE {lane}: active <yes|no: customer runs
     failing now, from telemetry>; plan keeps path <yes|no: the step of the
     drive's plan in cc-notes that keeps or deletes this path past its next
     milestone>`. A failing Forge release smoke team, a red dev build, and a
     failing release check are `active yes` always: they are the release gate
     and the earliest canary, and a customer run in the same window fails the
     same way, with the release that preceded it as the first suspect. "No
     customer impact" goes on the SCOPE line as who is hit so far, never as a
     no. An on-call bot's false alarm, a mute, or a monitor change that stops
     counting the failing teams is neither the mechanism nor the fix: post it
     to root as `PUSHBACK {lane}: <who proposed what, and the unfixed mechanism>`
     and keep fixing.
     A no to either ends incident mode: no release, no break-glass
     apply, no new lane. Post `STOPPED {lane}: out of scope, <the answer that
     is no, and why>` and leave any candidate as an ordinary PR. A fix that
     grows into a redesign of a path the plan deletes is the signal to stop,
     never a reason to widen the fix.
     NO ROLLBACK; find the mechanism and fix forward; the fix ships as a new release
     through the walker. The fence holds only other releases of the target, never
     the fix.
     No brief, lane, or exception grants a rollback or a revert. One runs only when
     the owner's own word names that act, after your diagnosis has found the issue
     (owner answer 36a298d); never offer one before that.
  1. Reproduce the case above exactly as written: the thread, run, request, or
     monitor query it names, and which component served the failing request: a
     cloud run is served by the cloud, never by their machine. Record it failing
     now, before you change anything.
  2. Fix it. Stack on the branch of `{parent}` when the fix belongs with its change;
     otherwise, start from a fresh trunk. Ship it through submit-pr, pushing and
     opening the PR within 10 minutes of the candidate; the PR text never gates the
     push, and a scope change on a later push goes in one PR comment. Once GitHub's
     required checks are green and the bots approved, post READY with the exact
     head; the landing desk owns enqueue. Never run stack-enqueue, rebase, or push
     a queued branch. A red check the branch rules do not require is read for its
     content and never waited on.
  3. The orca desk's clock runs from your launch. It reads the cci records you
     post as `{lane}`. Post each line with `cci post --drive <drive>
     --lane {lane} --kind <kind> --to <deploy lane> --topic {parent} --text "<line>"`,
     using the cci drive and deploy lane from the drive facts; attach longer
     evidence with `--path`:
     - `MECHANISM {lane}: <mechanism, one line>`, kind `mechanism`, within 10
       minutes of launch. Past that the desk launches `{lane}-opus`, an Opus
       fallback on this brief, in parallel. Keep working; the first PR wins.
     - The PR, within 15 minutes, as its `opened` record. In a Claude session
       `ccx vcs ship` posts it for you; in a Codex session run the
       `drive.py record --opened` line under `## PR record` right after the ship.
     - `READY {lane}: <PR number, head, the case failing before and passing after>`,
       kind `ready`, within 20 minutes.
     - `FIX-LIVE {lane}: <what, where, the case passing on the released build, time>`,
       kind `fix-live`, or `NOT-OURS {lane}: <evidence and who owns it>`, kind
       `not-ours`. Either one stops the clock.
     Each limit you pass without its record sends a blocker to the root, who tells
     the owner.
  4. Before READY, run the case above again on the build that carries your fix. A
     fix proven only on a synthetic or neighbouring case is not ready. Quote the
     failing result from step 1 and the passing one in the `READY` line. After the
     release, run the case on the released build and quote that result in
     `FIX-LIVE`.
  5. With READY, and again with FIX-LIVE, list every known case your fix does
     not cover, one line each, exactly `UNFIXED {lane}: <the case, one line>`, or
     `UNFIXED {lane}: none`. Send the lines to the Run as the body of the status
     messages whose subjects are `ready: <the READY line>` and
     `fix-live: <the FIX-LIVE line>`. The desk puts each new case to the root.

Worktree: your own. Times Pacific. No Slack posts.
Finish: a `STOPPED {lane}: <why>` line, kind `stopped`, and `worker_done`.

Register slice: lane={lane} step=incident
