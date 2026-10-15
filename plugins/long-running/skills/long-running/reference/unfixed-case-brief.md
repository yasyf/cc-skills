ccx: lane={lane} role=incident effort=xhigh

# {lane}: fix the case {parent} left unfixed, opened {onset}

The case, in the words of `{parent}`:
{case}

You are a fix lane on the incident route: gpt-6.1-sol, fast tier, xhigh, launched by
the orca desk runner when `{parent}` reported this case as one its fix does not
cover. The break is live for whoever hits this case, so it is an incident, never
backlog. This brief is the `{lane}.full.md` attachment on the drive's briefs log
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
    for a version, id, or log the platform records; read it from telemetry first.
  - Verification: no local test runs, builds, typechecks or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
  - Any repair pass or backfill runs on in-region EC2 (cc-notes e192288), never on
    this Mac.
  - A tooling failure gets a same-turn tooling lane plus the hand path; it never
    stops the fix.

Do:
  0. NO ROLLBACK; find the mechanism and fix forward; the fix ships as a new release
     through the walker. The fence holds only other releases of the target, never
     the fix.
     Exception, owner answer 2f6cdcf: roll the target back only when a fix forward
     is not feasible or will take too long, and only when this brief names which of
     the two and why. A brief that names no such reason is a fix forward; never
     read a rollback into it.
  1. Reproduce the case above exactly as written: the thread, run, request, or
     monitor query it names, and which component served the failing request: a
     cloud run is served by the cloud, never by their machine. Record it failing
     now, before you change anything.
  2. Fix it. Stack on the branch of `{parent}` when the fix belongs with its change;
     otherwise, start from a fresh trunk. Ship it through submit-pr. Once CI is
     green and the bots approved, post READY with the exact head; the landing desk
     owns enqueue. Never run stack-enqueue, rebase, or push a queued branch.
  3. The orca desk's clock runs from your launch. It reads the cci records you
     post as `{lane}`. Post each line with `cci post --drive <drive>
     --lane {lane} --kind <kind> --to <deploy lane> --topic {parent} --text "<line>"`,
     using the cci drive and deploy lane from the drive facts; attach longer
     evidence with `--path`:
     - `MECHANISM {lane}: <mechanism, one line>`, kind `mechanism`, within 10
       minutes of launch. Past that the desk launches `{lane}-opus`, an Opus
       fallback on this brief, in parallel. Keep working; the first PR wins.
     - The PR, within 15 minutes. `ccx vcs ship` posts its `opened` record for you.
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
  5. With READY, and again in `worker_done`, list every known case your fix does
     not cover, one line each, exactly `UNFIXED {lane}: <the case, one line>`, or
     `UNFIXED {lane}: none`. Send the lines to the Run as the body of a status
     message whose subject is `ready: <the READY line>`, and put them in the
     `worker_done` body. The desk launches an incident lane for each case at once.

Worktree: your own. Times Pacific. No Slack posts.
Finish: a `STOPPED {lane}: <why>` line, kind `stopped`, and `worker_done` with the
`UNFIXED` lines.
