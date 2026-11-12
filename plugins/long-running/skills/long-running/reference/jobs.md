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
  question is the communicator's, under its `answer` order. An order of the coordinator's whose
  builder serves drive tooling becomes the meta session's when
  `drive job brief <j> --serves "drive tooling"` writes that brief: from then on only the meta
  session starts its agents and posts its reports, and the watch sends it the order's lines. The
  coordinator starts no agent on it and sends `meta@<drive>` a `defect` record naming the job and its
  `Read your brief first:` line.
- At most eight builders run on this Mac at once: each agent here still in its turn, and each Orca
  worker started here with `drive job launch --local` while it launches or runs. An agent that ended
  its turn, on a result line or a question, does not count, nor does a launch that started no worker
  or a lane on a Sprite. `drive job add builder` records a ninth with no agent, waiting for a slot, and
  names each builder it counted with its order and when its agent last wrote. Before you start a
  Forge-AI/monorepo builder, run `drive place` and start it where it says. It answers `remote` when
  this Mac's CPU is at or over its load gate's limit or eight builders run here, and `local`
  otherwise; a remote builder runs its builds and checks on its own Sprite. Past eight, the
  `agent_brief` hook refuses a ninth builder here, names each builder it counted the same way, and
  names the builder jobs waiting for one, oldest due first: the earliest time promised him on an
  order each serves, then the oldest added. When a builder here finishes, start the first of them.
- Readers, codex jobs, builders on any other repository (a yasyf/* tool installs on this Mac), any
  job that touches this Mac's processes, a handoff whose `PROVE:` line runs `ci mac-box` or
  `ci devbox`, and a job whose brief or handoff runs `drive break-glass`, by his pick "No Sprite lane runs break-glass: a step needing a grant moves to a lane on this Mac", always
  run here: while `drive place` says remote, add a monorepo one with `--remote` and start it with
  `drive job launch <j> --local`, which refuses on a Sprite a brief that runs `drive break-glass`. A Sprite holds no AWS credentials, and `ci mac-box` and `ci devbox` run on this Mac's
  machine profile through `DEVBOX_AWS_PROFILE`, with no sign-in.

## Start one on this Mac

1. Add the job and write its brief in one `job` call:
   `[{add: {kind: "builder", order: "<o>", goal: "<one line>", part: ["<part>"]}}, {brief: {serves: "<his order's words, a port of #N, or drive tooling>", done_when: "<…>", where: "<repo and branch>", ruling: ["<answer id>"]}}]`.
2. Start it with the Agent tool: `subagent_type` `long-running:builder`, `long-running:reader` or
   `codex:codex-wrapper`, `run_in_background: true`, a short `description`, and the line the
   `brief` op printed as the whole `prompt`. The agent reads its brief file itself, so the brief
   never passes through your context.
3. Pass no model, so the agent runs on the Opus its definition sets, or `model: "sonnet"` only for a
   reader whose question is a one-fact lookup. A design job's model is in [Design first](design.md).

The `agent_brief` hook sets each Agent call to the shape the drive runs it: it drops `name`, since a
named agent joins the agent team and never wakes on events, drops Fable from any agent but
`long-running:designer`, starts a design job as `long-running:designer` whatever type the call
names, and drops `sonnet` or `haiku` from any lane of a build order or of an answer order you marked
`--complex`, so it runs on its definition's model. It refuses a builder whose brief lacks `Serves:`
or `Handoff:`, whose `Serves:` names none of the three, or whose Goal's own action backfills,
bulk-repairs, migrates, converts or repairs, or runs such a pass, with no `Runs as:` line; a Goal
that only names a PR, stack or check for a pass is not one; and a builder while eight run on this
Mac, naming the builder jobs waiting, oldest due first. The `scratch_folder` hook refuses a build
order's builder, designer or codex lane whose brief lacks the `Researched:` line the `brief` op
writes once its reader's research is kept, unless its job ships the order's `immediate fix` part.
Each refusal names its fix: do it, never route around it. These hooks run in the long-running
capt-hook pack, which sends each refusal to Jev first: one Jev finds the rule never meant lets the
call through, and a merge, release or public-body refusal of `landing_guard` blocks outright.

The brief's `Drive:` line names the job, or, when it names none, the job `--agent` already recorded
the agent on. The agent's first Bash, Read, Write or Edit call, or its stop, records the agent on that
job, and its first call after a prompt that reached it after its return, such as a `SendMessage`,
marks the job running again, when its type is one the job's kind runs as: `long-running:builder` on a builder, `long-running:reader` on a reader,
`long-running:designer` on a design job, and `codex:codex-wrapper` or an incident's second-lane
`long-running:builder` on a codex job; a call or stop after its return with no such prompt leaves
the return standing. An agent of any other type is never recorded this way, even
when its brief, the brief file it names or the record it is briefed with names a job: a
`long-running:handler` started to run `drive job launch <j>` leaves `<j>` to the lane it launches
and its return sets nothing on `<j>`, and an `orca:orca-doctor` or Explore agent that reads a lane's
brief leaves the job to that lane. No agent is recorded over a remote job's dispatch either: its
Orca lane holds it, and `drive job resume` restarts that lane once it dies. A handler holds a job
only when the communicator names it with `agent`, the id the `handler` tool returned, as it adds a
read-only question's reader job. `drive job set <j> --agent <the dispatch the job records>` puts a
remote job an agent took back on its own lane, recording the Run, Task, terminal, lane and Sprite
Orca shows for that dispatch, so `drive job resume` reaches it. The first line the agent pipes to `drive job report <j> -` sets the state and said line of every job
but a codex job, and nothing else does: the stop sends back a line not kept, `drive job set` refuses
every state but `stopped`, and `drive job report` refuses, naming the fix, a stopped job, a line
opening with no result, a `WAITING` naming nothing the watch frees it on, a design lane's `DESIGN`
whose `Fork:` line names a file keeping no question of the job, and a reader's `DESIGN` that
`drive job ask` refuses as his card. Each drive PR and `release:<service>:<owner>/<repo>#<n>` a
`READY`, `PUSHED` or `WAITING` line names becomes a wait, which the watch's `FREE` line ends once
the PR merges or closes or the release is out:

| First line | The job's state |
|---|---|
| `DONE`, or a reader's answer | done |
| `READY`, `PUSHED` or `DESIGN` from any job but a reader, `WAITING`, `ASK` | waiting; an `ASK` keeps as its said line the question's title `drive job ask` set |
| `BLOCKED` | failed |
| `HANDOFF <path>`, a job its session stopped before done | stopped |
| `BUILT <branch>, waits on <cards>` | built on that branch, waiting on each card it names |

A codex job's state comes from the codex-ask run its relay asked, never from the relay's line. At the
relay's stop, and on each watch pass once its turn ended, the drive runs `codex-ask --collect` on the
run folder of the newest `REPLY_FILE:` line codex-ask printed into the relay's tool results. A
completed run sets done, or failed when codex's reply opens `BLOCKED`, with the reply's first line as
the said line and the whole reply kept as the job's report; a failed or dead run sets failed on
`BLOCKED codex-ask <state>:` and its log's last line; a run still going sets nothing and raises no
gap, whatever the relay ended on. A relay that asked codex nothing is parked like any agent below.

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
   be live, so a reader checks that attempt before anything starts the job again. A launch whose
   worker started and was never recorded needs no reader: `drive job launch <j>` again records it
   from the ready receipt its attempt keeps.
4. Send a handoff or message to a lane on stdin, `drive job resume <j> -` from a quoted heredoc,
   never in a file you write: Claude Code reads the files a session wrote back into its context
   after every compaction.
5. A "You have N orchestration messages" line Orca types into your terminal needs nothing: the watch
   turns that mail into `JOB` events, so leave the `orca orchestration check` it names unrun. Your
   terminal holds a lane's Run only while a call starts a worker or sends an answer.
6. When a remote lane returns its line, run `drive job end <j>` in the background to free its
   Sprite, as the coordinator page's event rows say. The job keeps the state its line set, with its
   waits, PRs and carried parts: a `WAITING` job stays waiting, so nothing waiting on it is freed,
   and its next event starts its next lane. While the job is not finished, end first saves what its
   Sprite holds on no remote to GitHub as `saved/<lane>/<branch>`, uncommitted changes included,
   since the next lane starts on a fresh Sprite, and that lane's handoff names each saved branch.
   When the Sprite does not answer or the save fails, end refuses, ending nothing and naming why:
   end it again once the Sprite answers.

## Remote lanes

**Drive verbs.** A lane types the drive's verbs as a job on this Mac does. Its `drive`, which
`drive job launch` has the `orca` skill's launcher place in the Sprite's `~/.local/bin`, asks each
as one blocking Orca question, `DRIVE <its words>` over the JSON of its stdin and of each file a
word names, and the watch queues it as a read. `drive job read` runs it in the coordinator's
checkout with no session of this Mac's, on the lane's own job and drive alone, and answers
what it printed under a first line naming its exit status, each file named by the lane's own path,
which the lane's `drive` prints and exits with. It runs `job report`, `job ask`, `job set --repro`,
`job replies`, `pr add`, `plan search`, `manual` and `status`, and refuses, running
nothing, any other verb, such as `pr set --tap` or an order verb, another job or another drive. It
refuses `break-glass` too, since its command would run on this Mac as your user, by his pick
"No Sprite lane runs break-glass: a step needing a grant moves to a lane on this Mac": the lane returns `BLOCKED` naming the step and its command, and you move that step to a
lane on this Mac.

**Reports.** A lane keeps its report with `drive job report <j> -` and an `ASK` block with
`drive job ask <j> <file>`, each refusing it as it refuses yours, then ends its turn through Orca
mail: a `worker_done` carrying the report it kept, or an `ASK <path>` question naming the block
`drive job ask` kept. Each is a `JOB <j> <o> <line>` event, as a job's own return is: the report's
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

**Reads.** A Sprite holds no Buildkite or AWS credentials, by his least-privilege ruling, and no
copy of the drive's cci or cc-notes records, so a lane reads through this Mac with a blocking
question.

- Its first line opens with the read, after the lane's job id when it writes one: `LOG <url>` for a
  red Buildkite or GitHub Actions check, or `READ <command>` for a `cci` or `ccn` read the builder
  agent lists. `READ: LOG <url>` reads as `LOG <url>`. A `LOG` question reads every `LOG <url>` it
  names, each in an even share of the 12,000 characters, and a `READ` question runs its first line
  alone.
- The watch answers it itself, with no event, as it answers a `DRIVE` question: `drive job read`,
  which it starts as a process of its own, runs `tools/ci bk`, `ccx vcs job-log`, `cci` or `ccn` in
  the directory the coordinator started the drive from, and the watch replies with the output, cut
  to 12,000 characters, once that reader ends, so a read that runs for minutes holds up no pass. The
  reader keeps first the answer its own end leaves, then the one it finishes with, under its
  question's message id, so a refused reply sends the same answer again and a read stopped mid-run
  is answered as stopped, never run twice. A `LOG` answer leads each failed job's share with its failure lines, such as a
  Go `--- FAIL:` line or a Rust `panicked at`, then each failure's own output, a Go test's lines
  from its `=== RUN` and anything else's failure line with the two lines after it, then fills the
  rest with the output's last characters. A test's failure often sits far before the end of its
  log, and a passing Go test's own log lines never come ahead of a failing one's. A read that fails, such as a command off that list or a tool that exits
  non-zero, replies with why and arrives as a `JOB <j> <o> READ unanswered` line.
- A question that opens any other way, such as `READ: please send <file>`, is a `JOB` line for you
  to answer; one opening with another result word is a report, by **Reports**. One that names a `LOG <url>` past its first line says so on its line, and
  `drive job resume` reads each such log ahead of your text, so start no reader for it. One
  no resume answered for 30 minutes makes its parts `ORPHAN`s every 30 minutes, whatever card or PR
  the job has, since a card drawn before the question asks him something else; when he answered
  its card since, the line leads with his answer and the `drive job resume` that carries it.
- A question kept for you that reads as a `LOG` or `READ`, such as one kept before the watch read it
  that way, the watch answers on its next pass, and the lane runs again.
- A lane whose wait ends waits again on the same question, so the watch keeps each question queued
  until the answer goes through, past a refused reply or a watch restart, or until Orca says it
  already holds another answer, which drops it with no line. Its
  `WATCH the answer to <j>'s read failed` line needs nothing from you.

**What the watch reads each minute.**

- The lane's Dispatch, from Orca: a lane that sat at its prompt for 30 minutes makes its parts
  `ORPHAN`s, and a lane whose worker died or whose Sprite's Orca environment is gone, running or
  waiting, is one `LANE … died` line.
- A running job's dead lane is relaunched in the pass that finds it: the watch runs
  `drive job resume <j>` as a process of its own, its output in the drive's `relaunches.log`, and
  its `LANE … died` line says so. While its job stays `running`, a lane that finished, or ended with
  no next lane started within 30 minutes, or whose relaunch ended without one, makes its parts
  `ORPHAN`s even when another job or a posted answer carries them. So does an open PR from GitHub on
  the job's branch or its lane's that the drive does not track.

**Resume.** `drive job resume` checks the worker before it sends.

- A live worker gets the message: the answer to its open question, or a new message to its Dispatch
  when it asked none or Orca already holds another answer to it. On a finished worker whose Sprite is still up, it starts the job's
  next worker in its terminal, on the Run and terminal Orca shows for that worker; when Orca no
  longer finds that terminal or worktree, the worker counts as dead. A dead worker, or one whose
  Sprite's Orca environment is gone, as `drive job end` leaves it, is relaunched on a fresh Sprite
  on a Run this terminal makes, since Orca binds a Run only to the terminal that made it. A
  relaunch whose earlier try prepared a Sprite and started no worker on it reuses that Sprite, and
  the launcher copies the new handoff onto it.
- Either new worker gets a handoff carrying the job's brief, branch, last line, report, PRs and the
  message, and the output says which ran.
- Before a relaunch on a fresh Sprite it saves the dead lane's Sprite, found by the job's workspace
  even when Orca dropped its dispatch or environment. Over ssh it commits each worktree's uncommitted
  changes there and pushes each commit, branch or stash GitHub holds nowhere as
  `saved/<the dead lane>/<its branch>`, on the token `gh auth token` prints, lent to that one push
  through the ssh session's stdin, so it never reaches the Sprite's disk. The output and the new
  worker's handoff name each saved branch. When that Sprite does not answer or the save fails, the
  relaunch runs on that Sprite instead, through `cc-remote orca prepare --existing`, in the checkout
  the dead lane left, and the handoff and the output name why the save failed.
- When Orca cannot reach the lane's Sprite, or refuses the send or the start, nothing is delivered
  and it exits 2 with Orca's refusal; resume again once the watch reads the lane.
- The `bash_guard` hook runs it in the foreground with `timeout: 20000` however it is called: a
  message to a live lane returns in about five seconds, and a relaunch, which takes minutes, moves itself to the background at 20 seconds and
  notifies you when it ends.
- A remote lane registers its PR with `drive pr add`, its `--port-of` or `--exempt` included, as a
  job here does, and its `SERVES: port of #N` line still gets the `READY` row's check against #N.

## The brief

The brief is the job's whole context, the same here and on a Sprite. The `brief` op writes each line
below from what the drive holds, `Runs as`, `Handoff`, `Plan` and `Incident` from the call's fields
when given, and prints `Read your brief first: <path>`. It has no field for `Native` or
`Would he ask`: for a job whose result he sees or uses, add those two lines to the prompt below the
line it printed.

```
Order (his words): <verbatim; for drive tooling, its order's own "Drive tooling: …" words>
Serves: <"port of #N", "drive tooling" for a fix to a tool the drive runs on, under its own order and never his feature order's, or "his order" when the Order line asks for this build; a job for anything else is never started>
Goal: <the deliverable, one sentence>
Done when: <the check that proves it where he will see or use it, such as the installed command's output, the live post or a recording of each click, since data fetched from an API proves no screen>
Where: <repo, base branch, existing branch or PR to adopt>
Runs as: <only for a pass, migration, backfill, conversion or bulk repair over a team's data: the service account in that team's cluster it runs as, and how it starts with no person's role, such as a pipeline or an in-cluster schedule; `none: <why>` when the hook reads a pass in a Goal that runs none>
Handoff: <a PR event or a built branch freed only: the PR URLs or the built branch, its head, the event line verbatim, and the INSTALL: and PROVE: lines the first builder returned>
Decisions that bind: <each ruling the call's ruling field names and each that settled a question of the reader Researched names, its answer's title and body word for word, and every other ruling of his on the whole area this touches, found with answer_search; "none named; search his answers on the area" when the call names none>
Research: <a build order's reader only: the Researched: block this build needs>
Researched: <a build order's builder, designer or codex lane only: the reader job whose kept report holds the order's rulings, what exists and its open questions>
Native: <where he sees or uses the result, such as a Slack post or card, the dashboard, a CLI he runs or a product UI, and what that platform already does for it, with links, such as Block Kit's chart and table blocks; "none" for work he never sees>
Interactions: <when Native names his surface: each click or step he takes on the result, which the builder walks through and records; its order is proven done by that recording or the live post>
Would he ask: <what he would still have to ask for if only his words were built, such as the next piece, a link, or a native piece passed over>
Plan: <the plan section this implements, quoted>
Incident: <an incident job only: the record's id, which its owner, the first check or the cause job, keeps and every other job appends what it observed to; roll back to the newest build provably without the failing change, however many releases back; never a revert, which lands only on his tap; when no rollback stops the harm, the stop ships the immediate fix at once as an incident fix; the fix starts at the alert's code path or the failed release's step and is the root fix of the cause, which deletes any immediate fix and leaves no pin, flag, header, prefix rule, cap, retry, timeout raise, timer, wait, cache or special case standing; a card of his that blocks it never stops it, unless his words on the order ask for options and no fix; from the merged fix to its build nothing stops, no dry run, no cover or cause read, no comparison of open builds, no question; the job that merges the fix lifts the service's freeze, named here by its id, with tools/ci release unfreeze <id>; start the build at the fix commit with the cut reference/incidents.md names and report its link; the approve gate is the only check and reads run while it builds; return the inputs to a write-up, not the write-up; and last, the rule its kind follows on a choice, word for word, which `drive job brief` adds and `brief_refusal` refuses an incident brief without: a codex lane's "a choice never stops it: write the ASK block to a file in your scratch, build its recommended option in the same run, and return your result with an ASK: <that file> line, which puts his card up as you return; never BLOCKED on a choice", a builder's "a choice never stops it: return ASK <path> the moment the fork shows, which puts his card up, and build its recommended option when the coordinator resumes you in that turn; never BLOCKED on a choice">
Scratch: <the job's scratch folder>
Transcripts: <the transcript path of every earlier job on this order, so it reads instead of being told>
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
  reads it with `drive job report <j>`, which prints every return the job took, newest first, each
  earlier one under a line naming when the drive kept it, and an identical return once; an `ASK`
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
docs and builds on what the platform already does over anything hand-made. It searches his recorded
answers on the whole area and quotes each one it follows. Before it returns, it meets its result as
he will, on that surface, and closes each thing he would still have to ask for.

His approval of a release approves it, right then. His order or card pick to ship, release or
approve a release, or the coordinator's record of either, approves that release and its approve gate
at once, any migration, delete or replace included. The job passes `--order <his answer id>` to
`tools/ci release start`, or runs `tools/ci release approve <id> --via <agent>` the moment it holds
at approve, with no second card, check or question between.

A resume or re-plan of a release he ordered keeps his order. No agent approves a release or its gate
without his approval of that release, and a pick on a card that asked anything else is none. A
release he started himself is his: never cancel, retry, reorder or approve it. An incident's order
approves its own cut and rollback, by [incidents.md](incidents.md).

Answers resume; PR events start fresh. His answer to a job's `ASK` goes back by `SendMessage` to
its agent, or `drive job resume <j> -` with it on stdin for a remote lane or for an agent another
session started, which `SendMessage` cannot reach: there it prints a handoff brief's
`Read your brief first:` line, and the agent you start on it takes the job as yours. A PR event after a builder
returned gets a fresh builder, since a resumed one re-reads all it ever did on every event: the
`communicator_calls` hook refuses `SendMessage` to a builder that returned `BUILT` or `DONE` outside
an incident. Run `drive job add builder` for the same order and parts and `drive pr add <url> --job <new>` for each PR it takes over, write its brief with the
`Handoff:` line and every earlier job's transcript under `Transcripts:`, then run
`drive job set <old> --state stopped --said "handed to <new>"`.

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

One deliverable per job, and no job waits on another. When B needs A merged, B starts on A's `DONE`.

A sweep over many units is one job running a Workflow, and you start it: a builder has no Agent
tool, so the session that owns a job starts every agent its work needs.

A job's state is its transcript. Before you report on a running job or message it, read its
transcript: `cc-transcript` on the file its agent id names, or for a remote lane `drive job replies
<j>` or `orca orchestration worker-read --dispatch <its dispatch> --limit 50 --full --json`. An
absent message is never evidence. The `communicator_calls` hook refuses a `SendMessage` or
`drive job resume` that tells a running job it stopped, stalled, went quiet or is stuck, or asks it
whether it is, such as `status?`, with no such read in your last 10 minutes or earlier in the same
command. An order, ruling, answer or relayed refusal goes through unread, even one that says stop,
land or stuck.

A close job closes only what he named: each session or terminal his words or tapped card name, by
its exact id after a fresh idle check and never a live drive lane, returning the closed count with
the ids. A close cannot be undone, so his naming is the whole authorization.

A stack draws one card whatever its size. The card names every PR, and the PR sections past the 50
blocks one Slack message holds open under its Explain more.
