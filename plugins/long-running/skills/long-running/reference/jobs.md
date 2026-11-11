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

- Every job is the coordinator's except a drive tooling order's reader, designer and builder, which
  the communicator starts under its own "Drive tooling:" order, by
  [Tooling](communicator.md#tooling).
- At most eight builders run on this Mac at once; remote lanes do not count. Before you start a
  Forge-AI/monorepo builder, run `drive place` and start it where it says. It answers `remote` when
  this Mac's CPU is at or over its load gate's limit or eight builders run here, and `local`
  otherwise; a remote builder runs its builds and checks on its own Sprite. Past eight, a builder on
  any other repository waits as `open`, oldest due first.
- Readers, codex jobs, builders on any other repository (a yasyf/* tool installs on this Mac), any
  job that touches this Mac's processes, and a handoff whose `PROVE:` line runs `ci mac-box` or
  `ci devbox` always run here. A Sprite holds no AWS sign-in, and both verbs sign in with this Mac's
  `DEVBOX_AWS_PROFILE`.

## Start one on this Mac

1. Add the job and write its brief in one `job` call:
   `[{add: {kind: "builder", order: "<o>", goal: "<one line>", part: ["<part>"]}}, {brief: {serves: "<his order's words, a port of #N, or drive tooling>", done_when: "<…>", where: "<repo and branch>", ruling: ["<answer id>"]}}]`.
2. Start it with the Agent tool: `subagent_type` `long-running:builder`, `long-running:reader` or
   `codex:codex-wrapper`, `run_in_background: true`, a short `description`, and the line the
   `brief` op printed as the whole `prompt`. The agent reads its brief file itself, so the brief
   never passes through your context.
3. Pass no model, so the agent runs on the Opus its definition sets, or `model: "sonnet"` only for a
   reader whose question is a one-fact lookup, since a read needing research or judgment runs on
   Opus from the start. A design job's model is in [Design first](design.md).

The `agent_brief` hook refuses an Agent call that sets `name`, since agent teams are on and a named
agent becomes a teammate that never wakes on events. It also refuses Fable on any agent but
`long-running:designer`, and a builder whose brief has no `Serves:` or `Handoff:` line. The
`scratch_folder` hook refuses `sonnet` or `haiku` on any lane of a build order or a complex answer
order, and a build order's builder or designer whose brief lacks the `Researched:` line the `brief`
op writes once its reader's research is kept. Each refusal names its fix: do it, never route around
it.

The brief's `Drive:` line names the job. The agent's first Bash, Read, Write or Edit call, or its
stop, records the agent on that job, and its first call after a `SendMessage` marks the job running
again. The line the agent returns, or the first line it pipes to `drive job report <j> -`, sets the
job's state and its said line:

| First line | The job's state |
|---|---|
| `DONE`, or a reader's answer | done |
| `READY`, `WAITING`, `DESIGN`, `ASK` | waiting; an `ASK` keeps as its said line the question's title `drive job ask` set |
| `BLOCKED` | failed |
| `BUILT` | set by hand: `drive job set <j> --state built --branch <branch> --waits <card>` |

The watch reads the agent's transcript, never the stored state. A running job whose agent ended its
turn on a result line, ended it 30 minutes ago on any other line, wrote nothing for 30 minutes, or
belonged to an earlier session makes its parts `ORPHAN`s. An agent that ends its turn on a line with
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
   be live, so a reader checks that attempt before anything starts the job again.
4. Send a handoff or message to a lane on stdin, `drive job resume <j> -`, never in a file you
   write: Claude Code reads the files a session wrote back into its context after every compaction.
5. A "You have N orchestration messages" line Orca types into your terminal needs nothing: the watch
   turns that mail into `JOB` events, so leave the `orca orchestration check` it names unrun. Your
   terminal holds a lane's Run only while a call starts a worker or sends an answer.
6. When a remote job is done, run `drive job end <j>` in the background to free its Sprite, as the
   coordinator page's event rows say.

## Remote lanes

**Reports.** A lane reports through Orca mail. The drive's watch turns each report into a
`JOB <j> <o> <line>` event opening with the result line of the lane's final report, which sets the
job's state as a local agent's line does. `drive status --json` holds the whole latest report, and
`drive job replies <j>` prints every result and question the lane sent, oldest first with its time,
for a line that points at an earlier reply.

**Reads.** A Sprite holds no Buildkite or AWS credentials, by his least-privilege ruling, and no
copy of the drive's cci or cc-notes records, so a lane reads through this Mac with a blocking
question.

- Its first line opens with the read, after the lane's job id when it writes one: `LOG <url>` for a
  red Buildkite or GitHub Actions check, or `READ <command>` for a `cci` or `ccn` read the builder
  agent lists. `READ: LOG <url>` reads as `LOG <url>`, and text after a `LOG` URL is ignored.
- The watch answers it itself, with no event: it runs `tools/ci bk`, `ccx vcs job-log`, `cci` or
  `ccn` in the directory the coordinator started the drive from and replies with the output, cut to
  12,000 characters. A read that fails, such as a command off that list or a tool that exits
  non-zero, replies with why and arrives as a `JOB <j> <o> READ unanswered` line.
- A question that opens any other way, such as `READ: please send <file>`, is a `JOB` line for you
  to answer.
- A lane whose wait ends waits again on the same question, so the watch keeps each question queued
  until the answer goes through, past a refused reply or a watch restart. Its
  `WATCH the answer to <j>'s read failed` line needs nothing from you.

**What the watch reads each minute.**

- Each PR a lane's `READY` or `PR:` line links becomes that job's, wanting his tap.
- The lane's Dispatch, from Orca: a lane that sat at its prompt for 30 minutes makes its parts
  `ORPHAN`s, and a lane whose worker died, running or waiting, is one `LANE … died` line.
- While its job stays `running`, a lane that finished, or ended and was not resumed within 30
  minutes, makes its parts `ORPHAN`s even when another job or a posted answer carries them. So does
  an open PR from GitHub on the job's branch or its lane's that the drive does not track.

**Resume.** `drive job resume` checks the worker before it sends.

- A live worker gets the message. On a finished worker whose Sprite is still up, it starts the job's
  next worker in its terminal; a dead worker is relaunched on a fresh Sprite on the same Run.
- Either new worker gets a handoff carrying the job's brief, branch, last line, report, PRs and the
  message, and the output says which ran. Work the dead lane had not pushed is lost.
- When Orca cannot reach the lane's Sprite, or refuses the send or the start, nothing is delivered
  and it exits 2 with Orca's refusal; resume again once the watch reads the lane.
- Run it in the foreground with `timeout: 20000`: a message to a live lane returns in about five
  seconds, and a relaunch, which takes minutes, moves itself to the background at 20 seconds and
  notifies you when it ends.
- A remote lane's `EXEMPT:` line still gets `drive pr set <url> --exempt <class>`, and its
  `SERVES: port of #N` line the `READY` row's check against #N.

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
Done when: <the check that proves it where he will see or use it, such as the installed command's output, the live post or a recording of each click, since data fetched from an API proves no screen; every PR releasable alone, by reference/landing.md: a release cut at its merge commit is safe by itself, its targets in any order, with nothing run after it, so a change that needs a later step is split until each PR is safe alone>
Where: <repo, base branch, existing branch or PR to adopt>
Runs as: <only for a pass, migration, backfill, conversion or bulk repair over a team's data: the service account in that team's cluster it runs as, and how it starts with no person's role, such as a pipeline or an in-cluster schedule>
Handoff: <a PR event or a built branch freed only: the PR URLs or the built branch, its head, the event line verbatim, and the INSTALL: and PROVE: lines the first builder returned>
Decisions that bind: <each ruling the call's ruling field names, its answer's title and body word for word, and every other ruling of his on the whole area this touches, found with answer_search; "none named; search his answers on the area" when the call names none>
Researched: <a build order's builder or designer only: the reader job whose kept report holds the order's rulings, what exists and its open questions>
Native: <where he sees or uses the result, such as a Slack post or card, the dashboard, a CLI he runs or a product UI, and what that platform already does for it, with links, such as Block Kit's chart and table blocks; "none" for work he never sees>
Would he ask: <what he would still have to ask for if only his words were built, such as the next piece, a link, or a native piece passed over>
Plan: <the plan section this implements, quoted>
Incident: <an incident job only: the record's id, which its owner, the first check or the cause job, keeps and every other job appends what it observed to; roll back to the newest build provably without the failing change, however many releases back; never a revert, which lands only on his tap; the fix starts at the alert's code path or the failed release's step and is the root fix of the cause, never a pin, flag, header, prefix rule, cap, retry, timeout raise, timer, wait, cache or special case; a card of his that blocks it never stops it: build the recommended option meanwhile, unless his words on the order ask for options and no fix; from the merged fix to its build nothing stops, no dry run, no cover or cause read, no comparison of open builds, no question; the job that merges the fix lifts the service's freeze, named here by its id, with tools/ci release unfreeze <id>; start the build at the fix commit with the cut reference/incidents.md names and report its link; the approve gate is the only check and reads run while it builds; return the inputs to a write-up, not the write-up>
Scratch: <the job's scratch folder>
Transcripts: <the transcript path of every earlier job on this order, so it reads instead of being told>
Drive: <name>, order <o>, job <j>
```

Ask only for what the builder's definition and the drive's refusals allow, such as no picture of the
live drive in a public repository's PR body, which `drive pr add` refuses.

## What a job returns

A job's result goes to another agent, never to him, so it is the inputs to a write-up, never the
write-up; only the handler that writes to him writes prose.

- The job pipes its whole return on stdin into `drive job report <j> -`. That is its only report,
  even when a delegation hook appends an output contract naming a file, because Claude Code refuses
  a subagent's Write of a report file.
- It returns the first line alone, opening with a result word the state table lists. The
  `one_line_result` hook sends back a final message past one line of 500 characters.
- The rest of the kept report is one fact a line: links, numbers, paths, exact quotes, and what it
  could not prove.
- Whoever acts on it reads it with `drive job report <j>`, which prints a remote lane's Orca report
  the same way; an `ASK` keeps its block through `drive job ask` instead.

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

Answers resume; PR events start fresh. His answer to a job's `ASK` goes back to that job, by
`SendMessage` to its agent id here or `drive job resume <j> -` with the answer on stdin to a remote
lane. A PR event after a builder has returned gets a fresh builder, because a resumed builder
re-reads everything it ever did on every event. Run `drive job add builder` for the same order and
parts and `drive pr add <url> --job <new>` for each PR it takes over, write its brief with the
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
transcript. A brief for such a job says so.

One deliverable per job, and no job waits on another. When B needs A merged, B starts on A's `DONE`.

A sweep over many units is one job running a Workflow, and you start it: a builder has no Agent
tool, so the session that owns a job starts every agent its work needs.

A job's state is its transcript. Before you report anything about a running job, or send it a
`SendMessage` or `drive job resume`, read its transcript: `cc-transcript` on the file its agent id
names, or for a remote lane
`orca orchestration worker-read --dispatch <its dispatch> --limit 50 --full --json`. An absent
message is never evidence, and a message sent blind can repeat or contradict what the job did.

A close job closes only what he named: each session or terminal his words or tapped card name, by
its exact id after a fresh idle check and never a live drive lane, returning the closed count with
the ids. A close cannot be undone, so his naming is the whole authorization.

A stack is at most four PRs. His card shows each PR of a stack in its own section with its pictures,
so the card draw refuses a fifth. Split the change into stacks that each land on their own, or fold
PRs that carry one decision into one.
