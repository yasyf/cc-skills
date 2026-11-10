# Jobs

Every job is the coordinator's, and runs as a background agent on this Mac but one. His ruling,
Nov 9 7:11 AM: "No I'm saying that should be true when load is high, lanes will mostly start
remote". So while this Mac is over its load limit, a builder on Forge-AI/monorepo starts as a remote
lane on a Sprite, where its builds and checks are local to that machine; under the limit it runs
here. Before starting a monorepo builder, run `drive place`: it answers `remote` when this Mac's CPU
is at or over its load gate's limit, or when eight builders already run here, and `local`
otherwise. Readers, codex jobs, builders on any other repository, since a yasyf/* tool installs on
this Mac, and any job that touches this Mac's processes always run here.

**On this Mac.** Start the job with the Agent tool: `subagent_type` `long-running:builder`,
`long-running:reader` or `codex:codex-wrapper`, `run_in_background: true`, a short
`description`, and the brief as `prompt`; a design lane's call is in [Design first](design.md). **Never pass `name`.** Agent teams are on here, and a
named agent becomes a teammate that never wakes on events. Record the agent id the call
returns: `drive job add <kind> --order <o> --goal "<one line>" --agent <id> [--part "<part>"]...`.
The watch reads the agent's transcript, never the stored state: a running job whose agent ended
its turn, wrote nothing for 30 minutes, or belonged to an earlier session makes its
parts `ORPHAN`s.

**Remote.** `drive job add builder --order <o> --goal "<one line>" --remote`, then run
`drive job launch <j> - <<'BRIEF'`, the brief, and `BRIEF` from the monorepo checkout's root with
`run_in_background: true`. A brief, handoff or message for a lane always goes on stdin that way,
`drive job resume <j> -` included, never through a file the coordinator writes: Claude Code reads the
files a session wrote back into its context after every compaction. It starts the builder through the
`orca` skill's launcher as an Orca worker on a Sprite, on an Orca Run of its own that only this
drive's watch reads, so no other session takes its mail, and its notification says where it
started or why it could not. A lane that started no worker runs here instead, never
silently: start it with the Agent tool, record it with `drive job set <j> --agent <id>`, and
say so in the order's next report. A failure after worker-start ran may have left the lane
live, so a reader checks its attempt before anything starts it again. The lane reports through
Orca mail, and `drive watch` turns each report into a `JOB <j> <o> <line>` event that opens with
the `DONE`, `READY`, `WAITING`, `ASK` or `BLOCKED` a background agent returns; `drive status --json`
holds the whole latest report. `drive job replies <j>` prints every result and question the lane
sent, oldest first, each with its time, for a line that points at an earlier reply. A Sprite holds no Buildkite or AWS credentials, by his least-privilege
ruling, and no copy of the drive's cci or cc-notes records, so a lane reads through this Mac with
a blocking question: `LOG <url>` for a red Buildkite or GitHub Actions check, `READ <command>` for
a `cci` or `ccn` read the builder agent lists. The watch answers it itself, with no event: it runs
`tools/ci bk`, `ccx vcs job-log`, `cci` or `ccn` in the directory the coordinator started the
drive from and replies with the output, cut to 12,000 characters. A command off that list gets a
refusal naming the list. A lane whose wait ends waits again on the same question, so the watch
keeps each question queued on its job until the answer goes through, past a refused reply or a
watch restart. Its `WATCH the answer to <j>'s read failed` line needs nothing from you. The watch registers each PR a lane's `READY` or `PR:` line
links as that job's, wanting his tap, and reads the lane's Dispatch from Orca each minute: a
lane that finished or sat at its prompt for 30 minutes makes its parts `ORPHAN`s, and a lane whose
worker died, running or waiting, is one `LANE … died` line. `drive job resume` checks the worker
before it sends. A live one gets the message. Orca sends nothing to one that finished, so on a
Sprite still up resume starts the job's next worker in its terminal. A dead one is relaunched on a
fresh Sprite on the same Run. Either new worker gets a handoff carrying the job's brief,
its branch, its last line, its report, its PRs and the message, and the output says which ran.
Work the dead lane had not pushed is not carried over. When Orca cannot reach the lane's Sprite,
or refuses the send or the start, nothing is delivered and it exits 2 with Orca's refusal; resume
it again once the watch reads the lane. Run it in the background, since a relaunch takes minutes. An `EXEMPT:` line still gets
`drive pr set <url> --exempt <class>`, and a `SERVES: port of #N` line the `READY` row's check
against #N.

The brief is the job's whole context, the same for both:

```
Order (his words): <verbatim>
Serves: <"port of #N", or "his order" when the Order line asks for this build; a job for anything else is never started>
Goal: <the deliverable, one sentence>
Done when: <the check that proves it, e.g. installed and the installed version contains the merge>
Where: <repo, base branch, existing branch or PR to adopt>
Handoff: <a PR event only: the PR URLs, branch and head, the event line verbatim, and the INSTALL: and PROVE: lines the first builder returned>
Decisions that bind: <every ruling of his that touches this, his words verbatim with their time and what they imply here, from answer_search; never an id alone>
Plan: <the plan section this implements, quoted>
Incident: <an incident job only: from the merged fix to its build nothing stops, no dry run, no cover or cause read, no comparison of open builds, no question; start the build at the fix commit with the cut reference/incidents.md names and report its link; the approve gate is the only check and reads run while it builds; return the inputs to a write-up, not the write-up>
Scratch: <the job's scratch folder>
Transcripts: <the transcript path of every earlier job on this order, so it reads instead of being told>
Drive: <name>, order <o>, job <j>
```

A job's result goes to another agent, never to him, so it is never a write-up: its first line is
`DONE`, `READY`, `WAITING`, `ASK` or `BLOCKED`, or a design lane's `DESIGN`, and the rest is the inputs to the message he would
get, one fact a line: links, numbers, paths, exact quotes, and what it could not prove. Only the
handler that writes to him writes prose. His words, Nov 9 4:14 PM: "agents that are responding to
agents shoudnt do full himan-quality write ups they just need to dump their context back … I mean
the inputs to their would-be wriute ups".

- **His words, never a stand-in.** A brief's `Order` and `Decisions that bind` carry what he wrote,
  verbatim, and so does every cc-notes answer: never a paraphrase, a bot's summary, a card
  option's label, or an answer id standing in for words he wrote. A tap's label is his answer only
  to that card's question. A ruling travels by its words and what they imply through every
  compaction, restart, handoff and `SendMessage`. An id carried alone loses it, since an answer's
  title is the question and his words are in its body.
- **His approval of a release approves it, right then.** His order or card pick to ship, release or
  approve a release, or the coordinator's record of either, approves that release and its approve
  gate at once, migrations, deletes and replaces included: the job passes `--order <his answer id>`
  to `tools/ci release start`, or runs `tools/ci release approve <id> --via <agent>` the moment it
  holds at approve, with no second card, check or question in between, and nobody asks him again. A
  resume or re-plan of a release he ordered keeps his order. His words, Nov 9 5:25 PM: "what? no we want he oposite if I apprtove
  a release dont fuckl around kjust appriove it". No agent approves a release or its gate without an
  approval of his for that release, and a release he started himself is his: never cancel, retry,
  reorder or approve it. An incident's order approves its own cut and rollback, by
  [reference/incidents.md](incidents.md).
- **Answers resume; PR events start fresh.** His answer to a job's `ASK` goes back to that job:
  `SendMessage` to its agent id here, `drive job resume <j> -` with the answer on stdin to a remote lane. A PR
  event after a builder has returned gets a fresh builder instead: `drive job add builder` for the
  same order and parts, `drive pr add <url> --job <new>` for each PR it takes over, then the brief
  with its `Handoff:` line and every earlier job's transcript under `Transcripts:`, and
  `drive job set <old> --state stopped --said "handed to <new>"`. An incident's job is the one
  exception: it is resumed for every event, since minutes to users matter more there than tokens,
  by [reference/incidents.md](incidents.md). A resumed builder re-reads everything it
  ever did on every event; on Nov 8 and 9, 64% of builder spend came after the PR opened, at 288k
  tokens of context against 178k while writing the code. Each builder compacts at 300,000 tokens,
  set in its own definition, and remote lanes get the same window at launch; neither session's
  own window changes. His words, Nov 9: "it should be passing that as CLI arg to lanes its
  spawning for implmenetaiton (nt comunicator or coordinatior)".
- **A reproduction leaves one file.** A job that reproduces a bug, of any kind, saves one
  pick-up-and-run file before it returns: the steps in order, the exact prompt and code, the build
  and versions, and the numbers measured. It records it with `drive job set <j> --repro <file>`,
  which keeps a copy in the drive, and `drive status` lists it, so a share reads that file in
  seconds instead of rebuilding the steps from a transcript. A brief for such a job says so.
- **One deliverable per job, and no job waits on another.** When B needs A merged, B starts on
  A's `DONE`.
- **A sweep over many units is one job running a Workflow**, started by you, since a
  background agent cannot start agents.
- **A job's state is its transcript.** Before reporting anything about a running job, read
  it: `cc-transcript` on the file its agent id names, or for a remote lane
  `orca orchestration worker-read --dispatch <its dispatch> --limit 50 --full --json`. An
  absent message is never evidence.
- At most eight builders run on this Mac at once, and remote lanes do not count. Past eight, a
  monorepo builder starts remote, by his Nov 2 rule "Workers that are refused and enqueues
  should instead spill to being started remotely automatically"; any other builder waits as
  `open`, oldest due first.
- **A stack is at most four PRs.** His card for a stack lists every PR with its own lines and picture,
  so a fifth makes it too long to read on his phone. A change that needs more is split into separate
  stacks that each land on their own, or folded into fewer PRs.
