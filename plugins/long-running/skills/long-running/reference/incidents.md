# Handle an incident

An incident is the drive's in three cases only, and then the coordinator handles it in the turn it
arrives, before anything else:

- **He orders it:** his words call something an incident, ask for it urgently or with incident
  priority, or tell you to unbreak a live build.
- **He sends an alert or asks about one:** his link to a Datadog monitor, a Sentry issue, an
  `#outage` or `#alerts-*` report, or a page, which reaches the coordinator as an `owner` record.
- **The drive caused it:** a monitor goes bad on a target one of the drive's own releases or changes
  touched, during that release or after it.

Every other alert in the channels the drive holds is nobody's here: no line, no order, no job. He ruled
it ("stop wasting time with random non-issue incidents no one asked you to"), so before starting an
incident run `answer_search` for his incident rulings and follow them. The coordinator holds the
alert channels, each a `drive start --watch` named on its `Hold with slack_watch` line, so the drive
sees its own releases go bad.

The goal is that users stop being blocked within minutes: by releasing the default branch when the
first check shows its newest build already works, else by rolling the release back. Then the proper
root fix of the cause ships. A stopgap never ships. Nothing stands between the alert and the first
check or the rollback: no question to him, no real-or-ours verdict, no plan mode, no reproduction, no
mute as a resolution. His ruling, Nov 10 3:46 PM: "sounds good, and fix your tooling so you dont do
this again; you should have just rolle dback and done the proper fix".

## In the same turn

1. `answer_add` his words verbatim, labels `drive:<name>` and `incident`: that answer approves the
   incident's own release and rollback, so no card goes to him for either.
2. `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts> --thread <ts>
   --incident --parts "first check" --due +5m`, or `--parts "rollback" "fix" "cause"` when no
   release or new build came before the break. `--incident` puts it on the dashboard's top strip
   until the order closes; an incident order made without it gets `drive order set <o> --incident`.
3. Start its jobs, each a `codex:codex-wrapper` running `codex-ask --incident` whose brief carries the
   answer id from step 1 and the brief template's `Incident:` line written out in full, and record each
   with `drive job add codex --order <o> --goal "<its part>: <incident>" --agent <id> --part "<its part>"`.
   - After a release or a new build, the **first check** comes first and alone, before any cause hunt,
     hotfix or rollback plan. Within 10 minutes it reads which commit the live build was cut from and
     how far the default branch is ahead of it on the failing path. Then it installs the newest build
     of the default branch on a test machine and replays the failing action against state the bad
     build refused, such as a record it would not open. In the monorepo, `tools/ci release live` names the
     live build's commit, and the test machine is a test Mac. His ruling, Nov 9 4:55 PM: "so why the
     fuck did you waste 3 hours when the soluition was just to do a release?"
   - With no release or new build before the break, or once the first check's build fails too, three
     jobs in one message:
     - **rollback**, with apply authority. It rolls the release back to the newest build that provably
       lacks the failing change, however many releases back that is. The proof is that the commit
       that brought the failing change is not an ancestor of the target build's commit, so
       `git merge-base --is-ancestor <change> <target>` exits 1. While the change is not yet known,
       the proof is the failing action, replayed against the target on a test machine, succeeding. It
       also proves the target can open everything the bad builds wrote since, such as a record in a
       newer format, by opening one on a test machine. When a build passes both, it starts the
       rollback inside its clock; when no build passes both, it returns that evidence inside its
       clock instead, and the root fix is the only way out. In the monorepo the rollback is the
       `release` skill's; for a tool this Mac runs it installs that release.
     - **fix**, with apply authority. It builds the root fix of the cause, starting at the code path
       the alert or his words name. The repository's own performance and "fix the cause" rules apply
       (in the monorepo, AGENTS.md's Performance section). When the cause is a design, a design step
       comes first, by [Design first](design.md), with its questions on cards before any PR. It never
       ships a pin, a hard-coded flag, a header, a prefix rule, a cap, a retry, a timeout raise, a
       timer, a wait, a cache or a special case in the cause's place. A reverted or degraded path is
       not its deliverable either. Its PR is the whole root fix with the tests the repository's
       rules ask of it; nothing is split off to land faster, since the rollback already stopped the
       harm. His ruling, Nov 10 3:44 PM: "this is also a hack stop doing fucking hacks just do proper
       fundamental fixes".
     - **cause**, which reads Sentry, logs and code and sends what it finds to the fix job. It never
       gates the rollback.
4. A `report` to the communicator for his first line: what is running.

## When the first check returns

- **The default branch's build works:** releasing the default branch is the fix. In that turn, a
  `report` to the communicator saying so with what the test machine showed, `drive order set <o>
  --parts "first check" "release"`, and a `long-running:builder` that starts the release of the
  default branch's head by the cut [Ship it](#ship-it) names. No hotfix is built on the live build's
  commit, and no rollback or cause hunt starts.
- **It fails too:** `drive order set <o> --parts "first check" "rollback" "fix" "cause"` and the three
  jobs of step 3 in that turn; only then does the cause hunt start.

## Clocks

Each clock is set with `drive order set <o> --due` and checked at its `DUE` by reading the job's
transcript, never by waiting for its message. The first check's clock counts from his order or the
alert; the rollback's from the first check's verdict, or from the order when none ran.

| Clock | Due | Missed |
|---|---|---|
| First check: the live build's commit, and the default branch's newest build tried on a test machine | 10 minutes | A second job on the first check |
| Rollback started on the proven target, or the evidence that no build can serve | 10 minutes | A second job on the rollback |
| Every later step, until live | at most 10 minutes out | A second job on that step |
| His next line, until users are unblocked | 5 minutes after his last | A line of what is running and when the next lands |

A second job is an Opus 5.5 fast-mode `long-running:builder` on the same brief, started in the turn
the clock is missed, beside the first; the first to deliver wins. Never fable or astra. A missed clock
also sends him a line in that turn, before he asks: what is late and the new time.

## Ship it

- Codex edits and Claude ships: the turn the fix job has its change, a `long-running:builder` ships it
  from that worktree, lands it and proves it live.
- The fix PR carries the whole root fix and the tests the repository's rules ask of it; nothing is
  split off to land faster.
- An approved and green incident PR lands in the same turn the watch reports it: on its `checks
  passed` or `approved` event, resume its job with "land it and start its build now". In the monorepo
  the fix skips the merge queue: `drive pr set <url> --exempt "incident fix"`, then `gh pr merge <n>
  -R Forge-AI/monorepo --squash`. Never `--admin`. Accept a reviewer-bot finding with `submit-pr`'s
  `pr-reviews accept <slug> --why "incident: <why the fix is right>"` unless it says the fix is wrong.
- Nothing stands between the merged fix and its build: no dry run, no read of what the cut covers or
  what caused the break, no comparison of open builds, no question. The job shipping the fix starts the build at
  the fix commit and reports its link. The release's own approve gate is the only check, and every
  read runs while it builds.
- The cut is named here, never derived mid-incident. An incident fix for sand or sand-next ships
  alone, never through the normal start, which pulls in the whole platform release and waits on the
  fix's dev test:
  - a merged fix, or the default branch's head a first check proved: `tools/ci release start sand
    --only sand --emergency`;
  - a fix cherry-picked onto the live commit on a pushed branch, only once a first check found the
    default branch's newest build fails too, in any incident: `tools/ci release start --manual
    sand-next --env core-usw2-auto --commit <sha> --emergency`, since Platy refuses a commit off `dev`.

  Every other form is in the monorepo `release` skill.
- The incident order's words approve its own release's gate and its rollback: the job that starts
  the release answers the gate with `--order <the answer id>`, or `tools/ci release approve <id>
  --via <agent>` where it holds at approve. Any other release he ordered or picked is approved the
  same way, by his own order, as [landing.md](landing.md) says.
- A build or release he started is his: never cancel, retry, reorder or approve it, and start no other
  release while it runs. When its commit carries the fix, its steps are the lines he gets. One of ours
  that must stop gets a `report` to the communicator first, then its job stops it.
- Roll back to the newest build that provably lacks the failing change, however many releases back
  that is; the nearest earlier build is the target only when it lacks the change. The rollback job
  proves the target lacks the change and can open everything the bad builds wrote since, and starts
  the rollback or returns the evidence that no build passes both inside its clock.
- Break-glass applies are authorized at 0 deletes and 0 replaces; report both counts.
- A repair of a team's data is a pass like any other, however urgent: it runs as a service account
  inside the cluster that serves that team and starts from a pipeline or an in-cluster job, never
  from break-glass, an admin `kubectl exec` into a pod, or anyone's own sign-in, and never from
  another cluster with credentials copied in. A repair whose writes use the service account but
  that a person's admin access started inside the pod breaks this. His ruling, Nov 10 3:38 AM:
  "And remember any passes MUST be done with a service account in the cluster of the respective
  team never form a user role".
- Claim, fence, hold, or freeze nothing unless he asks.

## After users are unblocked

While the rollback unblocks users, the fix job keeps building the root fix, with a design step first
by [Design first](design.md) when the cause is a design, and its regression tests ride in that same PR.
When releasing the default branch was the fix, the commits the first check found past the live one are
the cause, and no hunt starts.

## What he hears

Each line is a `report` to the communicator, which posts it in his thread or the alert's thread, while
he is away too. Until users are unblocked he hears a line at least every 5 minutes; after that, each
step of the root fix reaches him once, as it happens. Nothing else reaches him but the one cause line
below, and a link appears once, inline, where the sentence names the thing it points at.

- No "not proven" or "unproven" paragraph under any label, as in every line to him by
  [messages.md](messages.md), and no side facts about a release, such as what else it applies, its
  counts or its parts. An unknown is a job's: the job returns it to the coordinator as an
  input, and he gets its result or nothing. This binds the communicator, its handlers and any reader
  that posts for it. His ruling, Nov 9 4:52 PM: "fuck of with this not prioven bull shit jujst
  fucking fix the actual isue".
- A cause reaches him in one line, never revised, and only once a test machine reproduces the failure
  and the suspected change explains it. Until then its candidates stay among the jobs.

The steps, in order:

1. **Running:** the first check, or the rollback, the fix and the cause hunt.
2. **The fix is a release:** after a first check whose build worked, releasing the default branch
   fixes it, as the test machine showed.
3. **Rollback started:** the build it rolls back to, and that it lacks the failing change.
4. **Users unblocked:** whose runs succeed again, on a real machine.
5. **Root fix PR open:** its title, linked, and the cause it removes.
6. **Merged.**
7. **Release started:** the root fix's release, or the default branch's.
8. **Live:** what is live, its proof on a real machine, and whose runs succeed. Healthy means the
   affected users' runs succeed, not that a monitor went quiet.
