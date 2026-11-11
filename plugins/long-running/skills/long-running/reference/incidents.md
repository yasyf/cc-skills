# Handle an incident

Contents: [One incident, one record](#one-incident-one-record) ·
[In the same turn](#in-the-same-turn) · [Jobs](#jobs) ·
[When the first check returns](#when-the-first-check-returns) ·
[His card on an incident](#his-card-on-an-incident) ·
[Freeze the broken service](#freeze-the-broken-service) ·
[His stop in a thread](#his-stop-in-a-thread) · [Clocks](#clocks) ·
[Ship it](#ship-it) · [What he hears](#what-he-hears)

An incident is the drive's in three cases only. Handle it in the turn it arrives, before anything
else, because users are blocked while it waits.

- **He orders it:** his words call something an incident, ask for it urgently or with incident
  priority, or tell you to unbreak a live build.
- **He sends an alert or asks about one:** his link to a Datadog monitor, a Sentry issue, an
  `#outage` or `#alerts-*` report, or a page, which reaches you as an `owner` record.
- **The drive caused it:** a monitor goes bad on a target one of the drive's own releases or
  changes touched, during that release or after it, or a drive release fails partway through its
  rollout.

Every other alert in the channels the drive holds gets no line, no order and no job, because he
ruled against time spent on incidents nobody asked for. Anything else he asks you to fix is an
ordinary order, by [coordinator.md](coordinator.md). When his words on an order ask for options or
a diagnosis, it is no incident even with something down: a reader's diagnosis and option cards
answer it, and no fix job starts. His words: "no wetf this one bring me options dont just fucking
try toi fix".

Before starting an incident, run `answer_search` for his incident rulings and follow them. You
hold the alert channels, each a `drive start --watch` named on its `Hold with cc-slack subscribe`
line, so the drive sees its own releases go bad.

The goal: users stop being blocked within minutes, by releasing the default branch when the first
check shows its newest build already works, else by rolling the release back. Then the root fix
of the cause ships, the one that stops a repeat, and a stopgap never ships. His words: "you should
have just rolle dback and done the proper fix".

The fix job starts at the alert, in the same message as the diagnosis job, because diagnosis
redirects the fix job and never comes first. Nothing stands between the alert and those two jobs:
no question to him, no real-or-ours verdict, no plan mode, no reproduction, no cause hunt, and no
mute as a resolution.

## One incident, one record

A failing service is one incident, however many alerts, threads and questions of his it draws,
with one evidence record and one job that owns it, so every thread hears one diagnosis. The record
is a cc-notes investigation: its premise is the symptom and when it started, its timeline what
changed and when, its findings the suspects, and its root cause the verdict.

- **The owner** is the first check, or the cause job when none runs; when the first check's build
  fails too, the same job is resumed as the cause job. In its first 10 minutes it appends each
  release, merge, flag flip and config change on the failing service since an hour before the
  start, with its time and link: `ccn investigation append <id> "<time> <change> <link>"`.
- Each suspect is `ccn investigation finding add <id> "<change, PR>: <how it could fail this way>"`,
  settled by `finding confirm` or `finding clear` with `--why "<what was observed, its link>"`.
  The verdict, `ccn investigation root-cause <id> "<change, PR, what in it fails>"`, waits until a
  test machine reproduces the failure and that change explains it.
- **Every other job**, a reader, the rollback or the fix, appends what it observed and names no
  cause. Read `ccn investigation show <id>` before any line about the cause.
- **A later alert or question** about a service whose incident is open joins it, with no job of
  its own: `drive order add "<his words, or the alert text>" --from slack:<channel>/<ts> --joins
  <o> --project "<o's project>" --workstream "<o's workstream>"`. It takes the parts
  his words ask for with `drive order set <new> --parts "<part>"`, and the owner carries it with
  `drive job set <owner> --carries <new> --part "<part>"`. `--joins` refuses an order that is
  closed or no incident, and `drive order posted` refuses a line in another alert's thread on an
  open incident, naming the `--joins` command to run.

## In the same turn

1. `answer_add` his words verbatim, or the alert's text when the drive caused it, with labels
   `drive:<name>` and `incident`. That answer approves the incident's own release and rollback, so
   no card goes to him for either.
2. Read the open incident orders on the dashboard's top strip. One on the same failing service
   makes this a join, by [One incident, one record](#one-incident-one-record), and nothing below
   starts. Otherwise, open it in one call, filed under the area it hit: `drive incident open "<his
   words, or the alert text>" --from slack:<channel>/<ts> --record "<service> on <cluster>:
   <symptom>, since <time>" --release --due +5m --project "<p>" --workstream "<w>"`.
   - `--release`, when a release or new build came before the break, gives it the parts `fix` and
     `first check`. With neither flag they are `fix`, `rollback` and `cause`, and with
     `--no-rollback`, when no build lacks the failing path, `fix`, `stop` and `cause`.
   - It records a codex job on each part, opens the evidence record and names it on the order's
     note, and prints each job's start line, then each other open incident order with its note.
   - The order sits on the dashboard's top strip until it closes. An order made another way gets
     `drive order set <o> --incident`, which refuses until a job owns its `fix` part.
3. In the next message, start every job the verb printed, as [Jobs](#jobs) says, and no freeze,
   which waits until users are unblocked, by [Freeze the broken service](#freeze-the-broken-service).
   Each job is a `codex:codex-wrapper` running `codex-ask --incident`, never `-m astra`. Its brief
   carries the answer id from step 1, the record's id, and the brief template's `Incident:` line
   written out in full. Its `Drive:` line names the job, which records the agent as it starts;
   until then the watch says on every pass that the job has no agent recorded.
4. A `report` to the communicator for his first line: the fix job and the code path it started
   at, and the diagnosis job beside it.

## Jobs

- **fix**, with apply authority, from the alert on. It starts at the code path the alert or his
  words name. When a release broke the service or failed partway through its rollout, it starts at
  that release: the step that failed, its log, and the code that step runs.
  - It builds the root fix of the cause, under the repository's own performance and "fix the
    cause" rules (in the monorepo, AGENTS.md's Performance section).
  - In the cause's place it never ships a pin, a hardcoded flag, a header, a prefix rule, a cap,
    a retry, a timeout raise, a timer, a wait, a cache, a special case or a revert of the change.
    His words: "this is also a hack stop doing fucking hacks just do proper fundamental fixes".
  - Its PR is the whole root fix with its regression tests and the tests the repository's rules
    ask for. Nothing is split off to land faster, because the rollback or release already stopped
    the harm.
  - When the cause is a design, a design step puts its questions on cards by
    [Design first](design.md), and the fix job builds the recommended option meanwhile, by
    [His card on an incident](#his-card-on-an-incident).
  - Send it each finding of the first check or the cause job with `SendMessage` in the turn the
    finding arrives. A finding may redirect it, and none holds it back.
- **first check**, after a release or a new build, is the diagnosis job beside the fix job. No
  cause hunt or rollback starts before it returns, because a bug the default branch already fixed
  needs a release, not a hunt.
  - Within 10 minutes it reads which commit the live build was cut from and how far the default
    branch is ahead of it on the failing path. Then it installs the newest build of the default
    branch on a test machine and replays the failing action against state the bad build refused,
    such as a record it refused to open.
  - In the monorepo, `tools/ci release live` names the live build's commit, and the test machine
    is a test Mac.
- With no release or new build before the break, two jobs start beside the fix job:
  - **rollback**, with apply authority. It rolls the release back to the newest build that
    provably lacks the failing change, however many releases back that is. The proof is that the
    commit that brought the change is not an ancestor of the target's commit:
    `git merge-base --is-ancestor <change> <target>` exits 1. While the change is unknown, the
    proof is the failing action, replayed against the target on a test machine, succeeding. It
    also proves the target opens everything the bad builds wrote since, such as a record in a
    newer format, by opening one on a test machine.
  - When a build passes both, the rollback starts inside its clock. When none does, the job
    returns that evidence inside its clock, and in that turn `drive incident stop <o>` starts the
    stop. In the monorepo the rollback is the `release` skill's; for a tool this Mac runs, the job
    installs that release.
  - **cause**, the diagnosis job, owns the record and sends the fix job each suspect it confirms.
    It never gates the rollback or the fix.
- **stop**, with apply authority, when no build lacks the failing path, because then nothing else
  halts the harm while the root fix is built. Its PR turns the failing automatic path off and lands
  only on his tap, on a card whose body quotes the bug from the record: `drive pr set --exempt
  "incident fix"` refuses it. Design cards hold only the root fix, never the stop.
  `drive incident open --no-rollback` starts it at the alert, and `drive incident stop <o>` the turn
  a rollback finds no build, refused while a stop job runs.
- **A Sentry read:** a codex lane has no Sentry sign-in. A first check or cause job that needs
  Sentry, such as the affected chats or the full error an alert links, gets a `long-running:reader`
  beside it in the same message. Add it first with `drive job add reader --order <o> --goal "Sentry
  read: <what>" --part "<its part>"`, then start it with the Agent tool on `model` `opus` in the
  background; it reads Sentry with this session's Sentry tools and appends what it read to the
  record. Send its result to the codex job with `SendMessage`.

## When the first check returns

- **The default branch's build works:** releasing the default branch is the fix. In that turn:
  - a `report` to the communicator saying so, with what the test machine showed;
  - a `long-running:builder`, added first with
    `drive job add builder --order <o> --part fix --goal "release the default branch"`, starting
    the release of the default branch's head by the cut [Ship it](#ship-it) names;
  - then a `SendMessage` to the fix job saying "the default branch's release is the fix", and
    `drive job set <j> --state stopped --said "the default branch's release is the fix"`, which
    refuses while that job is the last running one on the fix.

  No hotfix is built on the live build's commit, and no rollback or cause hunt starts. The
  commits the first check found past the live one are the cause, and the record's verdict says so.
- **It fails too:** `drive order set <o> --parts fix "first check" rollback cause`, the first
  check's job resumed as the cause job with `drive job set <j> --part cause`, and a rollback job
  started in that turn, added first with
  `drive job add codex --order <o> --part rollback --goal "rollback: <incident>"`. The fix job
  keeps going, fed by the cause job.

## His card on an incident

- When incident work needs a decision of his, the card goes up in that turn. His words: "if its
  more decisions fucking tell me dont just sit there".
- When a question card of his blocks incident work, such as a design card on the fix, the
  incident's thread hears it at once. The line opens with the card linked, names what it blocks
  by its proper term, and names the recommended option the job builds on meanwhile.
  `drive job set <j> --card <link>` or `--waits <card>` on a job of an open incident order
  refuses until `--told <link>` names that post, which links each card.
- The ask record says the card is an incident's, so its handler posts that line the moment the
  card is up: the cci `post` tool's hook marks an ask of a job on an open incident order
  `incident=true`, with the incident's thread as its `thread`, and `landing_guard` refuses a
  `cci post` in Bash without them.
- The job keeps building on the recommendation and never waits on the card: its `ASK` resumes it
  in that turn with "build the recommended option meanwhile", which runs the job again.
- Its PR opens and lands on that recommendation while the card stays open: `drive pr add` takes
  it, `drive pr set --waits <card>` refuses on it, and a different pick of his starts a follow-up
  job on the same order. His pick: "The repair lands on the recommendation, the question stays
  open, another pick is a follow-up: no incident waits; costs a second PR."

## Freeze the broken service

Bringing the service back comes first. While users are still blocked, no freeze goes on, because a
freeze can hold the release that brings the service back. Once a rollback, a release of the default
branch or a first fix unblocks users, the service's other releases freeze until its root fix is
released. The root fix then goes out alone: freeze, merge the root fix, lift the freeze, release.
His words: "well now its fixed so u can freeze, mnerge the fix, unfreeze and release". They are the
request for a freeze the monorepo `release` skill waits for, given once for every release that
breaks a service.

- In the turn users are unblocked, run `drive incident freeze <o> <component>`, the bare component
  covering every stack it deploys. It runs `tools/ci release freeze` from your checkout for 12
  hours, the reason naming the order and its root fix, and puts the freeze's id on the order's
  note beside the record's.
- Among agents, only the session that set a freeze releases through it. Your session sets it, so
  every builder you start shares that session and can start the root fix's release. He can too,
  and a rollback skips every freeze.
- The job that merges the root fix lifts the freeze with `tools/ci release unfreeze <id>` in that
  turn, before the fix's build starts.
- A freeze found on while users are still blocked is lifted in that turn, because it can only slow
  or block the release that unblocks them.
- Claim, fence or hold nothing else unless he asks. The broken service's freeze is the one freeze.

## His stop in a thread

His stop carries the scope his words give it: "leave it alone here" stops the work in that thread
and nothing else. Nothing stops an open incident's fix, because users stay blocked until it lands.
`drive job set <j> --state stopped` refuses the last running job on an open incident's `fix` part:
add the job taking the fix over first, or close the order once its fix is live. Only his words
saying he writes the fix himself close the incident for that reason.

## Clocks

The first check's clock counts from his order or the alert. The rollback's counts from the first
check's verdict, or from the order when none ran.

| Clock | Due | Missed |
|---|---|---|
| First check: the live build's commit, and the default branch's newest build tried on a test machine | 10 minutes | A second job on the first check |
| Rollback started on the proven target, or the evidence that no build can serve | 10 minutes | A second job on the rollback |
| Every later step, until live | at most 10 minutes out | A second job on that step |
| His next line, until users are unblocked | 5 minutes after his last | A line of what is running and when the next lands |

- His next line's clock is the order's due: after each line, `drive order set <o> --due +5m`, and
  the watch says `DUE` when no drive post is in the order's thread by then.
- A job's clock is its own, set with `drive job set <j> --due <time>`: the watch says `DUE j<n>` once
  if the job still runs then. Read its transcript, never wait for its message, since a stuck job
  sends none.
- A second job is an Opus 5.5 fast-mode `long-running:builder` on the same brief, started beside
  the first in the turn the clock is missed. The first to deliver wins, and both write to the one
  record. The `agent_brief` hook refuses a Fable model for it.
- A missed clock also sends him a line in that turn, before he asks: what is late, and the new
  time.

## Ship it

- Codex edits and Claude ships. In the turn the fix job has its change, a `long-running:builder`
  ships it from that worktree, lands it and proves it live.
- An approved and green incident PR lands in the turn the watch reports it: its `checks passed`
  or `approved` event wakes the coordinator, which resumes its job with "land it and start its
  build now".
- In the monorepo the fix skips the merge queue: `drive pr set <url> --exempt "incident fix"`,
  then `gh pr merge <n> -R Forge-AI/monorepo --squash`, or, for a PR in a native GitHub stack,
  which `gh pr merge` refuses, `gh api -X PUT repos/Forge-AI/monorepo/pulls/<n>/merge-async -f
  sha=<head> -f merge_method=squash -f merge_action=direct_merge`. The `landing_guard` hook holds
  both to that exemption, and refuses `--admin`.
- Accept a reviewer-bot finding with `submit-pr`'s
  `pr-reviews accept <slug> --why "incident: <why the fix is right>"`, unless it says the fix is
  wrong.
- Nothing stands between the merged fix and its build: no dry run, no read of what the cut covers
  or what caused the break, no comparison of open builds, no question. The job shipping the fix
  lifts the service's freeze, starts the build at the fix commit, and reports its link. The
  release's own approve gate is the only check, and every read runs while it builds.
- Every release a job of an open incident starts, its fix's, rollback's, stop's or the default
  branch's, is named to the drive in that turn with `drive incident release <o> <name>`, the
  name `tools/ci release start` prints. The turn its post in the releases channel says a step
  failed, cc-slack hands the drive that line, the incident's stop reopens, and the watch says
  `EVENT stop` with the job to start, or the running stop job to resume, since a stuck release
  nobody follows leaves his users broken.
- An incident filed under a project with a sub-coordinator is that session's to fix and land, and
  the top coordinator starts, approves and rolls back its cuts, one at a time, by his pick "the top
  starts, approves and rolls back production cuts": the sub-coordinator sends the top a `release`
  record for each, since the `landing_guard` hook refuses those three from its session and agents.
- No incident release starts, a rollback included, before a line to him states the bug it ships
  against, quoted from the record: what fails and, once there is a verdict, the change and its PR.
  The fix's release has that line from its PR-open line. For a rollback or a release of the
  default branch, the `report` saying so goes out before its job is told to start.
- The cut is named here, never derived mid-incident. An incident fix for sand or sand-next ships
  alone, never through the normal start, which pulls in the whole platform release and waits on
  the fix's dev test:
  - a merged fix, or the default branch's head a first check proved:
    `tools/ci release start sand --only sand --emergency`;
  - a fix cherry-picked onto the live commit on a pushed branch, only once a first check found the
    default branch's newest build fails too:
    `tools/ci release start --manual sand-next --env core-usw2-auto --commit <sha> --emergency`,
    since Platy refuses a commit off `dev`.

  Every other form is in the monorepo `release` skill.
- The incident order's words approve its own release's gate and its rollback. The job that starts
  the release answers the gate with `--order <the answer id>`, or
  `tools/ci release approve <id> --via <agent>` where it holds at approve. His own order approves
  any other release he ordered, as [landing.md](landing.md) says.
- A revert is never a rollback, because his standing rule is fix forward. A PR or build that takes
  a change out, whole or in part, is his to approve, by the monorepo `release` skill's reserved
  revert, and no incident order approves it. It lands only on his tap on its card, which states the
  bug in that change from the record, with its PR and evidence. `drive pr set <url> --exempt
  "incident fix"` refuses a PR that says it reverts a change and names the tap card to draw. Any
  line saying a change was taken out says what is wrong with it. His words: "you can just
  fuckingf revert it and not even tel me qwhat he fucking bug is".
- When a build or release he started carries the fix, its steps are the lines he gets. It stays his,
  by [SKILL.md](../SKILL.md).
- Break-glass applies are authorized at 0 deletes and 0 replaces; report both counts.
- A repair of a team's data runs, however urgent, as a service account inside the cluster that
  serves that team, by the brief's `Runs as:` line in [jobs.md](jobs.md). It never starts from
  break-glass, an admin `kubectl exec`, anyone's own sign-in, or another cluster with credentials
  copied in.

## What he hears

Each line is a `report` to the communicator, which posts it in his thread or the alert's thread,
while he is away too. These rules bind the communicator, its handlers and any reader that posts for
it.

- Until users are unblocked he hears a line at least every 5 minutes. After that, each step of the
  root fix reaches him once, as it happens. A link appears once, inline, where the sentence names
  the thing it points at.
- Each line names the failing service, function, file or table by its proper term, never a
  stand-in, and carries no "not proven" paragraph under any label, by [messages.md](messages.md).
  It gives no side facts about a release, such as what else it applies, its counts or its parts.
  An unknown is a job's input, and he gets its result or nothing.
- Every word about the cause is quoted from the record, read in the turn the line is written. A
  cause, a count of candidates or a fix reaches him only from the record, never from when alerts
  fired. The verdict goes out once and is never revised. Before one, he hears that there is none
  yet and what the fix job is doing right then, read from its transcript in
  that turn: the file and function it is changing, and its PR once open.
- When he asks what the bug is, he gets each open suspect with its PR and the evidence for and
  against it.

The steps, in order:

1. **Running:** the fix job and the code path it started at, beside the first check, or beside
   the rollback and the cause hunt.
2. **The fix is a release:** after a first check whose build worked, releasing the default branch
   fixes it, as the test machine showed.
3. **Rollback started:** the build it rolls back to, and that it lacks the failing change.
4. **Users unblocked:** whose runs succeed again, on a real machine, and the freeze on the
   service's other releases until its root fix releases. An order with a reporter gets
   `drive order set <o> --proof <that run>` first, so this report's handler posts the fix-live note
   in the thread the bug was reported in, by [messages.md](messages.md).
5. **Root fix PR open:** its title, linked, and the record's verdict it fixes.
6. **Merged.**
7. **Release started:** the root fix's release, or the default branch's.
8. **Live:** what is live, its proof on a real machine, and whose runs succeed. Healthy means the
   affected users' runs succeed, not that a monitor went quiet.

Step 8's `report` carries `--attr close=<o>`, and its handler closes the order once the line is in
the thread and its reporter, if any, has heard, which takes it off the dashboard's top strip, since
the strip shows live incidents only.
