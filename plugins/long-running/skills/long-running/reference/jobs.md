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

- Every job is the coordinator's except two kinds. A drive tooling order's reader, designer and
  builder are the meta session's, started under its own "Drive tooling:" order by [meta.md](meta.md),
  so neither the communicator nor the coordinator starts a tooling job. The reader of his read-only
  question is the communicator's, under its `answer` order, which `drive handler --answer` adds
  with that reader job, the handler its agent. An order of the coordinator's whose
  builder serves drive tooling becomes the meta session's when
  `drive job brief <j> --serves "drive tooling"` writes that brief: from then on only the meta
  session starts its agents and posts its reports, and the watch sends it the order's lines. The
  coordinator starts no agent on it and sends `meta@<drive>` a `defect` record naming the job and its
  `Read your brief first:` line.
- At most eight builders hold this Mac's builder slots at once: each builder job here the watch's
  `FREE` line gave its turn for a slot whose agent has not started yet, each agent here still in its turn, and each Orca
  worker started here with `drive job launch --local` while it launches or runs. An agent that ended its turn, on a result line or a question, does not count, nor
  does a launch that started no worker or a lane on a Sprite, nor a job freed from a card, PR or other wait whose agent has not started. `drive job add builder` past eight, or
  while builders wait their turn, records the job to start on a Sprite at once, `where` remote, and
  prints why, naming each builder it counted with its order and when its agent last wrote, and its
  `drive job launch <j> <brief>`. Only a builder whose brief declares a step only this Mac runs, on
  its `Mac:` or `Release:` line below, waits its turn for a slot, held on `builder-slot`;
  `drive status` shows its place, and the watch reads the job waiting, never abandoned.
  A builder on an open incident's order or on an `immediate fix` part never waits its turn: it
  starts here at once past the cap and the line, and holds a slot against later builders once it
  runs.
  A builder added with `drive job add builder --order <o> --goal "<goal>" --builds-nothing`, one whose whole work holds no
  checkout or compiler, such as a merged PR's live proof or a read, holds no slot at all: it starts
  here at once past the cap and the line, and no later builder counts it. `drive job set <j> --builds-nothing`
  marks a builder already added the same way, a waiting one included, so a message wakes it past a full
  cap and one waiting its turn leaves the line.
  Each watch pass counts this Mac's builders once and gives each open slot to the builder job waiting
  its turn that is due first: the earliest time promised him on an order it serves, then the oldest
  added. Its `FREE` line goes once to the session holding its order, which starts its agent then. The
  `agent_brief` hook refuses any other builder here while one waits its turn, naming each builder it
  counted and the line: one whose brief declares no such step starts on a Sprite at once with
  `drive job launch <j> <brief>`, and one whose brief declares one, that no agent runs, it holds waiting its
  turn the same way, unless a wait, a card of his or an open PR of its still holds it.
  Every start here meets this one count, and no judge lifts its refusal: an Agent call, through the
  `agent_brief` hook; `drive job launch --local`, for a first lane or one moving from a Sprite, which
  starts on a Sprite or waits its turn the same way; and a SendMessage that would wake an agent here
  whose turn ended, which the `communicator_calls` hook refuses while the slots are full, and after
  which `drive job resume <j> "<the message>"` hands the job to a lane on a Sprite with the message
  in its handoff.
- A Forge-AI/monorepo builder is placed when it starts, from the step its brief declares, by his pick
  "Decide at start: a brief naming a Mac-only step starts here at any load, others keep the load
  check". `drive job add` never refuses one for load. The brief's writer names the step only this
  Mac runs that the job itself runs with `drive job brief <j> --mac "<the step>"`, its `Mac:` line:
  break-glass, `drive install`, the drive's dashboard, a Mac box (`ci mac-box`) or test Mac, a
  devbox (`ci devbox`) or `ci db-read`. A `Release:` line declares one too, since its
  agent follows and approves the release with this Mac's Buildkite token. `place.mac_step` reads those two lines and no
  other word of the brief, since its fields quote where his words were typed, an alert he shared
  from Datadog, the `Incident:` rule's `tools/ci release start` and what earlier jobs found: no word
  of his `Order`, a `Goal`, `Plan` or `Handoff` places a job. The `agent_brief` hook refuses a
  builder started with the Agent tool while this Mac's CPU is at or over its load gate's limit, the
  `remote` that `drive place` prints, unless its brief declares such a step. Every later start of
  a Sprite job reads the same lines, by the same `place.mac_step`, over its brief, its builder's last
  report, where a `Mac:` line names the step its `PROVE:` needs, and the resume's text: a resume or
  relaunch that finds one starts the next lane in a fresh worktree here, as `--local` does, and
  never in the Sprite's own Claude session. Such a brief starts here
  at any load, and `drive job launch <j>` refuses its Sprite start, naming the step: start it with
  the Agent tool or with `--local`. A brief whose writer left its `Mac:` line out starts on a Sprite,
  which refuses or lacks the step. A refused builder starts on its own Sprite with `drive job launch <j>`,
  which also starts one waiting its turn there at once. A Sprite holds no Buildkite token or Datadog
  key and reads Datadog with `drive datadog`, which runs on this Mac with ai-oncall's keys; its AWS session reads Buildkite only through the sprite door, and `ci mac-box` and `ci devbox` run on this Mac's machine profile
  through `DEVBOX_AWS_PROFILE`, with no sign-in. Readers, codex jobs and any job that touches this
  Mac's processes always run here.

## Start one on this Mac

1. Add the job and write its brief in one `job` call:
   `[{add: {kind: "builder", order: "<o>", goal: "<one line>", part: ["<part>"]}}, {brief: {serves: "<his order's words, a port of #N, or drive tooling>", done_when: "<…>", where: "<repo and branch>", native: "<his surface, with links, or none>", interactions: "<his user stories on it, one per ;>", ruling: ["<answer id>"]}}]`.
2. Start it with the Agent tool: `subagent_type` `long-running:builder` or `long-running:reader`,
   `run_in_background: true`, a short `description`, and the line the `brief` op printed as the
   whole `prompt`. The agent reads its brief file itself, so the brief never passes through your
   context. A codex job runs no agent: start it with `drive job launch <j>`, as
   [Codex jobs](#codex-jobs) says.
3. Pass no model, so the agent runs on the Opus its definition sets, or `model: "sonnet"` only for a
   reader whose question is a one-fact lookup. A design job's model is in [Design first](design.md).

The `agent_brief` hook sets each Agent call to the shape the drive runs it: it drops `name`, since a
named agent joins the agent team and never wakes on events, drops Fable from any agent but
`long-running:designer`, starts a design job as `long-running:designer` whatever type the call
names, and drops `sonnet` or `haiku` from any lane of a build order or of an answer order you marked
`--complex`, so it runs on its definition's model. It refuses a builder whose brief lacks `Serves:`
or `Handoff:`, whose `Serves:` names none of the three, or whose Goal's own action backfills,
bulk-repairs, migrates, converts or repairs, or runs such a pass, with no `Runs as:` line; a Goal
that only names a PR, stack or check for a pass is not one; and a builder, other than one on an
open incident's order or an `immediate fix` part or marked with `--builds-nothing`, while eight hold this Mac's builder slots or one
waits its turn ahead of it, sending one whose brief declares no step only this Mac runs to a
Sprite at once and holding one whose brief declares one, that no agent runs and nothing else holds, waiting
its turn and naming the line, earliest due first. The `scratch_folder` hook refuses a build
order's builder, designer or codex lane whose brief lacks the `Researched:` line the `brief` op
writes once its reader's research is kept, unless its job ships the order's `immediate fix` part.
Each refusal names its fix: do it, never route around it. These hooks run in the long-running
capt-hook pack, which sends each refusal to Jev first: one Jev finds the rule never meant lets the
call through, and a merge, release or public-body refusal of `landing_guard` blocks outright.

The brief's `Drive:` line names the job, or, when it names none, the job `--agent` already recorded
the agent on. The agent's first Bash, Read, Write or Edit call, or its stop, records the agent on that
job, and its first call after a prompt that reached it after its return, such as a `SendMessage`,
marks the job running again, when its type is one the job's kind runs as: `long-running:builder` on a builder, `long-running:reader` on a reader,
`long-running:designer` on a design job, and an incident's second-lane `long-running:builder` on
a codex job; a call or stop after its return with no such prompt leaves
the return standing. Every worker start runs its job, a stopped one included, and ends its turn in line for a builder slot: this record, `drive job launch`, a `drive job resume` of a job not stopped, which refuses a stopped one, and `drive job set <j> --agent` naming a worker other
than the one recorded. Only the agent a stop left on its job keeps the stop, so that agent's later return is refused. An agent of any other type is never recorded this way, even
when its brief, the brief file it names or the record it is briefed with names a job: a
`long-running:handler` started to run `drive job launch <j>` leaves `<j>` to the lane it launches
and its return sets nothing on `<j>`, and an `orca:orca-doctor` or Explore agent that reads a lane's
brief leaves the job to that lane. No agent is recorded over a remote job's dispatch either: its
Orca lane holds it, and `drive job resume` restarts that lane once it dies. A handler holds a job
only when `drive handler --answer` adds a read-only question's reader job with the handler as its
`agent`. `drive job set <j> --agent <the dispatch the job records>` puts a
remote job an agent took back on its own lane, recording the Run, Task, terminal, lane and Sprite
Orca shows for that dispatch, so `drive job resume` reaches it. The first line the agent pipes to `drive job report <j> -` sets the state and said line of every job
an agent holds, and nothing else does but a codex job's run: the stop sends back a line not kept, `drive job set` refuses
every state but `stopped`, and `drive job report` refuses, naming the fix, a job still stopped, a line
opening with no result, a `WAITING` naming nothing the watch frees it on, a design lane's `DESIGN`
that Jev reads as building less or later than his words or answers name with no fork asking him, or a strategy before the
abstraction it shares, and a reader's `DESIGN` that `drive job ask` refuses as his card. Each drive PR, `release:<service>:<owner>/<repo>#<n>` and `<order>:<part>` a
`READY`, `PUSHED` or `WAITING` line names becomes a wait, which the watch's `FREE` line ends once
the PR merges, the job's own PR closes, the release is out or a builder or codex job on the part returns `DONE`, while another job's PR closing unmerged
moves the wait to that job's newest open PR, else to that job until its work is done; a release wait whose service `tools/ci release select
--pr` finds the PR's changed paths never reach is refused, naming the components they reach:

| First line | The job's state |
|---|---|
| `DONE`, or a reader's answer | done |
| `READY`, `PUSHED` or `DESIGN` from any job but a reader, `WAITING`, `ASK` | waiting; an `ASK` keeps as its said line the question's title `drive job ask` set |
| `BLOCKED` | failed |
| `HANDOFF <path>`, a job its session stopped before done | stopped |
| `BUILT <branch>, waits on <cards>` | built on that branch, waiting on each card it names and each open card of his holding its work as the lander reads it, none whose `Holds:` line reads none |

### Codex jobs

A codex job runs no agent. `drive job launch <j>` starts its codex-ask run on this Mac from the
session's folder, `codex-ask --dispatch -s <the job's scratch>/codex --schema <the job's
scratch>/codex-result.json` on the job's brief, with `-m <model>` on a job added with `--model`, the
model his order names, and `--incident` on an incident, after the checks the Agent hooks run on
every other job's brief, and records the run folder on the job. Its brief carries each text the job
holds and each answer of his it has not heard under `His answers since:`, as a first launch does,
and the launch drops those texts off the job and marks it sent those answers. A text held while
the run goes waits for its end: the watch starts no launch while it goes, and its next launch
carries the text. The `scratch_folder` hook refuses a
`codex:` agent started on a codex job's brief. The schema holds codex's final message to three
required fields: `state`, a result word of the table above, `line`, the rest of the result line, and
`report`, the return below it, so no reply lacks its state.

Once the run ends, the watch reads it with `codex-ask --collect` and takes it as the job's return.
A completed run's `state` and `line` set the state and said line by the table above, as every job's
line does, so a `HANDOFF` stops the job and a `READY` waits on its PR, and the line and report are
kept as the job's report. A reply `drive job report` would refuse, such as a cause naming no fix,
sets failed on `BLOCKED codex-ask completed, its reply refused:` and why, with the reply kept; a
failed or dead run sets failed on `BLOCKED codex-ask <state>:` and its log's last line; a run still
going sets nothing. Codex leaves its edits uncommitted, so a codex job that changed code returns
`HANDOFF`, and the builder that ships it is briefed `Handoff: drive job report <the codex job>`.
On an open incident's `fix` whose fix is not live, the watch adds that builder at each return of
the codex job and names it on a `JOB` line for the coordinator to start in that turn.

The watch reads the agent's transcript, never the stored state, and takes no result. A running job
whose agent ended its turn on a result line, ended it 30 minutes ago on any other line, wrote nothing for 30 minutes, or belonged to an earlier
session makes its parts `ORPHAN`s. An agent that ends its turn on a line with
no result word is parked on a task of its own, which usually wakes it; one still parked after 30
minutes is said with the remedy, a `SendMessage` to wake it.

## Start one on a Sprite

1. Make the same `job` call with `remote: true` on its `add`, then run `drive job launch <j>` from
   the monorepo checkout's root with `run_in_background: true`, which sends its brief. A local job
   no agent holds launches the same way.
2. It starts the builder through the `orca` skill's launcher as an Orca worker on a Sprite, on an
   Orca Run of its own that only this drive's watch reads, and its notification says where it
   started or why it could not.
3. When no worker started, start the job here with the Agent tool on its brief and say so in the
   order's next report, never silently. When the failure came after worker-start ran, the lane may
   be live, so a reader checks that attempt before anything starts the job again, unless Orca's
   Run holds no task for its lane, when the attempt counts as one that sent nothing and the next
   launch reuses its Sprite, or Orca answers `Unknown environment` for its Sprite, when nothing it
   started can run and the next launch destroys that Sprite and prepares a fresh one. A launch
   whose worker started and was never recorded needs no reader: `drive job launch <j>` again
   records it from the ready receipt its attempt keeps, or launches afresh once Orca answers
   `Unknown environment` for it.
4. Send a handoff or message to a lane on stdin, `drive job resume <j> -` from a quoted heredoc,
   never in a file you write: Claude Code reads the files a session wrote back into its context
   after every compaction.
5. Lane Runs belong to the drive's own Orca terminal, a shell running no agent that the drive's
   `drive.db` keeps under `terminal`, so Orca types no "You have N orchestration messages" line into your
   session; the watch turns lane mail into `JOB` events. Leave any `orca orchestration check` such
   a line names unrun.
6. A remote job that is not running holds no Sprite: two minutes after a lane returns its line,
   or once its job is stopped, the watch runs `drive job end <j>` on it in a process of its own,
   ahead of any reclaim, its output in the drive's `relaunches.log`, so no session runs it. The job
   keeps the state its line set, with its waits, PRs and carried parts: a `WAITING` job stays
   waiting, so nothing waiting on it is freed, and its next event starts its next lane on a fresh
   Sprite. Whatever the job's state, end saves what its Sprite holds on no remote to GitHub as
   `saved/<lane>/<branch>`, uncommitted changes included, before it destroys the Sprite, and keeps
   the Sprite, naming why, when the save fails. While the job is not finished, end saves before it
   ends the lane, and end's output and that lane's handoff name each branch GitHub holds under
   `saved/<lane>/`, read from its refs, never from what a save printed, or, when GitHub holds
   none, what the save found: that the Sprite held nothing unpushed or uncommitted, or that
   cc-remote records no Sprite under that name, so nothing on it was read or saved. When the Sprite does not
   answer or the save fails, end refuses, ending nothing and naming why, and the watch ends it again
   once it restarts.

## Remote lanes

**Drive verbs.** A lane types the drive's verbs as a job on this Mac does. Its `drive`, which
`drive job launch` has the `orca` skill's launcher place in the Sprite's `~/.local/bin`, asks each
as one blocking Orca question, `DRIVE <its words>` over the JSON of its stdin and of each file a
word names, and the watch queues it as a read. `drive job read` runs it in the coordinator's
checkout with no session of this Mac's, as the lane's own job on its own drive, and answers
all it printed under a first line naming its exit status, each file named by the lane's own path,
which the lane's `drive` prints and exits with. It runs `job report`, `job ask`, `job set --repro`,
`job replies`, `job held`, `pr add`, `pr set --flaky` on a PR its own job carries, `plan search`, `datadog`, `manual` and `status`, its reads on any job as a Mac lane's,
so a lane reads the `job report` or `job replies` of any job its brief names, and a bare `status`
on its own job. It refuses, running nothing, any other verb, such as `pr set --tap` or an order
verb, a write naming another job, which is a `job report` keeping stdin, `job ask`, `job set` or
`pr add`, and another drive. It
refuses `break-glass` too, since its command would run on this Mac as your user, by his pick
"No Sprite lane runs break-glass: a step needing a grant moves to a lane on this Mac": the lane returns `BLOCKED` naming the step and its command, and you move that step to a
lane on this Mac.

**Reports.** A lane keeps its report with `drive job report <j> -` and an `ASK` block with
`drive job ask <j> <file>`, each refusing it as it refuses yours, then ends its turn through Orca
mail: a `worker_done` carrying the report it kept, or an `ASK <path> [<path>…]` question naming each
block `drive job ask` kept. Each is a `JOB <j> <o> <line>` event, as a job's own return is: the report's
result line the moment the watch sends the lane its `drive job report` answer, a `PUSHED` aside,
which waits on its checks, so the `worker_done` after it is no second event, and `ASK <path>` when
that question comes. A report `drive job report` never took, such as one
from a lane launched before its Sprite had `drive`, the watch takes itself, setting the job's state
as a local agent's line does, and its line names each PR a `READY`, `PUSHED` or `PR:` line links
that the drive does not track, since only `drive pr add <url> --job <j>`, which you run, registers
one. A report sent as an Orca question, one whose first line opens with a result word other than
`ASK`, is that result and asks nothing, and a report ends any question the lane asked before it,
since the lane no longer waits on that answer. Any other `ASK` question is a `JOB` line saying that
`drive job ask <j>`, which you run, keeps it. The watch takes each message once: one the job's kept
history already holds is never an event again, so a watch that restarts after an install says
nothing it said before. `drive job report <j>` prints every result the lane sent, newest first, and
`drive job replies <j>` prints every result and question the lane sent and every return
`drive job report <j> -` kept, oldest first with its time, for a line that points at an earlier
reply.

**Reads.** A lane on a Sprite reads a red check's whole log itself, by his pick "Give Sprites a
scoped AWS read role, so every step runs where its lane runs": a Buildkite job's with
`tools/ci bk log` through the sprite door under the Sprite's AWS session, and a GitHub Actions
job's with `ccx vcs job-log`. A Sprite holds no copy of the drive's cci or cc-notes records and no Slack credential, so a
lane reads those, and Slack, through this Mac with a blocking question.

- Its first line opens with the read, after the lane's job id when it writes one: `READ <command>`
  for a `cci`, `ccn` or `cc-slack` read the builder agent lists, or `READ <path>` for a file under the drive's
  `reports/`, `asks/`, `repro/`, `scratch/` or `jobs/` folder, which `orca.drive_files` reads whole
  as it carries one a brief names, refusing a path that resolves outside the folder. A `READ`
  question runs its first line alone. A `LOG <url>` question, which a lane started under the builder
  rules from before this Mac's log read was deleted still asks, reads nothing: its answer is the
  builder rule that has the lane read the log on its Sprite, or return `BLOCKED` when `tools/ci bk`
  refuses there.
- The watch answers it itself, with no event, as it answers a `DRIVE` question: `drive job read`,
  which it starts as a process of its own, runs `cci`, `ccn` or the drive's own cc-slack launcher in
  the directory the coordinator started the drive from, or reads the drive files, and the watch
  replies with the output, a command's cut to 12,000 characters but for a `cc-slack thread` or
  `history` read, which goes whole, once that reader ends, so a read that runs for minutes holds up no pass. The
  reader keeps first the answer its own end leaves, then the one it finishes with, under its
  question's message id, so a refused reply sends the same answer again and a read stopped mid-run
  is answered as stopped, never run twice. A read that fails, such as a command off that list or a tool that exits
  non-zero, replies with why and arrives as a `JOB <j> <o> READ unanswered` line.
- A question that opens any other way, such as `READ: please send <file>`, is a `JOB` line for you
  to answer, its first line, whose record carries the whole question as its body, which cci's read
  tool prints; one opening with another result word is a report, by **Reports**. One
  no resume answered for 30 minutes makes its parts `ORPHAN`s, said once, whatever card or PR
  the job has, since a card drawn before the question asks him something else; when he answered
  its card since, the line leads with his answer and the `drive job resume` that carries it.
- A question kept for you that reads as a `READ`, such as one kept before the watch read it
  that way, the watch answers on its next pass, and the lane runs again.
- A lane whose wait ends waits again on the same question, so the watch keeps each question queued
  until the answer goes through, sending each kept answer at the start of every pass, before it reads
  any lane's mail, past a refused reply or a watch restart, or until Orca says it
  already holds another answer, which drops it with no line. Its
  `WATCH the answer to <j>'s read failed` line needs nothing from you.

**What the watch reads each minute.**

- The lane's Dispatch, from Orca: a lane that sat at its prompt for 30 minutes makes its parts
  `ORPHAN`s, and a running job's lane whose worker died or whose Sprite's Orca environment is gone
  is one `LANE … died` line. A waiting job's dead lane is recorded with no line, its Sprite freed
  by `drive job end`, since his answer reaches the job by `drive job resume` either way.
- A running job's dead lane is relaunched in the pass that finds it: the watch runs
  `drive job resume <j>` as a process of its own, its output in the drive's `relaunches.log`, and
  its `LANE … died` line says so. So is a running job's agent on this Mac whose last response ended
  on an API error, once the session holding it has had a response answered since, so
  its next worker does not start inside the outage that stopped it: its `LANE … died as agent`
  line says the watch ran that resume, which starts the next worker on a Sprite, or on this Mac
  when its brief declares a step only this Mac runs. A running lane on this Mac whose newest transcript
  in its worktree ends the same way, once any session bound to the drive has had a response
  answered since, has the resume prompt the StopFailure hook sends typed into its terminal, once
  per stop, and its `LANE … stopped on this Mac` line says so; after four resumes met the same stop
  the line says the watch types no more. A running job whose agent or lane on this Mac a model's
  safeguards stopped is set `stopped` at once instead, with nothing resumed or relaunched, since
  Claude Code's notice on that stop asks for it to go to him and for no one to do the agent's work:
  a POST report passes the notice to him, its `LANE … stopped by its model's safeguards` line says
  so, its parts make no `ORPHAN`, and `drive job launch <j>` runs it again only on his word. A PR's next step goes to its lane by `drive pr wake`, as its
  stage owes. While its job stays `running`, a lane that finished, or ended with
  no next lane started within 30 minutes, or whose relaunch ended without one, makes its parts
  `ORPHAN`s even when another job or a posted answer carries them, as does work he ordered that only
  a reader, a design with no builder, his card or a post holds. Those lines also name each open PR
  from GitHub on the job's branch or its lane's that the drive does not track, which alone makes no
  line, since a working builder opens its PR before its `drive pr add` registers it.

**Resume and launch.** `drive job resume` and `drive job launch` are one relaunch under the job's
own lock: a second one waits for the first, about 150 s, then acts on the worker it started, so a
resume that arrives while a worker starts reaches that worker as a message. Each checks the worker
before it sends or starts.

- A launch of a job whose lane's worker ended starts its next worker as a resume does, with no
  message; one whose worker takes messages is refused, naming the resume that sends it one. With
  `--local`, a job on a Sprite moves here: what its Sprite holds on no remote is saved to GitHub,
  its next worker starts in a fresh worktree on this Mac whether the last one finished, died, does
  not answer or returned its line and still takes messages, as a Sprite lane does after `BLOCKED`
  on a step only this Mac runs, though a running one stays refused, and
  the Sprite is destroyed once it holds nothing on no remote. A failed save still moves the job,
  keeping the Sprite, and its handoff names what each lane of the job saved on GitHub.
- An agent on this Mac that ended with its session, which no role holds any more, or whose last
  response ended on an API error or a refusal takes no `SendMessage`: either verb hands its job to
  a lane on a Sprite, or on this Mac with `drive job launch <j> --local` or when its brief declares
  a step only this Mac runs, on a handoff carrying the job's brief, branch, last line, report, PRs
  and the message.
- A live worker gets the message: the answer to its open question, or a new message to its Dispatch
  when it asked none or Orca already holds another answer to it. A live Sprite lane no start this
  drive recorded put the tools on, as one started before the drive forwarder existed or recorded by
  hand, first gets the tools a launch puts there, `drive` among them; a refusal is named after the
  send, and the next message tries again. On a finished worker on this Mac, it starts the job's
  next worker in its terminal, on the Run and terminal Orca recorded for that worker, kept even
  when Orca's read shows no terminal because it could not tie it to the worker's process. On a
  finished worker whose Sprite is still up, it prepares that Sprite again with
  `cc-remote orca prepare <Sprite> --existing`, which gives it the tool payload and keys a fresh
  Sprite gets, and starts the next worker there in the checkout the last one left, on a Run this
  terminal makes. A finished worker is read before its liveness, which Orca cannot always tell
  once a worker is done; when Orca no longer finds
  that terminal or worktree, or shows that terminal no longer connected, the worker counts as
  dead, and when its Sprite refuses the tools a worker needs, or was made from a cc-remote config
  other than the one the default branch holds now, as the SHA-256 its launch recorded on the job
  shows, that Sprite is passed over as a dead one's. A dead worker, or one whose
  Sprite's Orca environment is gone, as `drive job end` leaves it, or no longer paired with this
  Mac's Orca (`peer_changed`), is relaunched on a fresh Sprite
  on a Run this terminal makes, since Orca binds a Run only to the terminal that made it. A
  relaunch whose earlier try prepared a Sprite and started no worker on it reuses that Sprite, and
  the launcher copies the new handoff onto it, unless that Sprite's own install refused the tools
  a worker needs, a full disk or a toolchain directory it cannot write: the launcher then destroys
  it and exits 76, and the relaunch prepares one fresh Sprite, failing with his answer unsent when
  that one refuses them too, his answer still held.
- A delivered message, sent to a live worker or carried in the brief or handoff a worker started
  on, marks the job sent each answer of his whose words it holds verbatim, and a first launch adds
  each text the job holds and each answer it has not heard that its brief lacks under `His answers since:`; a `SendMessage`
  from the coordinator session to its agent on this Mac does the same once that agent's transcript
  holds it, as a queued command while the agent works or a meta user row once it waits, which the
  watch and `drive answer` read before either says the job has not heard them.
- Either new worker gets a handoff carrying the job's brief, rebuilt from its `drive job brief`
  call so it holds the rulings and carried orders the drive holds now, branch, last line, report,
  PRs and the message, and the output says which ran.
- A dead lane whose Sprite answers with the Claude session its worker ran there, found by the
  dispatch id in that session's transcript, as after the Sprite's VM restarts, is resumed instead:
  `cc-remote orca prepare --existing --resume <session>` starts that session again in the
  checkout it left, with a short brief saying why it died, which dispatch it reports on now and
  the message, and nothing is saved, moved or destroyed. A resume that starts no worker falls back
  to the relaunch below, its why naming the refusal.
- First, a running job's resume reads one cause line from the logs it copied off the dead lane's
  Sprite: the kernel's lines at error level or worse since it started init, `memory.high` when the
  lane's cgroup went over it, OOM kills, a full home volume and how long the VM and its init process
  had run, or why the Sprite did not answer. The death record, the next worker's brief and the
  resume's output carry it. When another lane of the job died since its last line, this second
  death starts no worker: the resume records it and refuses, naming both causes and both death
  files, and `drive job launch <j> --local` moves the job to a fresh worktree on this Mac.
- Before a relaunch on a fresh Sprite it saves the dead lane's Sprite, found by the job's workspace
  even when Orca dropped its dispatch or environment. Over ssh it commits each worktree's uncommitted
  changes there and pushes each commit, branch or stash GitHub holds nowhere as
  `saved/<the dead lane>/<its branch>`, on the token `gh auth token` prints, lent to that one push
  through the ssh session's stdin, so it never reaches the Sprite's disk. The output and the new
  worker's handoff name each branch GitHub then holds under `saved/<the dead lane>/`, by one read
  of its refs, never from text a save printed, so neither names a branch that is missing. When
  that Sprite does not answer or the save fails, the relaunch runs on that Sprite instead, through
  `cc-remote orca prepare --existing`, in the checkout the dead lane left, and the handoff and the
  output name why the save failed. A Sprite that refused a worker's tools is never run on again: it
  is kept, and the relaunch runs on a fresh Sprite.
- When Orca cannot reach the lane's Sprite, its worker-show refused as `remote_runtime_unavailable`,
  the resume, a launch and `drive pr wake` first wake that Sprite with `orca-reclaim.py --wake`,
  which runs `cc-remote orca reconnect`, then read the lane again. A worker that takes messages gets
  the text. A lane Orca still cannot reach is asked of its Sprite over cc-remote's ssh: with nothing
  listening on the port its cc-remote task runs Orca's runtime on, as when a full disk crash-loops
  that runtime while the lane's Claude works on, the lane is saved as above and relaunched on a
  fresh Sprite with its held texts, the save first deleting `~/.cache` on a home under 64 MiB free;
  with that runtime listening, the text is held and nothing restarts, since Orca on this Mac is what
  fails. Any other lane resumes its own Claude session there, as a dead lane above does, else
  relaunches on that Sprite through `cc-remote orca prepare --existing`, in the checkout it left,
  with no save. When that Sprite starts no worker, the relaunch runs on a
  fresh Sprite from the job's pushed branches, after the save above, and the old Sprite is kept when
  the save fails.
- A remote lane's text is held on the job's undelivered list under one Orca request id before any
  Orca call, so an outage delays it and never drops it. When Orca cannot tell whether the lane
  lives, or refuses the send, the resume exits 0 saying the text is held. A lane on this Mac that
  Orca shows as its own unfinished worker in a terminal it can write to takes the text whatever
  liveness Orca reads, as after an Orca restart, and when Orca reads it not live, the send is
  followed by a line typed into that terminal telling the lane to read its orchestration mail.
  The watch, once Orca reads that worker alive and unfinished, or once a lane that returned has no
  card of his asking it anything, runs `drive job resume <j>` with no text as a process of its own, which sends each held text under the id it was held with, so Orca
  replays a retry and never applies one twice; a relaunch meanwhile carries every held text in its
  handoff, oldest first. A relaunch Orca refuses exits 0 with Orca's refusal, saying the text is still held, which the
  watch carries again ten minutes on; `drive job resume <j>` delivers it once Orca reads the lane.
- The `bash_guard` hook runs it in the foreground with `timeout: 20000` however it is called: a
  message to a live lane returns in about five seconds, and a relaunch, which takes minutes, moves itself to the background at 20 seconds and
  notifies you when it ends.
- A remote lane registers its PR with `drive pr add`, its `--port-of` or `--exempt` included, as a
  job here does, and its `SERVES: port of #N` line still gets the `READY` row's check against #N.

## The brief

The brief is the job's whole context, the same here and on a Sprite. The `brief` op writes each line
below from what the drive holds, `Runs as`, `Release`, `Handoff`, `Plan`, `Incident`, `Native`,
`Interactions` and `Would he ask` from the call's fields when given, and prints
`Read your brief first: <path>`. A brief whose `native` names his surface needs `interactions`
naming his user stories on it, and the op refuses one Jev reads as steps with no result he must see,
by his words "you're supposed to proactively view and click through every user story and have strict
acceptance critera for yourself".

```
Order (his words): <verbatim; for drive tooling, its order's own "Drive tooling: …" words; then the words of each order the job carries, each on its own line>
Linked messages: <each Slack message those words link, read through the cc-slack bot as the brief is written: its link, who posted it and when, its text and each message it shares, so no job reads Slack for the message its order came from; one that does not read is kept as `<link>, not read: <cc-slack's words>`, and `drive job brief` refuses in those words only while nothing stands in for it: no linked message read, no word of his outside the links, and no `Handoff`>
Serves: <"port of #N", "drive tooling" for a fix to a tool the drive runs on, under its own order and never his feature order's, or "his order" when the Order line asks for this build; a job for anything else is never started>
Goal: <the deliverable, one sentence>
Done when: <the check that proves it where he will see or use it, such as the installed command's output, the live post or a picture of each user story, since data fetched from an API proves no screen>
Where: <repo, base branch, existing branch or PR to adopt>
Runs as: <only for a pass, migration, backfill, conversion, bulk or hand repair or any other write of a team's data: the service account in that team's cluster it runs as, and how it starts with no person's role and not from this Mac, such as a pipeline or an in-cluster schedule; `none: <why>` when the hook reads such a write in a Goal that makes none>
Release: <only for a job a release started on: the one `tools/ci release start` or `tools/ci release hotfix` that `drive job add --release` started, written by that call; it reads whether the answer its `--order` names orders that release, by the one reading of his pick [landing.md](landing.md#after-merge) states, unless the answer is an incident's, and the `landing_guard` hook refuses every start or hotfix a drive session or agent runs itself>
Release build: <beside Release only: the build that start printed, which the job's agent follows and approves>
Handoff: <a PR event or a built branch freed only: the PR URLs or the built branch, its head, the event line verbatim, and the INSTALL: and PROVE: lines the first builder returned>
Decisions that bind: <each ruling the call's ruling field names, each one the brief of any job on its order binds, a reader's or a design's included, each that settled a question of the reader Researched names and each the `rulings:` line of every reader of its parts of its order and of each order it carries names by id, the older readers' beside the newest's, whatever words surround it, an id cc-notes keeps no answer under, such as a commit or a session, left out, its answer's title and his words above its `Notes:` word for word, never the drive's notes under it, and every other ruling of his on the whole area this touches, found with answer_search; "none named; search his answers on the area" when none of these names one>
Research: <a build order's reader only: the Researched: block this build needs>
Researched: <a build order's builder, designer or codex lane only: the reader job of its own part, a change a design listed counting as that design's part, whose kept report holds the order's rulings, what exists and its open questions>
Native: <where he sees or uses the result, such as a Slack post or card, the dashboard, a CLI he runs or a product UI, and what that platform already does for it, with links, such as Block Kit's chart and table blocks; "none" for work he never sees>
Interactions: <when Native names his surface: his user stories on the result, one per `;`, each what he does, a click, key or hover, and what he must then see; its job writes each story's strict acceptance criteria into its proof file before building, which `drive pr add` refuses its PR without, and proves each on the installed surface with a picture or recording of it, or the live post's link its proof file reads back, which a builder's or codex job's `DONE` and `drive job prove` need one per story, Jev reading the proof against those criteria; its order is proven done by them or the live post>
Would he ask: <what he would still have to ask for if only his words were built, such as the next piece, a link, or a native piece passed over>
Plan: <the plan section this implements, quoted; a builder the drive added for a design's change gets `<design>'s design, change "<change>": drive job report <design>` from that add, so no brief writer leaves it out and a Sprite lane's brief carries that design's report whole>
Incident: <an incident job only: the record's id, which its owner, the first check or the cause job, keeps and every other job appends what it observed to; roll back to the newest build provably without the failing change, however many releases back; never a revert, which lands only on his tap; when no rollback stops the harm, the stop ships the immediate fix at once as an incident fix; the fix starts at the alert's code path or the failed release's step and is the root fix of the cause, which deletes any immediate fix and leaves no pin, flag, header, prefix rule, cap, retry, timeout raise, timer, wait, cache or special case standing; a card of his that blocks it never stops it, unless his words on the order ask for options and no fix; from the merged fix to its build nothing stops, no dry run, no cover or cause read, no comparison of open builds, no question; the job that merges the fix lifts the service's freeze, named here by its id, with tools/ci release unfreeze <id>; start the build at the fix commit the regular way, by the release rule of reference/incidents.md that `drive job brief` adds to a builder's line, and report its link; the approve hold, which the job reads, is the only check and other reads run while it builds; return the inputs to a write-up, not the write-up; and last, the rule its kind follows on a choice, word for word, which `drive job brief` adds and `brief_refusal` refuses an incident brief without, so its writer types none>
Scratch: <the job's scratch folder>
Transcripts: <the transcript path of every earlier job on this order, so it reads instead of being told; a Sprite lane's brief names each a READ cannot serve, a transcript on this Mac outside the drive's folder or a codex run's folder, as `drive job replies <j>` instead>
Drive: <name>, order <o>, job <j>
```

Ask only for what the builder's definition and the drive's refusals allow, such as no picture of the
live drive or teammate's name in a public repository's PR body, which `drive pr add` refuses.

## What a job returns

A job's result goes to another agent, never to him, so it is the inputs to a write-up, never the
write-up; only the handler that writes to him writes prose.

- The job pipes its whole return on stdin into `drive job report <j> -`, its only report even when
  a delegation hook names a file, because Claude Code refuses a subagent's Write of a report file.
- It returns the first line alone, opening with a result word the state table lists. The
  `one_line_result` hook sends back a final message past one line of 500 characters, and a
  builder's, designer's or build order reader's line until the job's newest return opens with it,
  whatever its said line reads since, such as the watch's `READY refused`, and `drive job report`
  refuses a builder's or designer's report that opens with any other word.
- The rest of the kept report is one fact a line: links, numbers, paths, exact quotes, and what it
  could not prove.
- The job's history, `jobs/<j>.replies.jsonl`, is its report: each `drive job report <j> -` adds
  its return there beside every earlier one, so no return, its own agent's, its lane's or anyone
  else's such as a coordinator restating its result, takes the place of another. Whoever acts on it
  reads it with `drive job report <j>`, which prints the returns the job took, newest first, the
  newest whole, each earlier one as a line naming when the drive kept it and its first line, every
  one whole under `--all`, and an identical return once; an `ASK`
  keeps its block through `drive job ask` instead. `drive job replies <j>` prints each return
  oldest first, beside each message a remote lane sent.

## Rules for every job

His words travel verbatim. A brief's `Order` and `Decisions that bind` carry what he wrote, word for
word, and so does every cc-notes answer: never a paraphrase, a bot's summary, a card option's label
or an answer id in their place. Your reading of his words in a brief is labeled as yours, beside
them, since the job obeys its brief and an unlabeled reading becomes the spec. A tap's label is his
answer only to that card's question. A ruling travels by its words and what they imply through every
compaction, restart, handoff and `SendMessage`; an id carried alone loses them, since an answer's
title is the question and his words are in its body.

His words are never the whole spec. A job whose result he sees or uses does three things its brief's
`Native`, `Decisions that bind` and `Would he ask` lines start it on. It reads that platform's own
docs and builds on what the platform already does over anything hand-made, and its visual design is
codex's, by his words "get codex to design and stop doing the bare minimum". It searches his recorded
answers on the whole area and quotes each one it follows. Before it returns, it clicks through each
user story on that surface as he will, against the criteria it wrote first, and closes each thing he
would still have to ask for.

His approval of a release starts it, right then. His order or card pick to ship, release or approve a
release, or the coordinator's record of either, approves starting that release, never what its approve
hold holds. The top coordinator starts it with `drive job add builder --order <o> --goal "<what it
ships>" --release "<the start> --order <his answer id>"` with no second card or question first. That
one call starts it from the top's checkout, prints its build link and writes the job's brief on that
build. With no such answer, the top reads the release's plan and asks him with `drive job ask` whether
to start that exact command. At the approve hold the job's agent reads each held migration, delete and replace against the
code at the release's commit, then runs `tools/ci release approve <id> --via <agent>`, naming in
`--leave-out` each stack it cannot vouch for, never cancelling the release or asking him, by his pick
"Land whole stacks and read the gate: no schema change merges without its writer; a stack's bottom
waits for its top".

A resume or re-plan of a release he ordered keeps his order. No agent approves a release or its gate
without his approval of that release, and a pick on a card that asked anything else is none. A
release he started himself is his: never cancel, retry, reorder or approve it. An incident's order
approves starting its own cut and rollback, by [incidents.md](incidents.md). While he holds every
release, only his answer recorded after the hold approves one, by [landing.md](landing.md#after-merge).

Answers resume; PR events start fresh. His answer to a job's `ASK` goes back by `SendMessage` to
its agent, or `drive job resume <j> -` with it on stdin for a remote lane or for an agent another
session started, which `SendMessage` cannot reach: there it prints a handoff brief's
`Read your brief first:` line, and the agent you start on it takes the job as yours. A PR event after a builder
returned gets a fresh builder, since a resumed one re-reads all it ever did on every event: the
`communicator_calls` hook refuses `SendMessage` to a builder that returned `BUILT` or `DONE` outside
an incident. Run `drive job add builder` for the same order and parts, which takes each PR the old job opened and stops it
`handed to <new>`, since a part has one change job, then write its brief with the `Handoff:` line and every earlier
job's transcript under `Transcripts:`.

A built job's `FREE` line is a PR event too: the fresh builder's `Handoff:` names the built branch,
and `Decisions that bind` carries his answer to each card it waited on, verbatim. An incident's job
is the one exception, resumed for every event by [incidents.md](incidents.md), because minutes to
users matter more there than tokens. Each builder, a remote lane's included, compacts at 300,000
tokens; neither session's own window changes.

A reproduction leaves one file. A job that reproduces a bug, of any kind, saves one pick-up-and-run
file before it returns: the steps in order, the exact prompt and code, the build and versions, and
the numbers measured. It records the file with `drive job set <j> --repro <file>`, which keeps a
copy that `drive status` lists, so a share reads it instead of rebuilding the steps from a
transcript. A brief for such a job says so. A reproduction's scratch drive, one under
`LONG_RUNNING_STATE`, gets no watch process from `drive start` or `drive watch`, so the file runs
the watch's passes in-process with `watch.run_pass`.

One deliverable per job, one job per deliverable, and no job waits on another. Before a job starts on
a PR, branch, worktree or finding, read who holds it: `drive status` for the drive's jobs and PRs,
and `cci who --pr <n>` for any session's claim on that PR. Work someone holds goes to its holder by
`SendMessage`, `drive job resume`, or its order added with `--joins`, and no second job starts on it,
since two owners each restack, watch and fix the same change and their PRs fold into one later. A job
whose chain stops moving gets the watch's `STUCK` line once, by [The coordinator](coordinator.md),
and its owner takes that line's NEXT step that turn. When B needs A merged, B starts on A's `DONE`.

A sweep over many units is one job running a Workflow, and you start it: a builder has no Agent
tool, so the session that owns a job starts every agent its work needs.

A job's state is its transcript. Before you report on a running job or message it, read its
transcript: `cc-transcript` on the file its agent id names, or for a remote lane `drive job replies
<j>` or `orca orchestration worker-read --dispatch <its dispatch> --limit 50 --full --json`. An
absent message is never evidence. The `communicator_calls` hook refuses a `SendMessage` or
`drive job resume` that tells a running job it stopped, stalled, went quiet or is stuck, or asks it
whether it is, such as `status?`, with no such read in your last 10 minutes or earlier in the same
command. Only words about the job a message goes to count: a question whose subject is another job
or CI, such as `is CI still running?`, and another `drive job resume`'s words in the same command
are not about it, and each resume in a command is checked on its own words. An order, ruling, answer
or relayed refusal goes through unread, even one that says stop, land or stuck.

A close job closes only what he named: each session or terminal his words or tapped card name, by
its exact id after a fresh idle check and never a live drive lane, returning the closed count with
the ids. A close cannot be undone, so his naming is the whole authorization.

A stack draws one card whatever its size. The card names every PR, and the PR sections past the 50
blocks one Slack message holds open under its Explain more.
