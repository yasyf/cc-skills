# Jobs

Contents:
- [Who owns a job and where it runs](#who-owns-a-job-and-where-it-runs)
- [Start one on this Mac](#start-one-on-this-mac)
- [Start one on a Sprite](#start-one-on-a-sprite)
- [Remote lanes](#remote-lanes)
- [The brief](#the-brief)
- [What a job returns](#what-a-job-returns)
- [Rules for every job](#rules-for-every-job)

## Who owns a job and where it runs

- **Owner.** A job is the coordinator's, with two exceptions. A drive tooling order's reader,
  designer and builder are the meta session's, started under its own "Drive tooling:" order by
  [meta.md](meta.md). The reader of his read-only question is the communicator's. A
  coordinator order whose builder serves drive tooling moves to the meta session when
  `drive job brief <j> --serves "drive tooling"` writes that brief. From then on the meta session
  starts its agents and posts its reports, and the watch sends it the order's lines. The coordinator
  sends `meta@<drive>` one `defect` record naming the job and its `Read your brief first:` line.
- **Usage limit.** A session here that stops on Claude Code's usage limit ("You've hit your weekly
  limit · resets …") keeps working on paid usage. The StopFailure hook sends Escape to the dialog in
  the session's Orca terminal, and Escape again to a login screen, because picking the paid option
  or logging in forces a fresh login. It then resumes the turn as it resumes any API error. Each hit
  is counted on the job under `limit_hits` and logged as one line of `claude-usage-limits.jsonl`
  under the state folder, which every drive on this Mac shares. A hit does not fail the job. The
  watch tells him of each hit once for the whole Mac, as a milestone, whichever drive reads it first.
- **Caps.** `caps.json` under the drive's state root is read at each start, so a change holds at
  once. Its defaults are `{"local": 12, "sprite": 30, "load_per_core": 1,
  "free_memory": 15}`. At most `local` Claude lanes of any role work here at once and `sprite` lanes on Sprites.
  No new local lane starts
  while the 1-minute load average is over the core count times `load_per_core`, or less than
  `free_memory` percent of memory is free. What happens at a full cap:
  - A builder waits on `builder-slot`, or starts on a Sprite when its brief declares no step only
    this Mac runs.
  - A reader or designer the `agent_brief` hook refuses starts on a Sprite.
  - The watch hands a report or an unanswered message of his to the
    communicator as one `ANSWER THIS:` note, which meets no cap.
  - A stall's watchdog waits for a later pass.
  - A first Sprite lane past the `sprite` cap waits on `sprite-slot`. The watch's `FREE` line
    names it, earliest due first, as a lane frees.
  - Work on a priority or open incident order, or an `immediate fix` part, starts past both caps
    and the gate. Queued work waits. It does not fail.
- **Budget.** The same file sets each project's daily budget in dollars. `daily_usd` is the day's
  total, `15000` unless set. Drive tooling, every order whose words open `Drive tooling:`, may spend
  `tooling_share` of it, `0.25`. Each other project may spend `project_usd`, `5000`, unless
  `projects` names its own, as `{"projects": {"Billing": 4000}}`. Spend prices each message of a
  job's transcripts dated today at its model's list price: input and output at their own, a cache
  write at 1.25 times input (2 times when kept an hour), a cache read at 5% of input. It sums over
  every drive filing work under the project's name. The watch re-reads it every five minutes, and a
  Sprite lane counts once its transcripts are copied here at its end. A project past its budget
  starts no new lane, relaunch or takeover until the next day, and its jobs wait as at a full cap.
  Work on a priority or open incident order, or a job marked `--priority`, still starts. The watch
  tells him once a day, in dollars.
- **Builder slots.** Eight builders hold this Mac's builder slots at once. A slot is held by: a
  builder the `FREE` line gave its turn whose agent has not started; a Sprite job whose turn to move
  here came, for 30 minutes or until its lane starts; a builder the `agent_brief` hook let start,
  from that call on; an agent still in its turn; an Orca worker started with
  `drive job launch --local` while it launches or runs. An agent that ended its turn, a launch that
  started no worker, a Sprite lane, and a job freed from a wait whose agent has not started hold
  none.
  - `drive job add builder` past eight, or while builders wait, records the job to start on a Sprite
    at once (`where` remote) and prints why, with its `drive job launch <j> <brief>`.
  - A builder whose brief declares a step only this Mac runs, on its `Mac:` or `Release:` line,
    waits on `builder-slot` instead. `drive status` shows its place.
  - Each watch pass gives each open slot to the waiting builder added first. Its `FREE` line goes once to the
    session holding its order.
  - A builder on an open incident's order, on an order `drive order set <o> --priority` marked, or
    on an `immediate fix` part starts here at once past the cap and the line, and holds a slot once
    it runs. A priority order's builder also starts past the load gate, and a drive verb or MCP call
    naming that order, its jobs or its PRs takes the drive's lock ahead of other writers. The mark
    also starts a coordinator session for that order alone, the sub-coordinator for `Priority <o>`.
  - A builder that builds nothing holds no slot and starts at once: one added with
    `drive job add builder --order <o> --goal "<goal>" --builds-nothing`, such as a read, or one
    whose goal reads `prove <PRs> live`. `drive job set <j> --builds-nothing` marks one already
    added, a waiting one included. `drive job prove` proves live only the PRs a `prove <PRs> live`
    goal names.
  - Every start here meets this one count, and Jev does not judge it: an Agent call through the
    `agent_brief` hook, `drive job launch --local`, a move or takeover from a Sprite, and a
    `SendMessage` that would wake an agent here whose turn ended, which the `communicator_calls`
    hook refuses while slots are full. After that refusal `drive job resume <j> "<the message>"`
    hands the job to a Sprite lane with the message in its handoff, or holds the message for the
    takeover when the brief declares a Mac step. A move from a Sprite waits on `builder-slot` with
    its Sprite lane running on, until the watch starts it with `drive job launch <j> --local`. A
    refused launch reaches the coordinator as a `relaunch_refused` event.
- **Placement.** A <monorepo> builder is placed when it starts, from the step its brief declares.
  `drive job add` does not refuse one for load.
  - `drive job brief <j> --mac "<the step>"` writes the brief's `Mac:` line: the step only this Mac
    runs that the job itself runs, such as break-glass, `drive install`, the drive's dashboard, a
    Mac box (`ci mac-box`), a test Mac or a devbox (`ci devbox`). A `Release:` line declares one
    too, since its agent follows and approves the release with this Mac's Buildkite token.
  - `place.mac_step` reads those two lines and nothing else in the brief, at every start:
    `drive job resume`, `drive job launch` and `drive pr wake` included. So name the step on the
    `Mac:` line. Words in the Order, Goal, Plan or Handoff place nothing.
  - A brief with such a line starts here at any load, and `drive job launch <j>` refuses its Sprite
    start, naming the step: start it with the Agent tool or `--local`. A Sprite job that gains one
    moves to a fresh worktree here, its Sprite worker stopped by `orca.reclaim` and every text the
    job holds carried in the handoff. A brief with no `Mac:` line starts on a Sprite, which lacks
    the step.
  - Without such a line, the `agent_brief` hook refuses a builder started with the Agent tool while
    this Mac's CPU is at or over the load gate's limit, the `remote` that `drive place` prints.
    Start it on its own Sprite with `drive job launch <j>`.
  - A drive tooling builder whose Goal names a step to take after its change is installed, such as
    redrawing a card or resuming a job, needs no `Mac:` line, because a `yasyf/*` merge reaches no
    lane. Name each step as a drive command with `drive job brief <j> --after "<command>"`, the
    `After install:` line. `drive job prove` runs each here once the proof file passes on the
    installed build and takes `DONE` when each exits 0. `brief_refusal` refuses the launch when Jev
    reads the Goal as naming such a step and no `After install:` line declares it.
  - A builder return's `Mac:` line, the step its `PROVE:` needs, goes onto the brief of the prover
    added at its merge, so the prover starts here.
  - A Sprite holds no Buildkite token or Datadog key. It reads Datadog with `drive datadog`, which
    runs on this Mac. Its AWS session reads Buildkite and starts `ci db-read` builds through the
    sprite door. `ci mac-box` and `ci devbox` run on this Mac's machine profile through
    `DEVBOX_AWS_PROFILE`, with no sign-in. Codex jobs and any job that touches this Mac's
    processes run here, and so does a reader unless a cap sends it to a Sprite.
- **Mac boxes.** A Mac box comes down with the job that holds it.
  `<ci> mac-box up <name> --holder <drive>/<job>` refuses a box with no holder. The lane names the
  drive and job from its brief's `Drive:` line, which tags the box `mac-box:holder`. The save that
  moves a job out of running, or to done, failed or stopped, starts `drive mac-boxes-down <job>`, which runs
  `<ci> mac-box down --holder <drive>/<job>` and logs to the drive's `mac-boxes.log`. A job holds a
  box only while it runs: the save that moves it to waiting, by WAITING or any other line, takes its
  box down too, and it runs `<ci> mac-box up` again when it resumes. To hand a box on, run <mac box hand over> before the old job's save: it moves the
  tag. A box handed on without it comes down at the old job's save, and its host
  sits in AWS's scrub for an hour or more.

## Start one on this Mac

1. Add the job and write its brief in one `job` call:
   `[{add: {kind: "builder", order: "<o>", goal: "<one line>", part: ["<part>"]}}, {brief: {serves: "<his order's words, a port of #N, or drive tooling>", done_when: "<…>", where: "<repo and branch>", native: "<his surface, with links, or none>", interactions: "<his user stories on it, each what he does, and sees what he then sees, ; only between stories>", ruling: ["<answer id>"]}}]`.
   `ruling` is optional: name an answer of his when it helps the job. An old-tree port takes the
   part `port of #N`, which the `add` gives its order when missing, and its `Serves:` line reads
   that part.
2. The watch starts a briefed builder, reader or design job once nothing holds it: here with
   `drive job start <j>`, which runs its agent as its own `claude --bg` session on the line the
   `brief` op printed, or on a Sprite with `drive job launch <j>` when the job was placed there.
   `drive job start` runs every check the `agent_brief` hook runs and refuses a second start while
   one is in flight. The session reads as the job's agent of the session holding its order, and its
   return reaches you as any agent's does. `drive job resume <j> "<text>"` posts to its cci address,
   which its next step reads or a `claude --bg --resume` wakes it on, so it needs no `SendMessage`.
   It outlives your session.
3. A local job has no Orca terminal, dispatch or Run. It starts faster and leaves no Orca state,
   but shows in no Orca sidebar, so `claude --bg` and the drive's own death and gap reads track it.
   A Sprite lane, and a role that needs a visible terminal or Orca's ask and reply, runs through
   Orca.
4. A refused start reaches you as a `relaunch_refused` event. Start it with the Agent tool:
   `subagent_type` `long-running:builder`, `long-running:reader` or `long-running:designer`,
   `run_in_background: true`, a short `description` and the `Read your brief first:` line as the
   whole `prompt`. Or start it on a Sprite as the refusal names. Pass no model, or
   `model: "sonnet"` for a reader whose question is a one-fact lookup. A design job's model is in
   [Design first](design.md). A codex job runs no agent: see [Codex jobs](#codex-jobs).

What the hooks do to an Agent call:

- `agent_brief` sets its shape. It drops `name`, since a named agent joins the agent team and does
  not wake on events. It drops Fable from any agent but `long-running:designer`, starts a design job
  as `long-running:designer` whatever type the call names, and drops `sonnet` or `haiku` from a lane
  of a build order or of an answer order marked `--complex`.
- `agent_brief` refuses a reader, builder or designer whose prompt names no brief file in the
  drive's `jobs/` folder, where `drive job brief`, a handoff and a launch write every brief. It
  names `drive job brief <j>`.
- `agent_brief` refuses a builder whose brief lacks `Serves:` or `Handoff:`, whose `Serves:` names
  none of the three kinds, whose job still holds a wait with no worker started, or whose Goal's own
  action backfills, bulk-repairs, migrates, converts or repairs with no `Runs as:` line. A Goal
  that only names a PR, stack or check is not such a pass. It also applies the builder slot count
  above.
- `scratch_folder` refuses a build order's builder, designer or codex lane whose brief lacks the
  `Researched:` line. The `brief` op writes it once the reader's or design's research is kept. Not
  checked: a job shipping the order's `immediate fix` part, a `Handoff:` handing it a PR the drive
  tracks, and a reader-pilot order of the project `drive order pilot` names.
- These hooks run in the long-running capt-hook pack, which sends each refusal to Jev first. Jev
  lets through a call the rule was not meant for. A merge, release or public-body refusal of
  `landing_guard` blocks outright.

A refusal names its fix and is information. Take the fix when it fits. When it does not, do the work
another way and send one `defect` record to the meta session.

How an agent is recorded on a job:

- The brief's `Drive:` line names the job. The agent's first Bash, Read, Write or Edit call, or its
  stop, records it on that job when its type matches the job's kind: `long-running:builder` on a
  builder, `long-running:reader` on a reader, `long-running:designer` on a design job, and an
  incident's second-lane `long-running:builder` on a codex job. Its first call after a prompt that
  reached it after its return, such as a `SendMessage`, marks the job running again.
- An agent of another type is not recorded, even when its prompt names a job:
  an `orca:orca-doctor` or an Explore agent.
- No agent is recorded over a remote job's dispatch, or while a `drive job launch` or
  `drive job resume` holds the job's lock to start a Sprite lane. `drive job set <j> --agent`
  refuses it then. `drive job set <j> --agent <the dispatch the job records>` puts a remote job an
  agent took back on its own lane.
- Each worker start sets the job running, by this record, `drive job launch`, `drive job resume` or
  `drive job set <j> --agent`. It also ends a question the last lane asked and holds that lane's
  said line for the new worker. A stopped job stays stopped: launch and resume refuse it, a later
  return is kept in its history and sets nothing, and `drive job add` on its parts adds the job
  that takes its work. The watch alone starts each pass of a standing part.

The first line piped to `drive job report <j> -` sets the job's state and said line. Nothing else
does, except a codex job's run and `drive job set <j> --state stopped`. `drive job report` refuses,
naming the fix: a line opening with no result word, a `WAITING` nothing can wake, and a reader's
`DESIGN` that `drive job ask` refuses as his card. It checks the report's shape only: whether a
design builds all his words name is the designer's to get right and the coordinator's to read.

| First line | The job's state |
|---|---|
| `DONE`, or a reader's answer | done; a builder's or codex job's `DONE` while a PR it opened is open waits on that PR as `WAITING <its links> to merge: <the DONE>`, so its change is delivered once they merge and `drive job prove` runs its proof file and sees it exit 0 |
| `READY`, `PUSHED` or `DESIGN` from any job but a reader, `WAITING`, `ASK` | waiting; an `ASK` keeps as its said line the question's title `drive job ask` set |
| `READY`, `PUSHED` or `BUILT` from a reader | refused, naming the builder on its part, since a reader ships no change; a reader's or design's `DONE` or `DESIGN` links on a `PR:` line each PR the drive does not track that already builds its part, which stops each builder of that part no agent took and keeps the PR, so the watch names its prover once it merges |
| `BLOCKED` | failed |
| `HANDOFF <path>`, a job its session stopped before done | stopped |
| `BUILT <branch>, waits on <cards>` | built on that branch, waiting on each card it names and each open card of his holding its work as the lander reads it |

Waits a first line sets, and what ends them:

- A `READY` or `PUSHED` first line turns each of these it names into a wait: a drive PR,
  `release:<service>:<owner>/<repo>#<n>[@<env>]`, `staging:<target>/<env>@<commit>`,
  `<order>:<part>` and a Buildkite build link, the build a `--release` job started included.
- A `WAITING` first line does the same for each that still holds, and also takes `pid:<n>` for a
  process on this Mac that still runs (not on a Sprite), `reply:<the question's permalink>` for a
  teammate's reply, `reply:cci:<the seq of its record the job waits after>` for another drive's next
  record, and `j<n>` for another job, stored as `drive job set --waits <j>` stores it. Lines below
  the first set no wait.
- A `WAITING` is taken only while something can wake the job: its due time or an order's, a stored
  wait that still holds, an open question card of its own, a first-line wait that still holds, a
  card its `waits on` names, or a PR the drive does not track that it keeps by `drive job set --pr`.
  One naming only merged or closed PRs, delivered parts or ended processes is refused.
- No wait is kept on a PR that itself waits on the job or a part it carries, such as the PR a
  staging tester tests.
- The watch's `FREE` line ends a wait: a PR's when it merges; a release wait's when every cluster of
  its service, or the one env it names, serves the PR's merge, or at once where `<ci> release live`
  cannot follow it; a staging wait's when `release live` reads the env running its target at its
  commit or a build carrying it; a build's when it passed, failed, was canceled or stopped at an
  approve step; a process's when it ends; a part's when a builder or codex job on it returns `DONE`
  with no PR of its own unlanded.
- A wait on the job's own PR ends when that PR closes unmerged. A wait on another job's PR that
  closes unmerged moves to that job's newest open PR, else to that job until its work is done.
- A `staging test of #N before it lands` part ends on its tester's `DONE`. That return holds one
  line per case, `CASE <case>: <verdict>`, at least one of the PR's reading `pass` and each other
  `pass` or `not exercised`, and a line naming the PR by link or `#N`. A `DONE` testing several PRs
  heads each PR's cases with a `CHECK:` line naming it. A failed or skipped case is a `CASE` line
  reading neither word, and the job still returns `DONE`. `drive job report` refuses a `DONE` on the
  part with no `CASE` line. A `DONE` whose every case reads `not exercised` ran none and leaves the
  part as it stood. A newer tester at work, waiting or failed, or the tester's next other return,
  reopens the part. The `CASE` lines travel in that return alone: a tester posts no record per
  case, and a brief that wants them before the return asks for one `handoff` record to the root,
  opening `LAST-READ` and carrying every `CASE` line.
- A staging test pausing until staging runs its soak commit, or an after-merge pass waiting for a
  build carrying its merge, returns one `WAITING staging:<target>/<env>@<commit>` per stack, such
  as `WAITING staging:db/<staging env>@044e8552b694`.
- A release wait whose service the PR's changed paths do not reach, by
  `<ci> release select --pr`, is kept with a note naming the components they reach. A release wait
  is a wake the job sets, not a gate.

### Codex jobs

A codex job runs no agent. `drive job launch <j>` starts its codex-ask run on this Mac from the
session's folder: `codex-ask --dispatch -s <the job's scratch>/codex --schema <the job's
scratch>/codex-result.json --effort high` on the job's brief, with `-m <model>` on a job added with
`--model` and `--incident` on an incident. It runs the checks the Agent hooks run and records the
run folder on the job in place of any earlier agent. The `scratch_folder` hook refuses a `codex:`
agent started on a codex job's brief.

- **Texts.** The brief carries each text the job holds under a heading naming its sender:
  `His answers since:` for his, `From the coordinator since:` for yours. A text held while the run
  goes reaches it at its next Bash call, where `landing_guard` hands it over as that call's
  `additionalContext`. Answer a codex job that asks over cci on the drive's `record` tool with `to`
  its id. cci's own tools refuse that lane.
- **Result.** The schema holds codex's final message to three fields: `state`, a result word of
  the table above but `DESIGN`; `line`, the rest of the result line; and `report`. The watch's
  relay, which runs every 2 seconds, takes an ended run as the job's return with
  `codex-ask --collect`, so you hear it within seconds. `state` and `line` set the job's state by
  the table above.
- **Stories.** A `DONE` on a brief naming his user stories is taken once it keeps its `REPRO:`
  file, which `drive job prove` runs as it runs a builder's.
- **Refused reply.** A reply `drive job report` would refuse is kept, and the same pass starts the
  next run with the refusal under `From the watch since:`. A second refusal fails the job on
  `BLOCKED codex-ask completed, its reply refused:`. A failed or dead run fails it on
  `BLOCKED codex-ask <state>:` and its log's last line. A run the relay cannot read for six hours
  counts as lost.
- **Edits.** Codex leaves its edits uncommitted, so a codex job that changed code returns
  `HANDOFF`, and the builder that ships it is briefed `Handoff: drive job report <the codex job>`.
- **Long runs.** A codex job waiting on a long run it started here returns
  `WAITING … waits on pid:<n>`. The drive keeps it as `pid:<n>@<the second it started>`. When the
  process ends, the `FREE` line says so and the same pass starts the next run, so nobody launches
  it.
- **Incidents.** On an open incident's `fix` whose fix is not live, the watch adds that builder at
  each return of the codex job and names it on a `JOB` line for the coordinator to start that turn.

The watch reads the agent's transcript, not the stored state, and takes no result itself. A running
job makes its parts `ORPHAN`s when its agent ended its turn on a result line, ended it 30 minutes
ago on any other line, wrote nothing for 30 minutes, or belonged to an earlier session.

## Start one on a Sprite

1. Add the job with a `job` call holding its `add` alone, `remote: true` on it. Then brief and
   start it in one command from the monorepo checkout's root with `run_in_background: true`:
   `drive job launch <j> --serves "<…>" --done-when "<…>" --where "<repo and branch>"`, with any
   other `drive job brief` flag. It writes the brief before it starts anything, so a refused flag
   starts no worker. Pass words holding quotes as `--handoff "$(cat <<'EOF'`, the words, then `EOF`
   and `)"`. A local job no agent holds launches the same way.
2. It starts the job through the `orca` skill's launcher as an Orca worker on a Sprite, on an Orca
   Run of its own that this drive's watch reads. Its notification says where it started or why it
   could not. The worker runs the agent its kind starts as.
3. A first try that started no worker launches once more by itself. When that starts none, start
   the job here with the Agent tool on its brief. When Orca answered a worker start with no ready
   receipt, run `drive job launch <j>` or `drive job resume <j>` again: it reuses, re-prepares or
   replaces the Sprite as its config and Orca's answer allow, and records a worker that started
   unrecorded from its ready receipt.
4. Send a handoff or message on stdin, `drive job resume <j> -` from a quoted heredoc. A file you
   write would be read back into your context after every compaction.
5. Lane Runs belong to the drive's own Orca terminal, a shell running no agent, kept under
   `terminal` in `drive.db`. Orca types no "You have N orchestration messages" line into your
   session, and the watch turns lane mail into `JOB` events. An `orca orchestration check` such a
   line names needs no run.
6. The watch frees Sprites by itself. A remote job that is not running loses its Sprite once its
   worker's turn is over: the watch runs `drive job end <j>` in a process of its own, logged to
   `relaunches.log`. A worker waiting inside its own question keeps its Sprite and session until
   the answer reaches it. The job keeps its state, waits, PRs and parts, and its next event starts
   its next lane on a fresh Sprite. End saves what the Sprite holds on no remote to GitHub as
   `saved/<lane>/<branch>-<sha>`, uncommitted changes included, before it destroys the Sprite. When
   the Sprite does not answer or the save fails, end refuses and keeps the Sprite, naming why. The
   handoff names the saved branches from GitHub's refs.
7. Sprites that earlier lanes left go to `drive job reclaim <j> <workspace>`, which saves unpushed
   work and destroys the Sprite. The watch starts it after a relaunch records a lane's death, finds
   strays from `sprite list` every 10 minutes, and asks again an hour after a reclaim that could
   not read or save a Sprite, which it marks kept. Run it by hand to ask at once. A job's current
   Sprite is not destroyed unread. A superseded attempt's unreadable Sprite is destroyed unverified,
   and the reclaim says so. A live Sprite with no cc-remote workspace record is not the drive's to
   free.

## Remote lanes

**Drive verbs.** A lane types the drive's verbs as a job here does. The launcher puts a `drive` in
the Sprite's `~/.local/bin` that asks each as one blocking Orca question, `DRIVE <its words>`, and
the watch's responder starts `drive job read` within seconds. That runs the verb in the
coordinator's checkout as the lane's own job and answers with its output under a line naming its
exit status.

- It runs `job report`, `job ask`, `job set --repro`, `job replies`, `job held`, `pr add`,
  `pr set --flaky` on a PR its own job carries, `plan search`, `datadog`, `fde-scan`, `manual`,
  `page-owner` and `status`. Reads work on any job, so a lane reads the `job report` or
  `job replies` of any job its brief names.
- It refuses any other verb, such as `pr set --tap` or an order verb, a write naming another job,
  and another drive. It refuses `break-glass`, since that command would run on this Mac as your
  user. The lane returns `BLOCKED` naming the step and its command, and the step moves to a lane on
  this Mac.
- The reader answers a relayed verb by running it, and the lane's `drive` prints what it printed.
  A `job report <its job> -` is answered once the return is kept: `drive job check <j>` then reads
  a `READY`'s PRs on GitHub in a process of its own and wakes the lane by `drive job resume`
  on a refusal. With no answer, the lane's `drive` says so on stderr every 30 s and exits 1 at
  240 s, a worst-case cap.

**Reports.** A lane keeps its report with `drive job report <j> -` and an `ASK` block with
`drive job ask <j> <file>`, then ends its turn through Orca mail: a `worker_done` carrying the
report, or an `ASK <path> [<path>…]` question naming each block.

- Each reaches you as a `JOB <j> <o> <line>` event: the result line the moment `drive job report`
  takes it, and `ASK <path>` when the question comes. A `PUSHED` waits on its checks and makes no
  event.
- A report `drive job report` did not take, the watch takes itself. Its line names each PR a
  `READY`, `PUSHED` or `PR:` line links that the drive does not track. You register one with
  `drive pr add <url> --job <j>`.
- A report sent as an Orca question, its first line a result word other than `ASK`, is that result.
  Any other `ASK` question is a `JOB` line saying that `drive job ask <j>`, which you run, keeps it.
- A `status` a lane sends while it works is a `JOB <j> <o> MESSAGE <msg id>: <subject>` event whose
  record carries the body. It sets no state.
- The watch takes each message once, so a restart repeats nothing. `drive job report <j>` prints
  every result the lane sent, newest first. `drive job replies <j>` prints every result, question
  and kept return, oldest first with its time.

**Reads.** A Sprite lane reads a red check's whole log itself: a Buildkite job's with `<ci> bk log`
through the sprite door, a GitHub Actions job's with `ccx vcs job-log`. A Sprite holds no copy of
the drive's cci or cc-notes records and no Slack credential, so a lane reads those through this Mac
with a blocking question.

- The question's first line is `READ <command>` for a `cci`, `ccn` or `cc-slack` read the builder
  agent lists, or `READ <path>` for a file under the drive's `reports/`, `asks/`, `repro/`,
  `scratch/` or `jobs/` folder. A `READ` naming one of those folders lists it one level deep. A
  `LOG <url>` question reads nothing: its answer tells the lane to read the log on its Sprite.
- The watch's relay answers it with no event, as it answers a `DRIVE` question. It checks Orca's
  mail every 2 s and starts `drive job read` in a process of its own that takes no drive lock, so a
  long pass delays no read. A command's output is cut to 12,000 characters, except a
  `cc-slack thread` or `history` read. An answer past the 64 KiB one Orca message carries is
  written onto the lane's Sprite and named in the reply. A question older than 240 s is dropped,
  since the lane's `drive` gave up.
- A read that fails replies with why and reaches you as a `JOB <j> <o> READ unanswered` line. A
  `WATCH the answer to <j>'s read failed` line needs nothing from you.
- A question that opens any other way, such as `READ: please send <file>`, is a `JOB` line for you
  to answer. Its record carries the whole question. One no resume answered for 30 minutes makes its
  parts `ORPHAN`s, said once. When he answered its card since, the line leads with his answer and
  the `drive job resume` that carries it.

**What the watch reads each minute.**

- A lane that sat at its prompt for 30 minutes makes its parts `ORPHAN`s. A running job's lane
  whose worker died, or whose Sprite's Orca environment is gone, is one `LANE … died` line. A
  waiting job's dead lane is recorded with no line and its Sprite freed.
- The watch relaunches a running job's dead lane in the same pass, with `drive job resume <j>` as a
  process of its own, and the `LANE … died` line says so. It does the same for an agent here whose
  last response ended on an API error, once the session holding it has had a response since, or at
  once when Claude's usage limit stopped it, since a holder stopped on the same limit never answers
  (`LANE … died as agent`). A lane here whose transcript ends that way gets the StopFailure resume
  prompt typed into its terminal, after Escape on the usage-limit dialog for a limit stop, once per
  stop, four times at most
  (`LANE … stopped on this Mac`).
- A job whose agent or lane a model's safeguards stopped is restarted at once in a fresh Claude
  session on this Mac with a handoff, by `drive job launch <j> --local`, never resuming the flagged
  session (`LANE … stopped by its model's safeguards`). The second stop restarts it the same way on
  `claude-sonnet-5-5`, which the job's later workers keep. A third stop, or restarts that never
  start a worker, sets it `stopped` and the watch asks him on a question card with two buttons and
  no notice text; its parts make no `ORPHAN`. His Restart tap runs the job again in a fresh session
  by the watch itself, its stop count reset to that one stop, and Leave it stopped keeps it stopped.
- While a job stays `running`, a lane that finished, ended with no next lane within 30 minutes, or
  whose relaunch started none makes its parts `ORPHAN`s. So does work he ordered that only a
  reader, a design with no builder, his card or a post holds. Those lines also name each open PR on
  the job's branch that the drive does not track.
- A PR's next step goes to its lane by `drive pr wake`.

**Resume and launch.** `drive job resume` and `drive job launch` are one relaunch under the job's
own lock. A second call waits for the first, about 150 s, then acts on the worker it started. The
`bash_guard` hook runs it in the foreground with `timeout: 20000`: a message to a live lane returns
in about five seconds, and a relaunch moves to the background at 20 seconds and notifies you.

- **Live worker.** It gets the message as the answer to its open question, or as a new message to
  its Dispatch, queued for its next check. A launch of a job whose worker takes messages is
  refused, naming the resume.
- **Finished worker.** The next worker starts in a fresh terminal and Claude session: here in the
  worktree Orca recorded or the one holding the job's open PR branch, on a Sprite in the checkout
  the last one left after `cc-remote orca prepare <Sprite> --existing`.
- **Dead worker.** A dead lane whose Sprite still holds its Claude session is resumed in place with
  `cc-remote orca prepare --existing --resume <session>`. Otherwise the relaunch saves the dead
  lane's Sprite to GitHub as `saved/<the dead lane>/<its branch>`, on a token lent over ssh stdin,
  and starts on a fresh Sprite. When the save fails, it runs on the old Sprite instead and the
  handoff says why.
- **Faulted Sprite.** A Sprite that refused a worker's tools, or whose kernel logged an error after
  init (such as an nbd0 I/O error) or whose home volume filled, is not reused. A resume reads one
  cause line from the dead lane's logs into the death record and the next brief. A Sprite that
  failed on its own counts against no job. After a second death with a cause since the job's last
  line, the session resumes on that Sprite only when the logs show its VM booted after its worker
  started. Otherwise the job relaunches on a fresh Sprite at once and waits for no slot on this
  Mac. Only a builder whose brief has a `Mac:` line moves here.
- **Agent here that cannot take `SendMessage`.** One that ended with its session, or whose last
  response was an API error or a refusal, hands its job to a Sprite lane, or to this Mac with
  `drive job launch <j> --local` or a declared Mac step.
- **Move here.** With `--local`, or a declared Mac step, a Sprite job moves here whatever its
  worker is doing: the worker is stopped, its unpushed work saved, and the Sprite destroyed once it
  holds nothing unsaved. A worker starting here starts in the worktree that holds the branch of the
  job's newest open PR, since git checks a branch out in one worktree. A fresh worktree is cut only
  when none holds it.
- **Handoff.** A new worker gets the job's brief, rebuilt from its `drive job brief` call so it
  holds what the drive holds now, plus branch, last line, report, PRs, the message, and each text
  given since the job's last return. Every handoff, resumed brief and message ends with one
  `## Holds now` block: each wait still holding, each card of his by question and link, and what
  holds each open PR from landing. A worker reads it over any older line. A hold does not stop it
  keeping a PR rebased and green.
- **Delivery.** A text to a remote lane is held on the job under one Orca request id before any
  Orca call, so an outage delays it and a retry is not applied twice. While Orca refuses calls as
  `runtime_unavailable`, resume, launch, `drive pr wake` and `drive bound` retry for up to 600 s.
  When Orca cannot tell whether the lane lives, the resume exits 0 saying the text is held, and the
  watch sends it later with a bare `drive job resume <j>`. A lane Orca cannot reach
  (`remote_runtime_unavailable`) is first woken with `orca-reclaim.py --wake`. The drive tracks
  which of his answers each job has been sent, and the watch's `ANSWER undelivered` line names one
  a job has not heard. `drive status`'s job row names each held and queued text.
- **Unread mail.** A text stays queued until a mailbox read finds it read. A worker that reports
  with mail unread gets an Orca follow-up Dispatch, `New messages`, which the watch reads as mail
  delivery. When that fails the watch holds the texts, runs `drive job resume <j>` and says so on
  one `JOB` line.
- A remote lane registers its PR with `drive pr add`, its `--port-of` or `--exempt` included, as a
  job here does.

## The brief

The brief is the job's whole context, the same here and on a Sprite. Write it so a capable agent
can finish without you: the goal, what done looks like, and the facts it cannot find itself. The
`brief` op fills the lines below from what the drive holds and from your flags, and prints
`Read your brief first: <path>`. His words ride along where they help the job. They are context,
not a condition for starting. A brief whose `native` names his surface needs `interactions`, and
the op refuses stories Jev reads as steps with no result he must see.

```
Order (his words): <the order's stored words, then each order the job carries on its own line>
Its parts (his words): <each part of the job his words named that the Order line does not hold>
From: <the record or message the order came from>
Linked messages: <each Slack message those words link, read through the cc-slack bot: link, who, when, text; one that does not read is kept as `<link>, not read: <cc-slack's words>`>
Serves: <which of three this is: "port of #N", "drive tooling" for a fix to a tool the drive runs on, or "his order">
Decisions that bind: <his recorded answers on the feature, newest first, each with its question and id or link, written by `brief.spec`; a replaced answer shows as replaced; "none named; search his answers on the area" when there are none>
Fresh: <only when his words order the work designed from the ground up: those words and where he said them>
Goal: <the deliverable, one sentence>
Done when: <the check that proves it where he will see or use it, such as the installed command's output, the live post or a picture of each user story>
Where: <repo, base branch, existing branch or PR to adopt>
Runs as: <only for a pass, migration, backfill, conversion, bulk or hand repair or other write of a team's data: the service account in that team's cluster it runs as and how it starts, such as a pipeline or an in-cluster schedule, not from this Mac; `none: <why>` when the Goal makes no such write>
Release: <only for a job a release started on: the one `<ci> release start` or `<ci> release hotfix` that `drive job add --release` started, written by the process that started it>
Release build: <beside Release only: the build that start printed, which the job's agent follows and approves>
Release freezes: <the release freezes standing now>
Mac: <the step only this Mac runs that the job itself runs, from `--mac`>
After install: <each drive command `drive job prove` runs here once the change is installed, from `--after`>
Handoff: <a PR event or a built branch freed only: the PR URLs or the built branch, its head, the event line, and the INSTALL: and PROVE: lines the first builder returned>
Repo rules: <each `[[rule]]` of the repository's `.claude/long-running.toml` at its default branch's head, as `<id>: <text>`>
Research: <a build order's reader only: the Researched: block this build needs>
Researched: <a build order's builder, designer or codex lane only: the reader or design job of its part whose kept report holds what exists and the open questions; another order's reader counts once `drive job set <it> --carries <order> --part "<part>"` gives it the part>
Answer: <a reader on his read-only question only: how its answer goes out first>
Staging: <a staging tester only: where and how its cases run, and his tap on each PR it tests>
Native: <where he sees or uses the result, such as a Slack post or card, the dashboard, a CLI or a product UI, and what that platform already does for it, with links; "none" for work he does not see>
Interactions: <when Native names his surface: his user stories on it, `;` only between stories, each what he does, and sees what he then sees, such as "he presses j on the queue, and sees the next card open with its question as its first line">
Would he ask: <what he would still have to ask for if only his words were built, such as the next piece, a link, or a native piece passed over>
Plan: <the plan section this implements; a builder added for a design's change gets `<design>'s design, change "<change>": drive job report <design>` from that add>
Incident: <an incident job only: the record's id; `drive job brief` adds the rule its kind follows from reference/incidents.md, so you type none>
Scratch: <the job's scratch folder>
Earlier parts: <a builder's brief only: each other builder of this order whose return kept a `SUMMARY:` block, at most 15 lines>
Transcripts: <the transcript path of every earlier job on this order; a Sprite lane's brief names `drive job replies <j>` for one a READ cannot serve>
Drive: <name>, order <o>, job <j>
```

How a story is proven: the job writes each story's acceptance criteria into its proof file before
it builds, and `drive pr add` refuses its PR without one. `drive job prove` runs the file on the
installed surface and reads each story's `STORY`, `CRITERION`, `PASS` or `FAIL` and `SEEN` lines in
what it prints. The proof is a picture or recording, the live post's link, or a command's output
file. A builder's or codex job's `DONE` on such a brief is refused until then.

`drive pr add` refuses a public repository's PR body that pictures the live drive or names a
teammate, so a brief does not ask for either.

## What a job returns

A job's result goes to another agent, so it is the inputs to a write-up. The communicator, which
writes to him, writes the prose.

- The job pipes its whole return on stdin into `drive job report <j> -`. Claude Code refuses a
  subagent's Write of a report file, so this is its one report.
- Its final message is the first line alone, opening with a result word the state table lists. The
  `one_line_result` hook sends back a final message past one line of 500 characters, and a
  builder's, designer's or build order reader's line until the job's newest return opens with it.
- The rest of the kept report is one fact a line: links, numbers, paths, exact quotes, what it
  could not prove, and each choice it made at a fork.
- The job's history, `jobs/<j>.replies.jsonl`, is its report. Each `drive job report <j> -` adds a
  return beside the earlier ones. `drive job report <j>` prints them newest first, the newest whole
  and each earlier one as its time and first line, all whole under `--all`. An `ASK` keeps its block
  through `drive job ask`. `drive job replies <j>` prints each return oldest first, beside each
  message a remote lane sent.

## Rules for every job

**A job decides.** A job that meets a fork picks, proceeds and names the choice in its return. His
next word steers it. It returns `ASK` for what is his: product scope, an architecture fork with
lasting cost, his own workflow, money, and the limits below.

**His words in a brief.** A brief carries his words where they help the reader, and a paraphrase is
fine when marked as one. Quote him where his exact words matter. A tap's label answers only its
card's question.

**A result he sees.** A job whose result he sees or uses builds on what the platform already does,
from its own docs, over anything hand-made. Its visual design is codex's. It checks his recorded
answers on the area, and before it returns it clicks through each user story on that surface as he
will, and closes what he would still have to ask for.

**Releases.** The top coordinator starts a release his words cover, on its own reading of them,
with no card or question first:
`drive job add builder --order <o> --goal "<what it ships>" --release "<the start> --order <his answer id>" --reading "<why his words cover it>"`.

- The call starts the release from the top's checkout, prints its build link and writes the job's
  brief on that build. `--release` takes one `<ci> release start` or `<ci> release hotfix`. A start
  typed directly is blocked by `landing_guard`, so this flag is the way in.
- The start's own `--order` names the answer of his the release rests on. A release on
  `<staging env>` or `<sandbox env>` alone needs none. An incident's order covers its own cut and
  rollback, by [incidents.md](incidents.md).
- `--reading` records your reading, and the job keeps it for the release's resume, approve, rerun
  and rollback. Say in one line to him what you started and why.
- The release job works out the order of what ships and holds itself. At the approve hold its agent
  reads each held migration, delete and replace against the code at the release's commit, then runs
  `<ci> release approve <id> --via <agent>`, naming in `--leave-out` each stack it cannot vouch
  for.
- The one limit: a dangerous release starts, resumes or passes its approve hold only while he is
  present, meaning his word or tap in the last 15 minutes. Dangerous is what the repository's root
  AGENTS.md lists, such as a migration, a backfill or a delete of user data, because those can
  break customer runs or lose data with nobody watching. Until then the job returns `WAITING` on
  his word with the release held. A rollback, a hotfix that changes nothing dangerous
  and a release off production run without him.
- While he holds every release, one starts on his word given after the hold, by
  [landing.md](landing.md#after-merge). A release he started himself stays running as he left it:
  tell him what you see unless his words hand it to you.

**Answers resume. PR events start fresh.** His answer to a job's `ASK` goes back by `SendMessage`
to its agent, or `drive job resume <j> -` with it on stdin for a remote lane or an agent another
session started. There it prints a handoff brief's `Read your brief first:` line, and the agent you
start on it takes the job as yours. A PR event after a builder returned gets a fresh builder, since
a resumed one re-reads all it ever did on every event. The `communicator_calls` hook refuses
`SendMessage` to a builder that returned `BUILT` or `DONE` outside an incident, unless it carries
an answer of his the job holds. Run `drive job add builder` for the same order and parts. It takes
each PR the old job opened that serves them and stops it `handed to <new>`, or takes a failed job's
open PRs and stops it `replaced by <new>`. Then write its brief with the `Handoff:` line. A built
job's `FREE` line is a PR event too: the `Handoff:` names the built branch, and the drive writes
his answer to each card it waited on into the brief. An incident's job is resumed for every event,
by [incidents.md](incidents.md), because minutes matter more there than tokens.

**Session settings.** The roots compact at 400,000 tokens, the communicator at 300,000, and every
other session and lane at 350,000, set by `CLAUDE_CODE_AUTO_COMPACT_WINDOW` in the orca plugin's
`lib/lane.json`. Each leaf session runs headless and light, by `agents.light` and
`agents.STRICT_MCP`: `--strict-mcp-config` with no MCP server, since every role uses the CLIs,
its role's plugins and the
drive's, no user skills, no auto-memory. An Agent call, launch or `codex-ask` naming an effort
names `high` or lower.

**A reproduction leaves one file.** A job that reproduces a bug saves one pick-up-and-run file
before it returns: the steps in order, the exact prompt and code, the build and versions, and the
numbers measured. It records it with `drive job set <j> --repro <file>`, which keeps a copy
`drive status` lists. A reproduction's scratch drive, one whose state root `LONG_RUNNING_STATE` or
`XDG_STATE_HOME` moves, gets no watch process from `drive start` or `drive watch`, so the file runs
the watch's passes in-process with `watch.run_pass`.

**One deliverable, one job.** Two owners of one PR, branch or finding each restack, watch and fix
the same change, and their PRs fold into one later. `drive status` shows who holds what, and
`cci who --pr <n>` shows any session's claim on a PR. Work someone holds goes to its holder by
`SendMessage`, `drive job resume`, or its order added with `--joins`. When B needs A merged, B
starts on A's `DONE`. A job whose chain stops moving gets the watch's `STUCK` line once, by
[The coordinator](coordinator.md).

**Sweeps.** A sweep over many units is one job running a Workflow, and you start it. A builder has
no Agent tool, so the session that owns a job starts every agent its work needs.

**A job's state is its transcript.** Check a fact about a job in the turn you tell it to him:
`cc-transcript` on the file its agent id names, or for a remote lane `drive job replies <j>` or
`orca orchestration worker-read --dispatch <its dispatch> --limit 50 --full --json`. The
`communicator_calls` hook refuses a `SendMessage` or `drive job resume` that tells a running job it
stalled, or asks whether it did, such as `status?`, with no such read in your last 10 minutes. An
order, ruling, answer or relayed refusal goes through unread.

**Closing sessions.** A close job closes the sessions or terminals his words or tapped card name,
by exact id after a fresh idle check, and returns the count with the ids. It does not close a live
drive lane or anything else, because a close cannot be undone and the session may be someone's
work.

**Stack cards.** A stack draws one card whatever its size. The card names every PR, and the PR
sections past the 50 blocks one Slack message holds open under its Explain more.
