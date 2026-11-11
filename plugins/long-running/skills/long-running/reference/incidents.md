# Handle an incident

An incident is the drive's in three cases only, and then the coordinator handles it in the turn it
arrives, before anything else:

- **He orders it:** his words call something an incident, ask for it urgently or with incident
  priority, or tell you to unbreak a live build.
- **He sends an alert or asks about one:** his link to a Datadog monitor, a Sentry issue, an
  `#outage` or `#alerts-*` report, or a page, which reaches the coordinator as an `owner` record.
- **The drive caused it:** a monitor goes bad on a target one of the drive's own releases or changes
  touched, during that release or after it, or one of the drive's releases fails partway through
  its rollout.

Every other alert in the channels the drive holds is nobody's here: no line, no order, no job. He ruled
it ("stop wasting time with random non-issue incidents no one asked you to"), so before starting an
incident run `answer_search` for his incident rulings and follow them. The coordinator holds the
alert channels, each a `drive start --watch` named on its `Hold with cc-slack subscribe` line, so the drive
sees its own releases go bad.

The goal is that users stop being blocked within minutes: by releasing the default branch when the
first check shows its newest build already works, else by rolling the release back. Then the proper
root fix of the cause ships. A stopgap never ships. The fix job starts at the alert, in the same
message as the diagnosis job: diagnosis redirects the fix job and never comes first. Nothing stands
between the alert and those two jobs: no question to him, no real-or-ours verdict, no plan mode, no
reproduction, no cause hunt, no mute as a resolution. His ruling, Nov 10 3:46 PM: "sounds good, and
fix your tooling so you dont do this again; you should have just rolle dback and done the proper
fix". On Nov 10 SandSQL release 60 failed at about 9:30 PM, a cause hunt ran alone until 10:04 PM,
and the fix's PR opened at 10:26 PM. His words, 10:38 PM: "ok hurry the fuckl up its an incident",
and 10:39 PM: "why trhe fuck did you waitg this loing?"

## One incident, one record

A failing service is one incident, however many alerts, threads and questions of his it draws, with
one evidence record and one job that owns it. The record is a cc-notes investigation: its premise is
the symptom and when it started, its timeline what changed and when, its findings the suspects, and
its root cause the verdict.

- **The owner** is the first check, or the cause job when none runs; when the first check's build
  fails too, the same job is resumed as the cause job. In its first 10 minutes it appends each
  release, merge, flag flip and config change on the failing service since an hour before the
  start, with its time and link: `ccn investigation append <id> "<time> <change> <link>"`. Each
  suspect is `ccn investigation finding add <id> "<change, PR>: <how it could fail this way>"`,
  settled by `finding confirm` or `finding clear` with `--why "<what was observed, its link>"`.
  The verdict, `ccn investigation root-cause <id> "<change, PR, what in it fails>"`, waits until a
  test machine reproduces the failure and that change explains it.
- **Every other job**, a reader, the rollback or the fix, appends what it observed and names no
  cause. The coordinator reads `ccn investigation show <id>` before any line about the cause.
- **A later alert or question** about a service whose incident is open joins it: `drive order add
  "<his words, or the alert text>" --from slack:<channel>/<ts> --thread <ts> --joins <o>` records it
  with the record of `<o>` and no job of its own, it takes the parts his words ask for with `drive
  order set <new> --parts "<part>"`, and the owner carries it with `drive job set <owner> --carries
  <new> --part "<part>"`. On Nov 10 three SandSQL alerts on shared prod became three incident
  orders, each diagnosed apart. His four threads got a guess from when the alerts fired, then one
  of two changes, then three, a stall story, and a revert with no bug named. His words, 9:19 PM:
  "but you shoul;d have known this was thed polronlem not freak out and start saying its all
  sortgs of other things fujcking fix your toolking so theres beter coordinatioin in the gfuture".

## In the same turn

1. `answer_add` his words verbatim, labels `drive:<name>` and `incident`: that answer approves the
   incident's own release and rollback, so no card goes to him for either.
2. Read the open incident orders on the dashboard's top strip: one on the same failing service makes
   this a join, by [One incident, one record](#one-incident-one-record), and nothing below starts.
   Otherwise `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts> --thread
   <ts> --incident --parts fix "first check" --due +5m`, or `--parts rollback fix cause` when no
   release or new build came before the break. `--incident` refuses an order with no `fix` part,
   records a codex job on each part, and prints each job's id beside its part, then each other open
   incident order with its note. Open the record with `ccn investigation open "<service> on
   <cluster>: <symptom>, since <time>" --body "<the alert or his words, with its link>" --label
   drive:<name> --label incident`, then `drive order set <o> --note "record <id>"`. `--incident`
   puts the order on the dashboard's top strip until it closes; an incident order made without it
   gets `drive order set <o> --incident`, which refuses until a job owns its `fix` part.
3. In the next message, start every job the order printed, and the freeze when a release broke the
   service, by [Freeze the broken service](#freeze-the-broken-service). Each job is a
   `codex:codex-wrapper` running `codex-ask --incident` whose brief carries the answer id from step
   1, the record's id and the brief template's `Incident:` line written out in full, its `Drive:`
   line naming the job, which records the agent as it starts. The watch says a job has no agent
   recorded on every pass until it does.
   - **fix**, with apply authority, from the alert on. It starts at the code path the alert or his
     words name; when a release broke the service or failed partway through its rollout, it starts
     at that release: the step that failed, its log, and the code that step runs. It builds the root
     fix of the cause. The repository's own performance and "fix the cause" rules apply (in the
     monorepo, AGENTS.md's Performance section). When the cause is a design, a design step puts its
     questions on cards by [Design first](design.md), the fix job builds the recommended option
     meanwhile, by [His card on an incident](#his-card-on-an-incident), and its PR waits only on his
     answers. It never ships a pin, a hard-coded flag, a header, a
     prefix rule, a cap, a retry, a timeout raise, a timer, a wait, a cache or a special case in
     the cause's place, and never a revert of the change. Its PR is the whole root fix with the
     tests the repository's rules ask of it; nothing is split off to land faster, since the
     rollback already stopped the harm. Each finding of the first check or the cause job goes to it
     by `SendMessage` in the turn it arrives and may redirect it, and none of them holds it back.
     His rulings, Nov 10 3:44 PM: "this is also a hack stop doing fucking hacks just do proper
     fundamental fixes", and 10:37 PM: "stop shiping shitty software do dunamental fixes".
   - After a release or a new build, the **first check** is the diagnosis job, beside the fix job,
     and no cause hunt or rollback starts before it returns. Within 10 minutes it reads which commit
     the live build was cut from and how far the default branch is ahead of it on the failing path.
     Then it installs the newest build of the default branch on a test machine and replays the
     failing action against state the bad build refused, such as a record it would not open. In
     the monorepo, `tools/ci release live` names the live build's commit, and the test machine is a
     test Mac. His ruling, Nov 9 4:55 PM: "so why the fuck did you waste 3 hours when the soluition
     was just to do a release?"
   - With no release or new build before the break, two jobs start beside the fix job:
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
     - **cause**, the diagnosis job, which owns the record and sends the fix job each suspect it
       confirms. It never gates the rollback or the fix.
   - A codex lane cannot read Sentry: it has no Sentry sign-in, and Sentry's site asks for one.
     So a first check or cause job that needs Sentry, such as the affected chats or the full error
     an alert links, gets a `long-running:reader` beside it in the same message. Start it with the
     Agent tool on `model` `opus` with `run_in_background: true`; it reads Sentry with this
     session's Sentry tools and appends what it read to the record. Add it first with `drive job add
     reader --order <o> --goal "Sentry read: <what>" --part "<its part>"`, so its brief's `Drive:`
     line records the agent, and send its result to the codex job with `SendMessage`. On Nov 10 two first checks came back blocked at
     "Sign in to Sentry", and a reader with the Sentry tools answered the same question in six minutes.
4. A `report` to the communicator for his first line: the fix job and where it started, the diagnosis
   job beside it, and the freeze when one went on.

## Freeze the broken service

When a release breaks a service, that service's other releases freeze the moment it does and stay
frozen until the fix merges, and the fix's own releases always pass. In the message of step 3, a
`long-running:builder` on Opus fast mode, added first with `drive job add builder --order <o> --part
fix --goal "freeze <service> until the fix merges"` so its brief's `Drive:` line records the agent, runs `tools/ci release freeze
<component> --reason "<the incident>: <what fails>, held until its fix merges" --until <RFC 3339 time
12 hours out>`, the bare component covering every stack it deploys, and returns the freeze's id,
which goes on the order's note beside the record's.

- From an agent, only the session that set a freeze releases through it. A builder of the
  coordinator's sets it, so each builder the coordinator starts, sharing that session, starts the
  fix's release through it. He does too, since a person releases through a freeze his own agent
  session set, and a rollback skips every freeze.
- The job that merges the fix lifts it with `tools/ci release unfreeze <id>` in that turn, before the
  fix's build starts. A fix release the freeze still refuses gets it lifted in that turn, never a
  wait.
- It goes on in the alert turn or never: once the fix job has started, a freeze could only slow or
  block the fix's release, so a later turn sets none.

His words, Nov 10 10:42 PM: "why the fuck didnt youj put a ssql freeze until this was merged", then
10:48 PM: "dont freeze ssql now wtf fix it frist". The 10:42 PM words are the request for a freeze
the monorepo `release` skill waits for, given once for every release that breaks a service.

## His card on an incident

When a question card of his blocks incident work, such as a design card on the fix, the incident's
thread hears it at once, in a line that opens with the card linked, says what it blocks, and names
the recommended option the job builds on meanwhile. The job keeps building on that recommendation:
its `ASK` resumes it in that turn with "build the recommended option meanwhile" and `drive job set
<j> --state running`, and it never waits on the card. `drive job set <j> --card <link>` or `--waits
<card>` on a job of an open incident order refuses until `--told <link>` names the drive's post in
the incident's thread that links the card. His words, Nov 10 10:48 PM: "I selected on that card,
next time if thats blkocking an incident fucking tell me".

## When the first check returns

- **The default branch's build works:** releasing the default branch is the fix. In that turn, a
  `report` to the communicator saying so with what the test machine showed; the fix job is told
  "the default branch's release is the fix" with `SendMessage` and `drive job set <j> --state
  stopped --said "the default branch's release is the fix"`; and a `long-running:builder`, added
  first with `drive job add builder --order <o> --part fix --goal "release the default branch"` so
  its brief's `Drive:` line records the agent, starts the release of the default branch's head by the cut [Ship it](#ship-it) names. No
  hotfix is built on the live build's commit, and no rollback or cause hunt starts.
- **It fails too:** `drive order set <o> --parts fix "first check" rollback cause`, the first check's
  job resumed as the cause job with `drive job set <j> --part cause`, and a rollback job of step 3
  in that turn, added first with `drive job add codex --order <o> --part rollback --goal "rollback:
  <incident>"` so its brief's `Drive:` line records the agent. The fix job keeps going, fed by the cause job.

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
the clock is missed, beside the first; the first to deliver wins, and both write to the one record.
Never fable or astra. A missed clock also sends him a line in that turn, before he asks: what is late
and the new time.

## Ship it

- Codex edits and Claude ships: the turn the fix job has its change, a `long-running:builder` ships it
  from that worktree, lands it and proves it live.
- An approved and green incident PR lands in the same turn the watch reports it: on its `checks
  passed` or `approved` event, resume its job with "land it and start its build now". In the monorepo
  the fix skips the merge queue: `drive pr set <url> --exempt "incident fix"`, then `gh pr merge <n>
  -R Forge-AI/monorepo --squash`. Never `--admin`. Accept a reviewer-bot finding with `submit-pr`'s
  `pr-reviews accept <slug> --why "incident: <why the fix is right>"` unless it says the fix is wrong.
- Nothing stands between the merged fix and its build: no dry run, no read of what the cut covers or
  what caused the break, no comparison of open builds, no question. The job shipping the fix lifts the
  service's freeze and starts the build at the fix commit, and reports its link. The release's own
  approve gate is the only check, and every read runs while it builds.
- No incident release starts, a rollback included, before a line to him states the bug it ships
  against, quoted from the record: what fails and, once there is a verdict, the change and its PR.
  The fix's release has it from the line that opened its PR; for a rollback or a release of the
  default branch, the `report` saying so goes out before its job is told to start.
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
- A revert is never a rollback, and his standing rule is fix forward. A PR or build that takes a
  change out, whole or in part, is his to approve, by the monorepo `release` skill's reserved
  revert: "NO i didnt fucking say revert it". No incident order approves it. It lands only on his
  tap on its card, which states the bug in that change from the record, with its PR and evidence,
  and any line saying a change was taken out says what is wrong with it. On Nov 10, with no build
  to roll back to, #53868 reverted #52919 on the incident order alone. His words, 9:18 PM: "we need
  to fix it, you can just fuckingf revert it and not even tel me qwhat he fucking bug is".
- A build or release he started is his: never cancel, retry, reorder or approve it, and start no other
  release while it runs. When its commit carries the fix, its steps are the lines he gets. One of ours
  that must stop gets a `report` to the communicator first, then its job stops it.
- Break-glass applies are authorized at 0 deletes and 0 replaces; report both counts.
- A repair of a team's data is a pass like any other, however urgent: it runs as a service account
  inside the cluster that serves that team and starts from a pipeline or an in-cluster job, never
  from break-glass, an admin `kubectl exec` into a pod, or anyone's own sign-in, and never from
  another cluster with credentials copied in. A repair whose writes use the service account but
  that a person's admin access started inside the pod breaks this. His ruling, Nov 10 3:38 AM:
  "And remember any passes MUST be done with a service account in the cluster of the respective
  team never form a user role".
- Claim, fence or hold nothing unless he asks. The one freeze is the broken service's, by
  [Freeze the broken service](#freeze-the-broken-service).

## After users are unblocked

While the rollback unblocks users, the fix job keeps building the root fix, with a design step first
by [Design first](design.md) when the cause is a design, and its regression tests ride in that same PR.
When releasing the default branch was the fix, the commits the first check found past the live one are
the cause, the record's verdict says so, and no hunt starts.

## What he hears

Each line is a `report` to the communicator, which posts it in his thread or the alert's thread, while
he is away too. Until users are unblocked he hears a line at least every 5 minutes; after that, each
step of the root fix reaches him once, as it happens. A link appears once, inline, where the sentence
names the thing it points at.

- No "not proven" or "unproven" paragraph under any label, as in every line to him by
  [messages.md](messages.md), and no side facts about a release, such as what else it applies, its
  counts or its parts. An unknown is a job's: the job returns it to the coordinator as an
  input, and he gets its result or nothing. This binds the communicator, its handlers and any reader
  that posts for it. His ruling, Nov 9 4:52 PM: "fuck of with this not prioven bull shit jujst
  fucking fix the actual isue".
- Every word about the cause is quoted from the record, read in the turn the line is written. Its
  verdict goes out once and is never revised. Before one, he hears that there is none yet, and the
  same line says what the fix job is doing right then, read from its transcript in that turn: the
  code it is changing, and its PR once open. When he asks what the bug is, he gets each open suspect
  with its PR and the evidence for and against it. A cause, a count of candidates or a fix reaches
  him only from the record, never from when alerts fired.
- A card of his that blocks incident work gets its line at once, by [His card on an
  incident](#his-card-on-an-incident).

The steps, in order:

1. **Running:** the fix job and where it started, beside the first check, or beside the rollback and
   the cause hunt, and the freeze when one went on.
2. **The fix is a release:** after a first check whose build worked, releasing the default branch
   fixes it, as the test machine showed.
3. **Rollback started:** the build it rolls back to, and that it lacks the failing change.
4. **Users unblocked:** whose runs succeed again, on a real machine.
5. **Root fix PR open:** its title, linked, and the record's verdict it fixes.
6. **Merged.**
7. **Release started:** the root fix's release, or the default branch's.
8. **Live:** what is live, its proof on a real machine, and whose runs succeed. Healthy means the
   affected users' runs succeed, not that a monitor went quiet.
