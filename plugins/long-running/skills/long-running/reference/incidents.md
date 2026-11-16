# Handle an incident

Contents: [One incident, one record](#one-incident-one-record) ·
[In the same turn](#in-the-same-turn) · [Jobs](#jobs) ·
[When the first check returns](#when-the-first-check-returns) ·
[His card on an incident](#his-card-on-an-incident) ·
[Freeze the broken service](#freeze-the-broken-service) ·
[His stop in a thread](#his-stop-in-a-thread) · [Clocks](#clocks) ·
[Ship it](#ship-it) · [What he hears](#what-he-hears)

Handle an alert in the turn it arrives, before other work, because users are blocked while it
waits. A fix job and a diagnosis job start in the same message. Diagnosis redirects the fix job and
does not come first. Nothing stands between the alert and those two jobs: no question to him, no
real-or-ours verdict, no card, no hold, no plan mode, no reproduction, no cause hunt. A mute is not
a resolution.

An incident is the drive's in three cases:

- **He orders it:** his words call something an incident, ask for it urgently, or tell you to
  unbreak a live build.
- **He sends an alert or asks about one:** his link to a Datadog monitor, a Sentry issue, an outage
  or alerts channel report, or a page, which reaches you as an `owner` record.
- **The drive caused it:** a monitor goes bad on a target one of the drive's own releases or
  changes touched, or a drive release fails partway through its rollout. You hold the alert
  channels, each a `drive start --watch`, so the drive sees this.

Other alerts in the channels you hold get no line, order or job, since he does not want time spent
on incidents nobody asked for. One exception: a customer team's production runs failing that the
drive did not cause, such as after a teammate's release. It is no incident and pages nobody, but he
hears it at once in his DM, away too: one `report` to `communicator@<drive>` names the team, what
fails since when and the alert's link. It and every later report on that outage carry
`--attr customer_outage=true`. Working on it is his call.

When his words ask for options or a diagnosis on something that is down, give him a reader's
diagnosis and option cards for the proper fix, which waits for his pick because he asked to choose.
The immediate fix, or a rollback when prudent, still ships at once on its own `immediate fix` part.

The goal: users stop being blocked within minutes. Release the default branch when the first check
shows its newest build already works, else roll the release back, else ship the stop, the immediate
fix that turns the failing path off. Then the root fix ships and deletes any immediate fix.

A dangerous fix waits for him. One that changes a class the repository's root AGENTS.md lists
merges and releases at his next word while he is away, his last word or tap over 15 minutes old,
because it can break customer runs or lose their data. A rollback, and a hotfix of
fix PRs that change nothing dangerous, run at once without him. The incident's order approves its
own release and rollback, so no card goes to him for either. The release's approve hold still holds
the migrations, deletes and replaces a plan carries, and while he holds every release the order
approves its cut only when he gave it after that hold, by [landing.md](landing.md#after-merge).

## One incident, one record

A failing service is one incident, however many alerts, threads and questions it draws, with one
evidence record and one job that owns it, so every thread hears one diagnosis. The record is a
cc-notes investigation: premise, timeline, findings and root cause.

- **The owner** is the first check, or the cause job when none runs. In its first 10 minutes it
  appends each release, merge, flag flip and config change on the failing service since an hour
  before the start: `ccn investigation append <id> "<time> <change> <link>"`.
- A suspect is `ccn investigation finding add <id> "<change, PR>: <how it could fail this way>"`,
  settled by `finding confirm` or `finding clear` with `--why "<what was observed, its link>"`.
  The verdict is `ccn investigation root-cause <id> "<change, PR, what in it fails>"`, written once
  a test machine reproduces the failure and that change explains it.
- The owner's report ends with `FIX <the order or PR that fixes the cause>`, several
  comma-separated, or `FIX none`. `drive job report` refuses one without it. That line joins each
  open order it names to the incident, so `state.hurts_now` reads it as a bug hurting someone now
  until it merges.
- **Every other job** appends what it observed and names no cause.
- **A later alert or question** about a service whose incident is open joins it, with no job of
  its own: `drive order add "<his words, or the alert text>" --from <its permalink> --joins
  <o> --alert <its link> --project "<o's project>" --workstream "<o's workstream>" --title "<a few words>"`.
  It takes parts with `--parts "<part>"`, and the owner carries it with
  `drive job set <owner> --carries <new> --part "<part>"`. `--joins` refuses a closed order. The
  incident and each alert joined to it share their threads, so `say` and `drive order posted` take
  a line on any of them in any of their threads. A line in another thread is refused, naming the
  order whose thread it is or the `--joins` command to run.

## In the same turn

- An open incident order on the same failing service, on the dashboard's top strip, makes this a
  join. Otherwise open it in one call: `drive incident open "<his
  words, or the alert text>" --from <its permalink> --alert <link> --release --project "<p>"
  --workstream "<w>" --title "<a few words>"`.
  - `--alert`, once for each alert, is its Datadog monitor or Sentry issue link.
    `drive order set <o> --alert <link>` adds one later.
  - `--release`, when a release or new build came before the break, gives it the parts `fix` and
    `first check`. With neither flag they are `fix`, `rollback` and `cause`. With `--no-rollback`,
    when no build lacks the failing path, they are `fix`, `stop` and `cause`.
  - In one save it records a codex job on each part and opens the evidence record, named on the
    order's note. It prints each job's start line, the rule each brief's `Incident:` line carries,
    and each other open incident order.
  - `drive order set <o> --incident` makes an existing order an incident the same way, adding the
    `fix` and `cause` parts it lacks.
- Start every job the verb printed in your next message, with `drive job launch <j>`, which runs
  its codex-ask on the fast tier with `--incident`. Each brief carries the record's id and the
  `Incident:` line ending in the rule the verb printed. Until a job starts, the watch says on
  every pass that it has no codex-ask run.
- Send a `report` to the communicator for his first line: the fix job, the code path it started
  at, and the diagnosis job beside it.
- Keep his words, or the alert's text, with `answer_add` under labels `drive:<name>` and
  `incident`. The release start passes that answer's id as `--order`, which is how its approve hold
  reads the incident as approved. The jobs do not wait for it.

## Jobs

- **fix**, with apply authority, from the alert on. It starts at the code path the alert or his
  words name, or at the release that broke the service: the step that failed, its log, and the
  code that step runs.
  - It builds the root fix of the cause. No pin, hardcoded flag, cap, retry, timeout raise, cache,
    special case or revert stands in the cause's place.
  - Its PR is the whole root fix with its regression tests. Nothing is split off to land faster,
    because the rollback, release or stop already stopped the harm.
  - When the cause is a design, a design step puts its questions on cards by
    [Design first](design.md), and the fix job builds the recommended option meanwhile.
  - Pass it each finding of the diagnosis job with `SendMessage` as it arrives. A finding may
    redirect it. None holds it back.
- **first check**, after a release or a new build, is the diagnosis job. The cause hunt and the
  rollback wait for it, because a bug the default branch already fixed needs a release, not a hunt.
  - Within 10 minutes it reads which commit the live build was cut from and how far the default
    branch is ahead on the failing path. Then it installs the default branch's newest build on a
    test machine and replays the failing action against state the bad build refused.
  - The release provider's live read names the live build's commit, and its pull read lists each
    production stack behind a PR, a hotfix that cherry-picked it counting as serving it.
- With no release or new build before the break, two jobs start beside the fix job:
  - **rollback**, with apply authority, to the newest build that provably lacks the failing
    change. The proof is the release provider's pull read, GitHub's compare of the PR's squash
    commit to the target's commit: `behind`, or `diverged` with no listed commit whose subject ends
    `(#<pr>)`. `git merge-base --is-ancestor` proves nothing, since a cherry-pick is no ancestor.
    While the change is unknown, the proof is the failing action succeeding against the target on
    a test machine. It also proves the target opens what the bad builds wrote since.
  - When a build passes both, the rollback starts inside its clock. When none does, the job
    returns that evidence, and `drive incident stop <o>` starts the stop. The repository's page text names
    how it rolls back. For a tool this Mac runs, the job installs that release.
    A running watch moves to a new long-running install at most once per 15 minutes, so a fix to
    the drive's own code reaches it at once only through
    `sudo -n /usr/local/libexec/claude-restart service com.yasyf.long-running.watch.<drive>`.
  - **cause**, the diagnosis job, owns the record and sends the fix job each suspect it confirms.
    It gates neither the rollback nor the fix.
- **stop**, with apply authority, when no build lacks the failing path. It is the incident's
  immediate fix: its PR turns the failing automatic path off, its body quotes the bug from the
  record, and it lands at once with `drive pr set <url> --exempt "incident fix"`. The root fix's
  PR deletes it. Design cards hold the root fix, not the stop. `drive incident open --no-rollback`
  starts it at the alert, and `drive incident stop <o>` once a rollback finds no build, refused
  while a stop job runs.
- **A Sentry read:** a codex lane reads Sentry itself with the `mcp__sentry__*` tools `codex-ask`
  mounts on every run, so no reader starts for it.

## When the first check returns

- **The default branch's build works:** releasing the default branch is the fix. Send a `report`
  saying so, with what the test machine showed. Add a builder with
  `drive job add builder --order <o> --part fix --goal
  "release the default branch" --release "<the start>"`, the cut [Ship it](#ship-it) names with
  `--order <the incident's answer id>`. Then stop the fix job with
  `drive job set <j> --state stopped --said "the default branch's release is the fix"`, which
  refuses while that job is the last running one on the fix. The commits past the live one are the
  cause, and the record's verdict says so.
- **It fails too:** `drive order set <o> --add-part rollback --add-part cause`, resume the first
  check's job as the cause job with `drive job set <j> --part cause`, and start a rollback job
  added with `drive job add codex --order <o> --part rollback --goal "rollback: <incident>"`. The
  fix job keeps going.

## His card on an incident

A choice does not stop an incident lane. When incident work needs a decision of his, the card goes
up at once and the work goes on, on the recommended option.

- `drive job brief` and `drive incident open` end each incident brief's `Incident:` line with the
  rule its kind follows on a choice. `brief_refusal` refuses an incident brief without that rule
  word for word, or with a line ordering a `BLOCKED` return on a choice.
- A codex lane returns an `ASK: <file in its scratch>` line in its `report` field, and the watch
  keeps that block as `drive job ask` does, saying `JOB <j> <o> …; its ASK is kept`. A builder
  keeps its `ASK` with `drive job ask`, which leaves it running. Either builds the recommended
  option in the same run, and its PR lands on that option while his card stays open:
  `drive pr set --waits <card>` refuses on it. A different pick of his starts a follow-up job.
- A question he already answered in the same words draws no card. `drive job ask` settles it by
  that answer and prints his words. When the job already ended, the printed line names the job to
  start on its part.
- When a card blocks incident work, the incident's thread hears it at once: the card linked, what
  it blocks, and each job building its recommended option meanwhile. `drive job ask` sends the
  communicator that report itself, and again only once those facts change.
  `drive job set <j> --card <link>` or `--waits <card>` on an incident's job refuses without
  `--told <link>` naming that post.
- A card that asks him to lift a ruling of his, kept with `--unsettled`, holds its PR. The builder then pushes with `ccx vcs ship --no-pr` and returns `BUILT`.
  The `bash_guard` hook refuses the `ccx vcs ship` that would open its PR, and the lander holds an
  open one until he answers on the card or approves the PR.

## Freeze the broken service

Bringing the service back comes first. While users are blocked, no freeze goes on, because a freeze
can hold the release that brings the service back. Once users are unblocked, the service's other
releases freeze until its root fix is released, so the root fix goes out alone: freeze, merge the
root fix, lift the freeze, release. This page is the request for a freeze that a repository's
release tooling waits for.

- Once users are unblocked, run `drive incident freeze <o> <component>`, the bare component
  covering every stack it deploys. It runs the release provider's freeze from your checkout for 12 hours
  and puts the freeze's id on the order's note.
- Among agents, only the session that set a freeze releases through it. A rollback skips every freeze.
  The job that merges the root fix lifts it with the release provider's unfreeze before it returns
  `WAITING release` naming the fix's start.
- Lift a freeze found on while users are still blocked.
- Claim, fence or hold nothing else unless he asks.

## His stop in a thread

His stop carries the scope his words give it: "leave it alone here" stops the work in that thread
and nothing else. An open incident's fix keeps going, because users stay blocked until it lands.
`drive job set <j> --state stopped` refuses the last running job on an open incident's `fix` part:
add the job taking the fix over first, or close the order once its fix is live or he says he
writes the fix himself.

A fix job that ends with no PR and no builder taking over leaves the fix with nobody. The watch
then says `ORPHAN o<n> "fix": no fix job`, naming the builder to start. It says the same on each
work part of an incident joined to another once none of its jobs is live.

## Clocks

The first check's clock counts from his order or the alert. The rollback's counts from the first
check's verdict, or from the order when none ran.

| Clock | Due | Missed |
|---|---|---|
| First check: the live build's commit, and the default branch's newest build tried on a test machine | 10 minutes | A second job on the first check |
| Rollback started on the proven target, or the evidence that no build can serve | 10 minutes | A second job on the rollback |
| Every later step, until live | at most 10 minutes out | A second job on that step |

- His next line comes the moment a step of the incident happens, not on a clock: the session
  holding the incident sends the report and the communicator posts it in the incident's thread.
  No line goes out for quiet minutes, and an incident keeps no promised time:
  `drive incident open` takes no `--due`.
- Users are unblocked once an order of the incident keeps proof of it: a builder's `DONE` on its
  `stop` or `rollback` part, kept until that builder's next return is not `DONE`, or
  `drive order set <o> --proof <that run>` by hand. A `DONE` on the `fix` part keeps
  none, since a merged fix is not yet running where users are. The watch closes the incident once
  every merged fix PR of it runs in production.
- A job's clock is its own, set with `drive job set <j> --due <time>`. The watch says `DUE j<n>`
  once if the job still runs then. Read its transcript, since a stuck job sends no message.
- A second job is an Opus 5.5 `long-running:builder` on the same brief, started beside the first
  when the clock is missed. The first to deliver wins. The `agent_brief` hook refuses a Fable
  model for it.
- A codex lane gets the fast tier from `drive job launch`. Claude's /fast is a session's setting,
  so a Claude incident lane runs at the speed of the session that starts it.

## Ship it

- Codex edits and Claude ships. Each time the watch takes a codex fix job's return while the fix
  is not live, the drive adds an Opus 5.5 `long-running:builder` on `fix`, briefed
  `Handoff: drive job report <the fix job>`, and its `JOB` line has you start it. The builder
  ships the edits from that worktree, lands them and proves them live.
- An approved and green incident PR lands when the watch reports it: its `checks passed` or
  `approved` event wakes you, and you resume its job to land it, lift the freeze and return
  `WAITING release` naming its start.
- An open incident is his go to merge and hotfix its fix. His ruling: "It's an incident so it
  should skip the merge queue, just squash merge, then hotfix release". `drive pr add` classes a
  PR its job builds on an open incident's `fix`, `stop` or `immediate fix` as an `incident fix`.
  So does `drive pr set <url> --exempt "incident fix"` for any other PR that fixes the incident.
  The class means no Approve card and no staging test before the merge: its test is the
  after-merge pass. The lander squash-merges it directly, with no merge queue, the merge the
  repository names under `## In this repository`. A dangerous change still lands only while he is
  present, his word or tap within 15 minutes. The `landing_guard` hook holds a merge to that
  exemption, and refuses `--admin`.
- Accept a reviewer-bot finding with the repository's accept command, its why reading
  `incident: <why the fix is right>`, unless it says the fix is wrong.
- Nothing stands between the merged fix and its build: no dry run, no read of what the cut covers,
  no question. The job shipping the fix returns `WAITING release` naming the start at the fix
  commit, and the top coordinator starts it with `drive job add --release`. The release's own
  approve hold is the one check, and every other read runs while it builds.
- Every release a drive job starts, an incident's included, starts the regular way at the merged
  fix or the default branch's head a first check proved, a release start of its target from that commit, whose footers decide what ships with
  it, so it is not narrowed to leave out a partner they name. The job it started on does not cancel it, however wide its plan reads: a job that
  would decline it at approve returns with it held for the coordinator, and the `landing_guard`
  hook refuses a drive job's release cancel. A fix cherry-picked onto the live commit on a
  pushed branch, only once a first check found the default branch's newest build fails too, starts
  an emergency release of that commit on the emergency env, its
  target the one the repository names for an emergency cut under `## In this repository`, since
  the release platform refuses a commit off the default branch for any other.
- The line that tells him of a rollback or a release of the default branch says which bug it ships
  against, from the record.
- A release step that fails reopens no stop by itself. Run `drive incident stop <o>` for a failure
  that blocks the incident.
- An incident under a project with a sub-coordinator is that session's to fix and land. The
  `landing_guard` hook refuses a production cut's start, approve and rollback from its session and
  agents, so it sends the top coordinator a `release` record naming the exact start in backticks,
  and the top starts it with `drive job add
  builder --order <o> --goal "<one line>" --release "<the one ci release start or hotfix>"`.
- Fix forward. A rollback to an older build is an immediate fix the incident order approves, with
  no tap. A PR that takes a change out is no incident fix:
  `drive pr set <url> --exempt "incident fix"` refuses it and names the tap card to draw, and it
  lands on his Approve tap. Its card says what is wrong with that change, with its PR and evidence.
- A build or release he started that carries the fix stays his, and its steps are the lines he
  gets.
- Break-glass applies are authorized at 0 deletes and 0 replaces. Report both counts. It runs on
  this Mac's machine profile with no sign-in, so no card asks him to sign in:
  `drive break-glass <job> --stack <domain>/<env> --reason "<why>" -- <command>`, from a lane on
  this Mac, since a remote lane's command would not run here. It is for infrastructure, not a
  team's data.
- A team's data, its database rows, bucket objects, stored blobs and run and session journals, is
  not read or written from this Mac, in an incident too, because customer data stays off it. It is
  read or written as a service account, on a machine the repository grants for it or in the team's own
  cluster. A pass,
  migration or backfill over it runs inside the cluster that serves the team, by the brief's
  `Runs as:` line in [jobs.md](jobs.md). An urgent hand repair of the records
  a bug left stuck is an immediate fix: it starts at once beside the root fix, as a Job in the
  team's cluster started from its suspended CronJob by the repository's cluster Job verb, never under
  `drive break-glass` from this Mac. With no service account that can make it, its job returns
  `BLOCKED` naming the grant it needs while the root fix ships. That Job writes a team's data, so
  outside staging and sandbox the `landing_guard` hook refuses it while he is away or holds every
  release. Its job then returns `WAITING his word`, and the watch pages him when the incident carries
  the record of real users failing.

## What he hears

Each line is a `report` to the communicator with `--attr progress=true`, or `--attr close=<o>` on
the one closing the incident. The communicator posts it in the incident's thread, while he is away
too. A report on an open incident goes out when it is written, outside the one report per thread
per turn that `record_lines` holds the coordinator and the meta session to.

### When he is paged

He is paged only while he is away, and only when a read shows real users failing. His words
(ruling 07937c4): "this is only cloud agents? No real users? Then shouldn't have paged me. If it's
real users and it's impacting runs or local chats / agents page me". That read is a cci record
showing real users' runs, or their local chats or agents, failing on production. Whether it shows
real users is your call against that rule. A failure only the drive's own callers or proofs hit
opens the incident, and its jobs work on it, but nobody is paged. News or a question never pages.

Put the record on the incident with `--users <seq>` on `drive incident open` or
`drive order set <o>`. The watch then pages his Datadog On-Call user once for each incident opened
since he was last heard whose order carries one. A job that reads real users failing where no
incident holds it yet runs `drive page-owner "<why>" --users <seq> --order <its order>`.

A page never goes out alone. His ruling b4d22c3: "a page always comes with 1) a channel message in
a drive channel, and 2) in parallel needs to start working on a fix and be over communicative about
it, ai-oncall style". In the pass that pages, the drive:

1. Adds a codex job on the incident's `fix` and on its first check or cause wherever no worker
   runs one, and hands you each to launch in a `PAGED o<n>` line.
2. Posts the incident's first line, a fixed line the watch writes from the order's row with no
   post writer between, in a drive channel: the order's thread there, else the root of the drive
   channel of its project, never an alert's thread outside the drive's channels. The line says what
   broke, for whom, its link, and who is fixing it, and starts the incident's thread.
3. Pages him with that line's text. A real-users incident never waits on Slack: when the line
   cannot post, the page still goes out carrying it, and the watch posts the line on a later pass.

### Over-communicate a paged incident

"Make everything less chatty" holds everywhere but here. A paged incident's thread hears each step
as it happens, so he never has to ask whether anyone is on it. Write it the way an on-call bot writes an
incident, by the repository's own incident comms rules that `drive manual incidents` prints under
In this repository:

- The top post is the headline: status and what broke in at most eight words, then who is affected
  and the next step. Evidence, PR lists and links go in the thread right after it.
- Reply in the thread only with news: a step of the list that follows reached, impact changed, a mitigation, or an
  answer to a question asked there. Skip the "no change" line when a step has not moved.
- Describe impact as what users see ("runs for Polar fail to start"), not the failing component.
- An ETA is when something already running finishes. Without one, say "No ETA yet."
- No logs, commands, stack traces, or queries. One sentence says what happened.

No timer sets the cadence. Each step of that list is a line, sent the turn it happens. A thread
from the page to the fix reads like this:

> 🚨 Runs fail to start for every team on plat. Paged; fix and diagnosis running.
> ↳ Since 8:31 AM: run start returns 500 from runtime-api (record 734657, monitor 334629504). Fixing: j7901 on fix, j7902 on cause.
> ↳ Cause found: the 8:20 AM release reads sessions with a schema its replica lacks. Rolling back to `sha-4ae49e12`.
> ↳ 🩹 Mitigated: rollback live at 8:52 AM, new runs start again. Root fix still to come.
> ↳ Fix PR up: <PR link|runtime-api reads the session schema it mounts>.
> ↳ Merged; release `17790` started, ETA 9:40 AM.
> ↳ 🟢 Fix live at 9:38 AM: runs for every team start; the rollback is undone.

### Every line

A report's `part` attr names only the first check or the cause, the answers a line delivers. The
`fix` and every other part are work, which the proof file under `drive job prove` delivers, so
`record_lines` refuses a report naming one.

- Each line names the failing service, function, file or table by its proper term, by
  [messages.md](messages.md), and carries no side facts about a release.
- What he hears about the cause comes from the record, `ccn investigation show <id>`, not from
  when alerts fired. The verdict, the mechanism found, goes out once, as its own line in the
  incident's thread. Before one, he hears that there is none yet and
  what the fix job is changing right then, from its transcript. When he asks what the bug is, he
  gets each open suspect with its PR and the evidence for and against it.

The steps, in order, each one line in the incident's thread:

1. **Running:** the fix and diagnosis jobs started, and the code path the fix job started at. On a
   paged incident this is the page's own line.
2. **The fix is a release:** releasing the default branch fixes it, as the test machine showed.
3. **Rollback started:** the build it rolls back to, and that it lacks the failing change.
4. **Users unblocked:** whose runs succeed again, on a real machine, and the freeze on the
   service's other releases. The order keeps that run as its proof, by [Clocks](#clocks).
   On an order with a reporter the communicator then posts the fix-live note
   in each thread the bug was reported in, by [messages.md](messages.md).
5. **Root fix PR open:** its title, linked, and the record's verdict it fixes.
6. **Merged.**
7. **Release started:** the root fix's release, or the default branch's.
8. **Live:** what is live, its proof on a real machine, and whose runs succeed. Healthy means the
   affected users' runs succeed, not that a monitor went quiet. A fix he uses on a surface, the
   one a job brief's `Native` line names, is live once its proof comes from that surface: a
   recording of it or the live post.

Step 8's `report` carries `--attr close=<o>`, and the communicator closes the order once the line is
in the thread, which takes it off the dashboard's top strip.

The watch sends step 8 itself once the incident's fix is live, read fresh each pass:

- No job on a `fix` part still works, no PR of its orders is open, and each merged one runs in
  production for each component the release provider's components read lists for it: every
  production cluster serves the merge, by the release provider's live read every five
  minutes, a hotfix that cherry-picked it included.
- Every Datadog monitor recorded on the incident reads OK. An incident with none closes on its fix
  alone. A monitor that recovers alone, a rollback, a stop's release or a builder's `DONE` does
  not close it, since the incident lasts until its fix runs in production.
- Each order that builds something he uses keeps a recording or live post from that surface as its
  proof. Until one does, the watch says `PROVE o<n>` once, naming the builder to add on its `fix`
  part with `--builds-nothing`.
- Then it moves what is left open on its orders to a plain build order in the same thread, sends
  the step 8 `report` closing each order, and tells you `RECOVERED o<n>`.

You send step 8 yourself for an incident the watch has no read for: one with no fix PR, such as
one a rollback or a repair made by hand ended, or one whose fix PR is outside the drive's repository.
