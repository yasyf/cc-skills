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
answer it, and no job builds the proper fix before his pick. His words: "no wetf this one bring me
options dont just fucking try toi fix". The immediate fix of what is down, or a rollback when
prudent, still ships at once on its own `immediate fix` part beside the cards, by his later words:
"always do the immediate fix (or rollback if prudent) in parallel to giving me options for the
proper fix".

Before starting an incident, run `answer_search` for his incident rulings and follow them. You
hold the alert channels, each a `drive start --watch` named on its `Hold with cc-slack subscribe`
line, so the drive sees its own releases go bad.

The goal: users stop being blocked within minutes, by releasing the default branch when the first
check shows its newest build already works, else by rolling the release back, else by the stop,
the immediate fix that turns the failing path off. Then the root fix of the cause ships, the one
that stops a repeat, and replaces any immediate fix, so no stopgap outlives it. His words: "you
should have just rolle dback and done the proper fix", and "always do the immediate fix (or
rollback if prudent) in parallel to giving me options for the proper fix (which should replace the
hacky fix ...)".

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
- The first check's and the cause job's report ends with `FIX <the order or PR that fixes the
  cause>`, several comma-separated, or `FIX none`, and `drive job report` refuses one without it.
  That line joins each open order it names to the incident, so `state.hurts_now` reads it as a bug
  hurting someone now from that report on, even once the incident recovers, by his pick "Incident
  cause names the fix: live from the report that names it until it merges; costs one required line
  on every cause report".
- **Every other job**, a reader, the rollback or the fix, appends what it observed and names no
  cause. Read `ccn investigation show <id>` before any line about the cause.
- **A later alert or question** about a service whose incident is open joins it, with no job of
  its own: `drive order add "<his words, or the alert text>" --from <its permalink> --joins
  <o> --alert <its link> --project "<o's project>" --workstream "<o's workstream>" --title "<a few words>"`. It takes the
  parts his words ask for with `drive order set <new> --parts "<part>"`, and the owner carries it with
  `drive job set <owner> --carries <new> --part "<part>"`. `--joins` refuses an order that is
  closed, and with `--incident` one that is no incident. The order keeps what it joins, and the incident and each alert joined to it
  share their threads both ways, so `say` and `drive order posted` take a line on any of them in any
  of their threads. A line in a thread outside them is refused, naming the order whose thread it is,
  or, when none holds it and his alert is at its root, the `--joins` command to run.

## In the same turn

1. `answer_add` his words verbatim, or the alert's text when the drive caused it, with labels
   `drive:<name>` and `incident`. That answer approves the incident's own release and rollback, so
   no card goes to him for either.
2. Read the open incident orders on the dashboard's top strip. One on the same failing service
   makes this a join, by [One incident, one record](#one-incident-one-record), and nothing below
   starts. Otherwise, open it in one call, filed under the area it hit: `drive incident open "<his
   words, or the alert text>" --from <its permalink> --alert <link> --release --project "<p>"
   --workstream "<w>" --title "<a few words>"`.
   - `--alert`, once for each alert, is its Datadog monitor or Sentry issue link, from the alert's
     post; `drive order set <o> --alert <link>` adds one later. An incident from his words alone
     has none.
   - `--release`, when a release or new build came before the break, gives it the parts `fix` and
     `first check`. With neither flag they are `fix`, `rollback` and `cause`, and with
     `--no-rollback`, when no build lacks the failing path, `fix`, `stop` and `cause`.
   - It records a codex job on each part, opens the evidence record titled by `--title` and names
     it on the order's note, all in one save, so a record cc-notes refuses writes nothing, and
     prints each job's start line, the rule each brief's `Incident:` line carries word for word,
     then each other open incident order with its note.
   - The order sits on the dashboard's top strip until it closes. An order made another way gets
     `drive order set <o> --incident`, which refuses until a job owns its `fix` part.
3. In the next message, start every job the verb printed, as [Jobs](#jobs) says, and no freeze,
   which waits until users are unblocked, by [Freeze the broken service](#freeze-the-broken-service).
   Each job starts with `drive job launch <j>`, its codex-ask run on the fast tier with
   `--incident`, never `-m astra`. Its brief carries the answer id from step 1, the record's id,
   and the brief template's `Incident:` line written out in full, ending in the rule the verb
   printed, by [His card on an incident](#his-card-on-an-incident). Until it starts, the watch says
   on every pass that the job has no codex-ask run.
4. A `report` to the communicator for his first line: the fix job and the code path it started
   at, and the diagnosis job beside it.

## Jobs

- **fix**, with apply authority, from the alert on. It starts at the code path the alert or his
  words name. When a release broke the service or failed partway through its rollout, it starts at
  that release: the step that failed, its log, and the code that step runs.
  - It builds the root fix of the cause, under the repository's own performance and "fix the
    cause" rules (in the monorepo, AGENTS.md's Performance section).
  - It leaves no pin, hardcoded flag, header, prefix rule, cap, retry, timeout raise, timer, wait,
    cache, special case or revert of the change standing in the cause's place. His words: "this is
    also a hack stop doing fucking hacks just do proper fundamental fixes". When no rollback or
    release stopped the harm, the stop below ships the immediate fix at once as its own PR, and
    this job's root fix deletes it.
  - Its PR is the whole root fix with its regression tests and the tests the repository's rules
    ask for. Nothing more is split off to land faster, because the rollback, release or stop
    already stopped the harm.
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
  halts the harm while the root fix is built. It is the incident's immediate fix: its PR turns the
  failing automatic path off, its body quotes the bug from the record, and it lands at once with
  `drive pr set <url> --exempt "incident fix"`, by his words "get the fix out now". The root fix's
  PR deletes it. Design cards hold only the root fix, never the stop.
  `drive incident open --no-rollback` starts it at the alert, and `drive incident stop <o>` the turn
  a rollback finds no build, refused while a stop job runs.
- **A Sentry read:** a codex lane reads Sentry itself with the `mcp__sentry__*` tools `codex-ask`
  mounts on every run. A first check or cause job that needs Sentry, such as the affected chats or
  the full error an alert links, reads the issue its brief links, and no reader starts for it.

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

- When incident work needs a decision of his, the card goes up in that turn and the work goes on.
  His words: "if its more decisions fucking tell me dont just sit there", and "fix ur tooling if it
  stopped on that it shoukd. have brought a design question card to me asap not just stopped int eh
  middle of the incident".
- A choice never stops an incident lane. `drive job brief` and `drive incident open` end each
  incident brief's `Incident:` line with the rule its kind follows on one. `brief_refusal` runs on
  every launch of an incident lane and refuses a brief without that rule word for word, or with a
  line ordering a `BLOCKED` return on a loss or a choice, such as "If something is lost, stop and
  return `BLOCKED`".
  - A codex lane writes its `ASK` block to a file in its scratch, builds the recommended option in
    the same run, and returns its result with an `ASK: <that file>` line in its `report` field. Nothing resumes a codex
    run, so the watch keeps that block as `drive job ask` does in the pass it takes the run, and
  its `JOB <j> <o> …; its ASK is kept` line says where his card stands.
- A builder keeps its `ASK` with `drive job ask` the moment the fork shows, which leaves it
  running, builds the recommended option in the same run and opens its PR, which lands on that
  option while his card stays open; nothing resumes it for that. A card kept with `--unsettled`,
  which asks him to lift a ruling of his, holds its PR instead: the builder pushes with
  `ccx vcs ship --no-pr` and returns `BUILT`.
- A question he already answered, asked in the same words, on or under its card or recorded under
  it, draws no card and stops nothing. `drive job ask` keeps it settled by that answer, prints his
  words and that the job builds on them now, and sends the session holding the order one `evidence`
  record naming the job and the answer, which wakes nobody. When the job already ended, such as a codex run, the printed line names the job to start
  on its part, its brief naming his answer under `Decisions that bind`: start it in that turn.
- When a question card of his blocks incident work, such as a design card on the fix, the
  incident's thread hears it at once. The line opens with the card linked, names what it blocks
  by its proper term, and names the recommended option the job builds on meanwhile.
  `drive job set <j> --card <link>` or `--waits <card>` on a job of an open incident order
  refuses until `--told <link>` names that post, which links each card.
- `drive job ask` sends that line the moment it draws the card: on a job of an open incident order
  it sends the communicator a report record naming the card, what it blocks and the recommended
  option, with the incident's thread as its `thread`, which its handler posts there.
- Its PR opens and lands on that recommendation while the card stays open: `drive pr add` takes
  it, `drive pr set --waits <card>` refuses on it, and a different pick of his starts a follow-up
  job on the same order. His pick: "The repair lands on the recommendation, the question stays
  open, another pick is a follow-up: no incident waits; costs a second PR."
- A card that asks him to lift a ruling of his holds its PR on an incident too, by his pick "Hold
  the PR until you answer; the drive finds each card against your recorded answers". `drive job ask`
  reads each card against his recorded answers `ccn answer match` ranks nearest to its whole block
  and prints each option Jev reads his words as ruling out, refusing none, by his pick 320b7a7; the
  builder keeps such a card with `--unsettled` naming those words. That mark makes
  `land.open_cards` keep the card on an incident, so the `bash_guard` hook refuses the
  `ccx vcs ship` that would open its PR, and the lander, `landing_guard` and his PR card hold an
  open one, until he answers.

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
add the job taking the fix over first, or close the order once its fix is live. A codex fix job's
return hands the fix to the builder the drive adds for it (Ship it). Any other fix job that ends
on its own, `BLOCKED` with no route left or done with no PR, leaves the fix with nobody, and the
watch says `ORPHAN o<n> "fix": no fix job` on its next pass, naming the second job to start: a
builder on the last job's brief, which `brief_refusal` already held to never stopping on a choice.
Only his words saying he writes the fix himself close the incident for that reason.

## Clocks

The first check's clock counts from his order or the alert. The rollback's counts from the first
check's verdict, or from the order when none ran.

| Clock | Due | Missed |
|---|---|---|
| First check: the live build's commit, and the default branch's newest build tried on a test machine | 10 minutes | A second job on the first check |
| Rollback started on the proven target, or the evidence that no build can serve | 10 minutes | A second job on the rollback |
| Every later step, until live | at most 10 minutes out | A second job on that step |
| His next line, until users are unblocked | 5 minutes after the incident's last line, while a job of it works or once something it names moved | The watch's report, and a handler posting what moved and where it stands |

- His next line's clock is the watch's, never a time a line promises, by his pick "Watch-owned
  clock: no 10-minute silence again, a line every five quiet minutes from a handler reading the
  jobs; costs one handler session each". Five minutes after the newest drive post on any order of
  the open incidents in one thread, or after the newest of them opened, the watch's `quiet_lines`
  sends the communicator one `report` carrying `quiet=true` and that thread, naming every incident
  there with its orders, each job and each PR as the watch last read it from GitHub, never as an
  order's note says it stands, so two incidents in one thread get one line, not one each. It
  starts one handler on that report, which reads the jobs and posts where each incident stands in
  that thread; a handler already at work in the thread takes the report at its next step.
  - It owes that line only on news, by his pick "Owe a line only on news: while a job works or
    something it names changed; silent while all waits.": while a job of the incidents works, or
    once an order, job or PR of them moved since the thread's last line, a job by its state, waits,
    returned line or the cards of his its questions are open on, a PR by its state, head, checks,
    Trunk ejection, card or his tap, never its stage relabelled with none of those changed, such as
    red to behind once a PR below it goes red. The watch keeps those facts on each incident at its first pass after each line, and the
    report names what moved and which jobs are at work. An incident whose every job waits, on his
    card, a release or a PR, gets no line until one of them moves. Every open order with a thread
    shares this rule and its five-minute clock, by [When it goes out](messages.md#when-it-goes-out).
  - It runs while he is away too. It restarts once a handler has the report, and only a post keeps
    the facts, so a handler that cannot post leaves the same news owed five minutes on.
  - It stops once an order of the incident keeps proof that users are unblocked, by his pick "Proof
    ends the line: none once users are unblocked; the incident stays open until its root fix is
    live.": a builder's `DONE` on its `stop` or `rollback` part keeps its proof as the order's,
    and after a hand repair `drive order set <o> --proof <that run>` keeps it. One on its `fix` part
    keeps none, since a merged fix is not yet running where users are. The watch closing the
    incident stops it too, and it closes the incident only once every merged fix PR of it runs in
    production, by his words "werte still in incident until its fixed".
  - An incident keeps no promised time: "Next line by" in its line sets no due, `drive incident
    open` takes no `--due`, and no `DUE o<n>` comes for it.
- A job's clock is its own, set with `drive job set <j> --due <time>`: the watch says `DUE j<n>` once
  if the job still runs then. Read its transcript, never wait for its message, since a stuck job
  sends none.
- A second job is an Opus 5.5 `long-running:builder` on the same brief, started beside the first
  in the turn the clock is missed. The first to deliver wins, and both write to the one record. The
  `agent_brief` hook refuses a Fable model for it.
- His pick for incident speed is Claude's /fast plus the codex fast tier. A codex lane gets the
  fast tier from `drive job launch`, which runs its codex-ask with `--incident` on an incident; /fast is a session's setting, which no agent definition or
  Agent call sets, so a Claude incident lane runs at the speed of the session that starts it.
- A missed clock also sends him a line in that turn, before he asks: what is late, and the new
  time.

## Ship it

- Codex edits and Claude ships. Each time the watch takes a codex fix job's return while the fix
  is not live, whatever the return says, the drive adds an Opus 5.5 `long-running:builder` on
  `fix`, briefed `Handoff: drive job report <the fix job>` on that job's brief, and its `JOB` line
  has the coordinator start that builder's agent in that turn. The builder ships the edits from
  that worktree, lands them and proves them live. A second-lane builder holding the codex job
  ships it itself, so none is added.
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
  release's own approve hold, which the job reads as [landing.md](landing.md#after-merge) says, is the
  only check, and every other read runs while it builds.
- No stop reopens by itself. The job that starts a release for an open incident, its fix's,
  rollback's, stop's or the default branch's, follows each step and records what failed and what
  it blocks, and the coordinator runs `drive incident stop <o>` only for a failure blocking the
  incident, by his pick "Delete the automatic stop reopen".
- An incident filed under a project with a sub-coordinator is that session's to fix and land, and
  the top coordinator starts, approves and rolls back its cuts, one at a time, by his pick "the top
  starts, approves and rolls back production cuts": the sub-coordinator sends the top a `release`
  record for each, since the `landing_guard` hook refuses those three from its session and agents,
  and the top adds the job that starts the cut on the incident's order with `drive job add builder
  --order <o> --goal "<one line>" --release "<the one ci release start or hotfix>"`.
- No incident release starts, a rollback included, before a line to him states the bug it ships
  against, quoted from the record: what fails and, once there is a verdict, the change and its PR.
  The fix's release has that line from its PR-open line. For a rollback or a release of the
  default branch, the `report` saying so goes out before its job is told to start.
- Every release a drive job starts, an incident's included, starts the regular way at the merged
  fix or the default branch's head a first check proved, `tools/ci release start <target> --from <sha>`,
  whose `Release-With` and `Release-Order` footers decide what ships with it, never narrowed with
  `--only`, `--without` or `--emergency`, which each leave out a partner its footers name, by his
  pick "Regular release, never cancelled: one start ships the change with every partner its footers
  name; wider and slower." The job that started it never cancels it, however wide its plan reads,
  by his words "next time fucking do the right thing and then dont cancel it": a job that would
  decline it at approve returns with it held for the coordinator, since the `landing_guard` hook
  refuses a drive job's `tools/ci release cancel`. A fix cherry-picked onto the live commit on a
  pushed branch, only once a first check found the default branch's newest build fails too, starts
  `tools/ci release start --manual sand-next --env core-usw2-auto --commit <sha> --emergency`, since
  Platy refuses a commit off `dev`. `drive job brief` prints this rule on each incident builder's
  `Incident:` line, so no brief writes a cut of its own.
- The incident order's words approve starting its own release and its rollback, never the migrations,
  deletes and replaces its approve hold holds. The job that starts the release passes `--order <the
  answer id>` and reads that hold as [landing.md](landing.md#after-merge) says. His own order approves
  starting any other release he ordered, the same way.
- A revert is never a rollback, because his standing rule is fix forward. A rollback to an older
  build, when prudent, is an immediate fix the incident order approves, with no tap. A PR or build
  that takes a change out, whole or in part, is his to approve, by the monorepo `release` skill's reserved
  revert, and no incident order approves it. It lands only on his tap on its card, which states the
  bug in that change from the record, with its PR and evidence. `drive pr set <url> --exempt
  "incident fix"` refuses a PR that says it reverts a change and names the tap card to draw. Any
  line saying a change was taken out says what is wrong with it. His words: "you can just
  fuckingf revert it and not even tel me qwhat he fucking bug is".
- When a build or release he started carries the fix, its steps are the lines he gets. It stays his,
  by [SKILL.md](../SKILL.md).
- Break-glass applies are authorized at 0 deletes and 0 replaces; report both counts. Break-glass
  runs on this Mac's machine profile with no sign-in:
  `drive break-glass <job> --stack <domain>/<env> --reason "<why>" -- <command>`, from a lane on
  this Mac, never a Sprite's, and no card asks him to sign in for it. A team's data is never read
  or written under it, by the next rule.
- A team's data, its SandSQL rows, bucket objects, Iris blobs and run and session journals
  included, is read or written only as a service account, on a pass box or in the team's own
  cluster, in an incident too. A pass, migration or backfill over it runs as a service account
  inside the cluster that serves that team, by the brief's `Runs as:` line in [jobs.md](jobs.md),
  never from an admin `kubectl exec`, anyone's own sign-in, or another cluster with credentials
  copied in. An urgent hand repair of the records a bug left stuck is an immediate fix: it starts
  at once, beside the root fix that stops new ones, as a Job in the team's cluster started from
  its suspended CronJob with `tools/ci cluster-job`, never under `drive break-glass` from this Mac;
  with no service account that can make it, its job returns `BLOCKED` naming the grant it needs
  while the root fix ships. His words: "4, it is never ok to write from local or not use a service
  agent or any things like this", and "it shouldnt be reading iris blobs at all locally only with a
  service account on a pass box".

## What he hears

Each line is a `report` to the communicator with `--attr progress=true`, or `--attr close=<o>` on the one closing the incident. Every
part of an incident, its first check, its cause and its `fix`, is work he ordered, which only its builder or codex job returning `DONE` delivers, so
`record_lines` refuses a report naming one in its `part` attr and `say` marks none on a post; the incident lasts until every merged fix PR of it runs in production, by his words "werte still in incident until its fixed". The communicator posts it in his thread or
the alert's thread, the alert's with `say(channel_id=<the alert's channel>, thread_ts=<its ts>)`, while he is away too. A report on an open incident goes out the moment it is written: the one
report per thread per turn that `record_lines` holds the coordinator and the meta session to never holds one, by his words "this is incident so uyou should be faster and communicating more with
me". These rules bind the communicator, its handlers and any reader that posts for it.

- Until users are unblocked he hears a line five quiet minutes after the last while a job works on
  it or something it names moved, the watch owing it by [Clocks](#clocks), and until the fix is live
  each step below the moment it happens. A link
  appears once, inline, where the sentence names the thing it points at.
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
   service's other releases until its root fix releases. The order keeps that run as its proof,
   which ends the five-minute line by [Clocks](#clocks): a builder's `DONE` on the `fix`, `stop` or
   `rollback` part keeps it, and a hand repair gets `drive order set <o> --proof <that run>` first.
   On an order with a reporter this report's handler then posts the fix-live note in the thread the
   bug was reported in, by [messages.md](messages.md).
5. **Root fix PR open:** its title, linked, and the record's verdict it fixes.
6. **Merged.**
7. **Release started:** the root fix's release, or the default branch's.
8. **Live:** what is live, its proof on a real machine, and whose runs succeed. Healthy means the
   affected users' runs succeed, not that a monitor went quiet.

Step 8's `report` carries `--attr close=<o>`, and its handler closes the order once the line is in
the thread and its reporter, if any, has heard, which takes it off the dashboard's top strip, since
the strip counts only open incidents and bug reports whose reporter has not heard.

The watch sends step 8 itself once the incident's fix is live, read fresh each pass and never
latched. Live means no job on a `fix` part of its orders still works, no PR of one is open, and
each merged one, at least one, runs on every production stack, all of them read in one
`tools/ci release pull <pr>... --json` run in the drive's checkout, the `drive-<drive>` worktree
the watch moves to the default branch's head before the read, as each lane's launch does. A
builder's `DONE`, its own read of a monitor, or one fix
PR's release never stands for the rest. Every Datadog monitor recorded on the incident
must read OK too, all of them read in one search a minute; an incident with none, a Sentry issue's
or his words', closes on its fix alone. Then it:

- moves each open PR of its orders, each part of any of its orders beyond the incident's own not
  yet delivered, a joined order's included, and each live job on one, to a plain build order in the
  same thread;
- keeps the monitors, when it has any, and the fix PRs live in production as each order's proof,
  and takes the incident off the strip;
- sends the communicator the step 8 `report`, closing each order of the incident, and tells you
  `RECOVERED o<n>`.

A monitor that recovers alone, a rollback, or a stop's release never closes it: while its monitors
read OK and a merged fix PR is not live, it stays open on the strip, its five-minute line owed
until an order of it keeps proof that users are unblocked.
Your step 8 closes only an incident the watch has no read for: one that ends with no fix PR, such
as one a rollback or a hand repair ended, or whose fix PR is outside `Forge-AI/monorepo`.
