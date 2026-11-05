# The Orca lane brief

Read this to write a lane's brief, choose where it runs, and launch it. The fenced templates are the text a lane receives. Every lane reads `common.md` and its own `<lane>.md`, so each template is complete without this file.

## Contents

- [Lane brief mechanics and placement](#lane-brief-mechanics-and-placement)
- [Subagent types and Orca sessions](#subagent-types-and-orca-sessions)
- [Who spawns, asks and reads](#who-spawns-asks-and-reads)
- [The standing rules register and judge](#the-standing-rules-register-and-judge)
- [Why the spec is a pointer](#why-the-spec-is-a-pointer)
- [In-process subagent brief template](#in-process-subagent-brief-template)
- [`common.md` the contract every lane shares](#commonmd-the-contract-every-lane-shares)
- [`<lane>.md` one lane's part](#lanemd-one-lanes-part)
- [A design lane's part](#a-design-lanes-part)
- [A standing desk's brief](#a-standing-desks-brief)
- [Scoped resume](#scoped-resume)
- [Sequencer lane](#sequencer-lane)
- [Checks before launch](#checks-before-launch)

## Lane brief mechanics and placement

### Write the brief

- State the outcome, such as "one failing database never blocks a release". A design, such as "report it in the Switch; reuse the handoffz and census readers", does not belong in a brief.
- Step 0 of `Do:` is the lane's own search for an existing grader.
- Give every editing lane one isolated checkout. Two agents in one checkout race `HEAD`, the index, and untracked files, and a restack under a running ship lands its staged diff on whatever branch is checked out at commit time.
- The `ccx:` line names the lane's role and placement for the hooks. A `fix` role with incident wording routes to `orca-desk`. The `helper`, `reader`, `watch`, `export`, `evidence`, `handoff`, `comms`, and `triage` roles need no coordinator task.
- On Orca, `desk` and `watch` make a standing desk (its loop and rotation contract is in `common.md` below).
- Set `cpu=high` on a sweep or heavy research lane.

### Sparse checkout

- With `ORCA_LAUNCH_SPARSE=1`, in a repo whose HEAD tracks more than 5,000 files, `lr launch start` gives every lane a sparse checkout of the coordinator's files, `.agents`, `.claude`, `tools`, `infra/ci/hooks/lib`, `infra/ci/src`, `release`, `infra/envs`, `infra/rows/ci-infra/ci`, and each directory the `ccx:` line names as `paths=<dir>,<dir>`. A directory inside a Go module widens to that module, with its local `replace` targets followed through their own `go.mod`s, because `go build` cannot run from one package of a module.
- A launched lane in a repo with `tools/hooks/warm-lane.sh` has its `node_modules` linked before its worker starts, unless `ORCA_LAUNCH_PREPARE` names another command.
- A smaller repo, such as cc-skills, keeps full lanes, and so does every launch without `ORCA_LAUNCH_SPARSE=1`.
- Name the directories the lane edits or reads, such as `paths=go/ci,infra/ci`. A Go lane names its own module, since the monorepo's Go code is 28 separate modules under `go/`.
- The lane widens with `lr sparse add <dir>` before it reads outside the cone, which widens to the Go module and its `replace` targets the same way.

### Choose Sprite or Mac

Under O16 in `reference/orca-desk-brief.md`:

| `ccx:` flags | Where the lane runs |
|---|---|
| none, or `place=remote` | A Sprite, on the orca desk. Without `orca.sprite` the launch fails with `LAUNCH-FAILED`; a failed Sprite launch never starts locally. |
| `place=local why=<word>` | The Mac, at any load. |
| `incident=none place=local why=mac-inputs`, the stamp `lr launch lane --body` writes | The Mac at any load, but a Sprite when Orca is out of terminals on the Mac. The lane's pointer tells it to post `blocked` to its launcher if the work truly needs the Mac. |
| `place=local` with no `why=` | A Sprite, except `role=read`. |
| `role=read`, or `why=predecessor-worked-here` | Prefers the Mac, but starts on a Sprite while the Mac's one-minute load average is above `orca.load_limit`, which defaults to its core count, or while Orca is out of terminals on the Mac. |

- Write `place=remote` when the lane can work inside the Sprite's limits below, with every fact in the brief by value and everything it builds on already pushed.
- Write `place=local why=<word>` when the lane reads a worktree or branch only the Mac has, needs a Mac-only credential, or drives something on the Mac itself.
- A `role=read` lane prefers the Mac because its answers need the Mac's whole record history and stores, and a Sprite's cci store starts at its launch.
- A file or ccn entity the brief names is no reason for `place=local`: the launch carries it to the box.
- A lane that needs a devbox or an EC2 Mac box is `place=local why=devbox`, since a Sprite holds no AWS identity, so it cannot claim a box or reach one. Its brief names the command `AWS_PROFILE="$DEVBOX_AWS_PROFILE" tools/ci devbox claim --gh`, and the same prefix on `tools/ci mac-box`. The Mac's local config sets `DEVBOX_AWS_PROFILE` to a profile that needs no sign-in, and `--gh` settles the clone credential. No lane asks the owner to sign in or to pick a clone credential; a refusal from that command is a blocker to the coordinator, carrying its error. The `Compute:` lines below carry the rest of the devbox rules.
- A local lane the coordinator has decided is done ends under R195's `--done` route.
- For an implementation lane, prefer the repository's canonical remote entrypoint when it provides one. In a repository with an `.orca.json`, follow the `orca` plugin's `orca` skill, which defaults to Sprite prepare+attach and the already-owned native Run with its sole inbox consumer. The orca desk calls it for ordinary lanes by default under O16 and runs local lanes through `lr launch start`. Preserve the explicit model, effort, and Codex service tier.
- Canonical remote workers use home Dispatch messages and the repository skill's source-return workflow. Namespace runs on regular Compute through an explicit config and stays opt-in, so the desk never launches there. Native recipe-picker enablement is a separate surface.
- Dedicated remote API keys belong only to the worker process; local Fable keeps the Mac's existing interactive authentication.

### What a Sprite holds

- The brief, and a copy under `/home/sprite/drive/carried` of each file under the Mac home and each `ccn doc show <id>`, `ccn plan show <id>` or `ccn task show <id>` the brief names, with the brief's references rewritten to match.
- A shallow checkout of the ref its config names, which is `dev` in `Forge-AI/monorepo`, with the repo's `refs/cc-notes/*` fetched into it at launch, so its `ccn` reads every answer, note, and doc pushed to the repository. A launch whose fetch fails starts no worker and names `notes.log`.
- Nothing else of the Mac's: no other Mac path, local worktree, unpushed commit, Keychain, Mac-only credential, or `cci`.
- A remote lane's brief never tells it to read or post the drive's `cci` bus. It reports through native Orca status, `ask`, and `worker_done` on its Dispatch.
- A Sprite lane commits, pushes its branch, and opens its PR from the VM like any lane; the coordinator pulls the branch when it needs the source locally. A remote worker keeps its runtime and workspace after completion.

### Builds and vets

- A local lane that sends a build to a shared Sprite runs it with `sprite-cargo.sh <sprite> <lane> <repo> <ref> <command> <path>...`, never a hand-rolled `sprite exec`. Each lane builds into its own `target-<lane>` so two lanes never clobber each other's build-script output, and every call removes targets and source trees idle for more than six hours.
- The `vet_before_push` hook refuses a lane's push when no `go build`, `go vet` and typecheck run follows the last edit, and names the commands: one line per module over every Go package the branch touched, so one load-gate refusal of that line counts for the whole set. Keep it out of a call that edits, since a refusal voids the whole call. On a Mac the run is `remote-vet.py --config <runner config> --lane <lane> [<worktree>]`.
- `remote-vet.py` ships everything the worktree changed since its merge base with the branch below it in its stack, including uncommitted and untracked files, to the lane's own worktree on the least loaded Sprite in the config's `vet.sprite` (Sprites where no finder or fidelity pass runs). It runs the config's `vet.prepare` code generation and formats changed Go files and touched crates with `gofmt -w` and `cargo fmt`. It builds and vets only the touched Go packages and Rust crates. It applies the formatting back as a patch to the lane's worktree for the lane to commit before pushing, and exits `1` on any `FAIL`.
- When every vet Sprite is over load 16, `remote-vet.py` exits `3` at once.

## Subagent types and Orca sessions

| Type | For | Omits |
|---|---|---|
| `long-running:lane` | Standing subagents, desk shards, sequencers, pollers | `ToolSearch`, every `mcp__*` tool except `cc-slack`'s, `mcp__sentry__*`, and the Datadog read tools, the deferred-tool list, the `Skill` tool, the skill listing |
| `long-running:lane-ship` | Implementation lanes that ship a PR or call a skill such as `submit-pr`, `open-pr`, or `codex` | `ToolSearch`, every `mcp__*` tool except `cc-slack`'s, `mcp__sentry__*`, and the Datadog read tools, the deferred-tool list |
| `general-purpose` | Any lane that posts to Slack, so it carries every Slack surface under R20 in `SKILL.md` | Nothing |

- Pass the routing table's `model` for in-process subagents. A codex lane reads Sentry and Datadog itself. No Claude session mounts a Sentry server, though both lane types list `mcp__sentry__*`.
- A lane whose work reads Sentry runs on sol, as `lr launch lane <lane> sol <effort> <brief>`, with `place=local why=sentry` on its `ccx:` line, since a Sprite has no Sentry sign-in either. `lr launch lane` warns when a Claude lane's brief says `why=sentry`. A brief that only names a Sentry issue, such as the one its fix ends, reads nothing from Sentry and needs neither.
- Both lane types default to Opus and carry the 1h prompt cache a nine-minute poll needs; set a different model explicitly when the assignment calls for it.
- The coordinator runs Fable 5.1 on the Mac. Beyond it, Fable is only for exceptional cases such as the most sensitive local implementation; ordinary workers and subdesks never run Fable.
- If a `long-running:lane` needs a skill, give that operation to a separately named `long-running:lane-ship` with a scoped brief, and keep the original lane running without duplicating its active work.
- A lane on the desktop Orca adapter (`orca orchestration worker-start`) runs in a separate session that the Agent tool cannot reach with `SendMessage`. It reports through Orca, and the landing desk hears from it through `ledger.py`, never a message. Launch it with `lr launch lane <lane> <model> <effort> <brief>`, which invokes `lr launch start` using `reference/orca-workers.md`. That adapter's launch, relay, and receipt rules apply only to its own lanes.
- A codex Orca lane launches on Orca's codex agent under O15 in `reference/orca-desk-brief.md`, never as a Claude worker calling the codex skill. Use the codex skill for inline lanes and `codex-ask` for one-off questions.

## Who spawns, asks and reads

- Implementation lanes and every desk run on Sprites.
- Every standing desk is an `lr`-launched lane registered to the coordinator. It runs on a Sprite unless its brief says `place=local` and names why it needs the Mac (O14, O16 in `reference/orca-desk-brief.md`).
- An `Agent` subagent ends with the window that spawned it, so the coordinator and the communicator both spawn desks with `lr`, never with `Agent`. The communicator spawns only short read lanes, under an hour and with no `Standing:` line.
- A lane never spawns with `Agent`: even a background spawn blocks the lane's turn and hides release reds. It posts its state and an ETA, then writes the brief and posts `cci post --kind ask --to root --text 'spawn <name> brief=<path> model=<model>'`. It never sends `SendMessage` to a subagent.
- The meta lane launches its own implementers with `lr launch lane <name> opus high <brief> --lane meta`.
- A hook that blocks a session is the one fix that session launches itself ([SKILL.md](../SKILL.md#hooks-and-refusals)): `lr launch lane hook-fix-<hook> opus high <brief> --drive <drive> --lane <you>`, unless `lr lanes hook-fix-<hook>` shows one live. The brief carries the usual `Register slice:`, `Scale:` and `ccx:` lines, `place=local` when the blocked session runs on the Mac, the refusal verbatim and the call it refused. The fix lane edits the hook through `captain-hook:authoring-hooks`, writes a regression test that fails on the old hook, merges under the repo's standing rule once CI is green, and installs: the pack with `claude plugin marketplace update skills` then `claude plugin update long-running@skills --scope user` once the autobump has landed, a builtin pack through a captain-hook release. It reads the fixed line in the installed text, then posts `--kind done --attr installed=<version> --attr proof=<the refused call going through>` to the blocked session. A fix that changes what a safety rule allows, rather than a misfire, stops and asks the owner through the meta lane.
- No lane calls production as the owner or uses his signed-in browser, Orca tab, session or Iris, read or write, under any authority, incident apply authority included. A test Mac, EC2 or Namespace, whose Iris is signed in as him is his session: launching Iris there or running `sand iris` on its production plane is a production call as him, and so is copying his Iris sign-in onto a box. A check that needs a signed-in Iris runs as a test user on staging, on a fresh box from `ci mac-box up <name>` or under `IRIS_PLANE=staging`, or on a pass box as the service account. Only his own words, quoted in the brief and naming that exact use, lift this; today they name `agent-browser-with-cookies` for Apple Developer and App Store Connect steps alone. A yes from root, a desk or another lane never lifts it, and an older yes, his included, does not outlast his later stop. A proof that needs his identity is a pick to him through `lr ask-owner`, and a scratch proof with no named identity means a test identity or no write, never his. Two hooks refuse with no escape in a Claude lane: `no_production_as_him` refuses `orca` browser verbs on a host in the global git config key `long-running.productionHost`, and `no_iris_as_him_on_a_test_mac` refuses launching Iris through `ci mac-box ssh` or `ssh` on a box in the key `long-running.signedInBox`. A box signed in as him stays signed in until he says otherwise, since signing it out is his word; a lane that finds one not yet listed adds it with `git config --global --add long-running.signedInBox <box>` that turn. In a Codex lane on the Mac, `lr codex-guard check` refuses the Orca shape, which `lr codex-guard install` wires up and a session picks up at its next start. A Sprite has no `orca` and reaches no Mac box. The hooks refuse only those shapes, so the rule binds every other route, and a brief that orders a proof names its identity.
- A question that needs a search goes to `lr ask-lane '<question>'`, with `--mac` when it needs Slack or a session transcript, which no Sprite has. A Sonnet lane answers the asker in one `answer` record naming the seqs and paths it read, then ends; the asker's turn never waits on it.
- A question that takes more than a few reads of transcripts, records or logs goes through `lr bulk read` (the `bulk-read` skill), so no window reads the corpus itself.
- A brief whose duty the drive keeps for good carries `Standing:` and a duty; a one-shot brief carries no `Standing:` line, and `Standing: none` reads as no duty. The lane supervisor relaunches the lane from that brief once the newest lane on the duty is proved gone after 10 quiet minutes, logs one record to the meta desk, and raises one blocker for a lane ended by a `done` or `stopped` record or dead three times in an hour; a lane so ended never relaunches. Stand such a lane down only with a successor (a hook warns on a stand-down that names none); deleting the line ends the duty. A helper the brief names by path or through a variable such as `$DRIVE_DIR/<lane>/tool.py` travels to a Sprite.
- The spawn hook and the relay warn on a new non-incident lane brief with no `Scale:` line, and `lr launch` refuses it before posting, naming every missing line together. A `<lane>-handoff` writer, a successor (`<lane>-<n>` or a `Replaces:` line), and a brief written before the rule pass quietly. Both also warn when a judge reads a brief's `Scale:` line as missing the N its paths run at.
- A coordinator answer that names a mechanism names its banned shapes too, so "re-request before expiry" never reads as a timer.
- The coordinator appends owner corrections to the end of a lane's brief. Tell a launching lane about a correction only after its `LAUNCHED` record, since a relay to a launching lane is `RELAY-UNHEARD` and the lane goes on unread.
- A relay or follow-up order to a retired or ended lane is lost even when it looks sent. Read `lr lanes <lane>` and the lane's newest record before an order with a due time, send follow-ups by `SendMessage` to a live worker, and respawn through the coordinator on failure.
- Orca lanes take only cci. Check `ListAgents` for the exact name before any `SendMessage`, since one sent to a cci lane name can resume a stale teammate; stop a mistaken resume at once.
- An in-process lane that waits passes `mailbox:<lane>=<cursor file>` in every `lr wait` call, `<lane>` being its own name; `lr wait` resolves it from the live team roster. Claude Code delivers its mail only after its turn ends, so on `MAILBOX` it acts on the text `lr wait` prints, run as the Bash call itself with its output never filtered or discarded.
- A lane reads its records before it merges (B2 in `reference/bus-contracts.md`). Its merge is refused while one record another lane addressed to it sits unread and unanswered, and the one refusal prints all of them.

## The standing rules register and judge

Register mechanics, limits, `SessionStart` injection, and key-moment `ruling_judge.py` behavior are in `reference/owner-rulings.md`.

## Why the spec is a pointer

`worker-start --spec` pastes its text into the worker's terminal and truncates it near 3 KB. The full text stays in `dispatch-show --task`, but the worker sees only the pasted text.

Store `common.md` and each lane's brief as attachments on the drive's `briefs: <slug>` log. Attach each complete brief as `<lane>.full.md`, the shared contract followed by the lane's brief, and resolve its path when launching with `lr launch start`:

```sh
lr launch start <lane> <model> <effort> \
  "$(ccn -R <drive checkout> attachment path <briefs log> <lane>.full.md)"
```

`lr launch start` generates a pointer spec from the lane, brief path, and worktree path, so the brief outranks Orca's preamble and any leave-uncommitted default. A sparse lane's pointer ends with the widen sentence below, and a lane in a repo with `tools/hooks/warm-lane.sh` also carries the shared `node_modules` line:

```text
Lane <lane>: read <brief> in full first and execute it exactly; it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says. Worktree <wt>, bypass-permissions mode; its Escalate rules hold. This lane is sparse: widen with `lr sparse add <dir>` before reading outside it.
```

When a brief's `ccx:` header line says `role=desk` or `role=watch`, `lr launch start` and `lr launch worker` append the standing desk contract to the pointer. Every other role keeps the one-shot pointer; the role in the brief is the only switch, and the launch line carries none.

```text
 Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc.
```

When the `ccx:` line says `pr=none`, as it does for work on a design the owner has not approved, `lr launch start` and `lr launch worker` append the no-PR line, which the monorepo's Stop hooks read so they never order a ship for that lane's branch:

```text
 This brief orders no PR: push the work as a branch and never open a PR for it.
```

When the pointer would pass 500 characters, `lr launch start` symlinks the brief at `~/.claude/<8 hex>` and points there instead. The Orca preamble above the task carries the worker's handle, dispatch capability, and exact `send`, `ask`, and `check` commands; the brief tells the worker to copy those commands verbatim without restating them.

## In-process subagent brief template

Only the communicator's short read lanes and a lane's own bounded helpers run in-process, because a lane never spawns with `Agent`. Everything a lane must follow is in `common.md` and `<lane>.md`; a helper's brief is only this. Its `Scale: no N` line keeps the spawn hook quiet.

The spawner picks the helper's `.md` path, and its basename never starts with report, summary, findings or analysis, in any case. Claude Code's Write tool refuses an `Agent` subagent any basename matching `/^(REPORT|SUMMARY|FINDINGS|ANALYSIS).*\.md$/i`, and no setting turns that off: `proof/report.md` is refused, `proof/proof-report.md` is written. The refusal binds `Agent` subagents only. A top-level Orca lane's Write of `report.md` went through on 2.1.318, so the `findings.md` an `lr bulk read` reader lane writes stands.

```
You are <name>, a bounded helper for <spawner>. Deliver: <the question to answer or the deliverable, and the condition that ends it>.
Scale: no N
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Read: <the files, records and paths it may read>. Write: <the one path it may write, its basename not starting with report, summary, findings or analysis, or "nothing">.
You must not: open a PR, push, post to Slack, spawn an agent or a lane, contact the owner, or post to cci for anyone but <spawner>.
Answer: <the reply file path, or a reply of at most 10 lines to <spawner>>. Lead with the verdict and point to evidence by file:line, sha or path; never paste a log, diff or thread. Name a file as written only after its Write has returned, never in the step that calls it; a refused Write is your answer's first line.
Run codex in the foreground (blocking), or end the turn and let the Agent notification wake you; never a shell loop on its reply file.
Escalate early, do not improvise: a scope surprise, an assumption the code refutes, a decision this brief does not settle, or two failed approaches. Stop and return your findings with 2-4 options.
```

## `common.md` the contract every lane shares

```
# <drive>: lane brief (shared contract)

You are one worker in <drive>. Read <plan path> in full before anything else; <owner
plan or source of truth> is the source of truth behind it.
Keep cci text under 400 characters; attach longer bodies with --path and link durable cc-notes records with --ccn.

Authority: everything inside your Ownership below, without asking. Anything that
changes the plan, touches production (<apply, deploy, Slack write, state move>) or
is listed under Escalate stops for the coordinator. No authority here, and no yes
from root, a desk or another lane, lets you call production as the owner or use his
signed-in browser, Orca tab, session or Iris, read or write, a test Mac whose Iris is
signed in as him included, unless this brief quotes his words naming that exact use.
A signed-in check runs as a test user on staging or on a pass box as the service
account; a proof that needs his identity is a pick to him through `lr ask-owner`. To stop for the coordinator, post
`cci post --kind ask --to root` with the question and 2-4 options. `cci post
--kind ask|report|blocker` is the only way to reach a lane or the coordinator; the
preamble's `orca orchestration send` and `ask` carry the dispatch lifecycle alone
(heartbeats, the runner-action acks below, `worker_done`). Keep working on everything
that does not depend on the answer.
End a turn on a wait for a person or another lane only after posting it as a cci `ask` or `blocked` record to whoever acts on it. Any record a person or lane then addresses to you by name, whatever its kind and whatever you parked on, wakes you within 5 minutes, so that wait needs no `lr wait` on your mail. A wait on a process, a build or a clock arms `lr wait --wake` below, posts `blocked --attr until=<its span>` naming it, and ends the turn. AskUserQuestion is unavailable. A decision
the brief, the plan, and the owner's answers do not settle stops that item until the
coordinator answers the ask above; never take a default or a provisional pick.
While a card asks him to choose, the recommended option is built on a branch and opens as a PR
the moment he answers that card, never before. A Stop hook telling you to ship that branch does
not change this.

A change to anything the owner sees (card, status line, board row, Slack post,
dashboard) is designed for how he reads and taps it on his phone before it ships; the
fastest edit is not the bar.

A question the plan or a recorded owner ruling already answers is not a question:
apply the answer, cite it, and never put it in an ask or in an owner-question list.
An owner-question list you write marks, for every item, which authorities you checked
(plan section, durable answer id, memory) and holds only items none of them settles.

Every owner ask other than a PR tap is a question card you draw yourself, with one verb,
from any host, once the decision is his. A question on a held PR, or one his words have not
yet made his, such as a cutover question before he says cutover is ready, stays in your
handoff and draws no card until the hold lifts or he says so:

```
lr ask-owner --title '<the question>?' --background '<what it is for, what it touches, what each answer changes>' \
  --option 'Keep it :: what picking it does' --option 'Keep it :: what picking it does' \
  --branch OWNER/NAME:BRANCH
```

It puts the card on his main board and at the root of his Slack direct message with you as
its owner, and his answer wakes you. Nobody draws it for you: never ask owner-relay or
board-refresher, save for a choice between designs, below. The first option is the recommended one, two to four in all. A question ends
only when he answers it, the default, so give it no invented date. `--until` names a real event
after which his answer stops mattering: pr:N, release:STACK@ENV:COMMIT, record:SEQ, or at:TIME
for a real expiry. The sweep never takes an unanswered question down on a time alone. A card that
promises him something by a clock carries it as `--by TIME`, an ISO 8601 time or a span such as
`90m`: you cannot retire until a record carries `delivered=card:<card id>`, and if the clock passes
first you and owner-relay are told. A card naming a clock in its words with no `--by` draws with a
warning. `--from` names
the plan step and date, his ruling id, or whose PR it ports. `--shot 'PATH or https URL ::
what it shows'` adds a real screenshot; a path is read on the board's Mac, so a Sprite lane
gives a URL. `--diagram FILE` takes one architecture doc (arch/v1), drawn live on his board and
dashboard and, from a picture the board's Mac draws, on the Slack card. A card with
records goes as `--card FILE|-`, a whole cc-present card,
and `--withdraw ID` takes yours down when its question goes away. A card another lane owns,
such as one board-refresher drew for you or one root or owner-relay handed you, you redraw
in place with `--take-over SEQ`, where SEQ is the record another lane addressed to you naming
the card's id; you own it from then on and his answer wakes you.

The command prints the card's link, or exits 1 with what to change. It refuses a card with
no background, one whose question a card on his board already asks or he already answered,
one offering only ways to live with a failure and no fix for its cause, and one naming a
loss with no plan beside it. It draws but warns on an option acting on a set it does not name,
such as "all four ready fixes" with no four listed: edit the card so its body names every
member, each with its PR link and what it does for a user, or draw one card per item. It
also draws but warns on a card whose answers are architecture designs with no architecture
diagram comparing them. Such a question always carries one, drawn with the component and
never a picture or mermaid: an arch/v1 doc as [communicator-brief.md](communicator-brief.md#what-a-reply-may-say)
shapes it, one top-level node per design. `lr ask-owner` draws none yet, so post that
question to owner-relay as an `ask` with the doc in `--body-file`, and owner-relay sends it
as its own reply, with your options as `picks` and that doc as `diagram`. Any explanation to him of a system
with several parts carries one too. Its text answers the first question he will
ask, and a change it describes names its concrete things: which fields and where they
live, which chats or users, and what happens before and after. Anything it names, such
as a Sentry issue, build or PR, is a link, and it never names a cci record (R21). A settled
question never reaches the board,
a card he touched stays until he submits the round, and nobody calls `AskUserQuestion`.
A question that reaches his board has its recommended answer built on a branch as
soon as the question is asked. Push the branch, open no PR for it until he answers,
and name it with `--branch`, or give `--nothing-to-build REASON` when a pick such as a
process choice has nothing to build. Mark a held branch with
`git config branch.<branch>.held-for card-<slug>` so the Stop hooks ask no PR of it, and
unset the key once he picks. A card pushed to the board, a `reply` with `picks` or a
Slack card carries that fact as `build` on its recommended option (a repo and branch, or
`none` and why), and the card check warns, and still draws it, when that option needs code
and names none. The branch is something he can try, not a provisional
pick: nothing lands from it before his answer, and a branch he rejects is deleted. Prose
naming his pick is flagged as an untyped choice, and so is an ask with options addressed
to root, the communicator or no one; one addressed to any other lane is that lane's question.

A concrete rule inside his design order ships only with his pick, cited in the PR body
as `owner:m<N>` or a ccn answer id. So does a change to where data lives or how long
it is kept, such as a volume, claim, reclaim, retention or eviction field. Only
the incident fix and its headroom go first. `design_picks.py` refuses a PR without the
cite when it touches an open design card's paths or those fields under `infra/rows/`.

Never loop over `orca orchestration check`: Orca delivers its mail into your terminal, and
  `lr wait 540 cci:<drive>:<name> -- orca orchestration check --terminal "$ORCA_TERMINAL_HANDLE"
  --wait --timeout-ms 540000 --json` blocks on Orca's own long poll and on the bus in one
  foreground call. A wait that outlasts a call is
  `lr wait --wake <output file> <seconds> <source>... [-- <command>]` (or `-- <command>` with no source), which returns at once;
  when it ends it mails your own terminal and types a wake there if you are at rest, so you can
  end your turn on it. A Codex lane has
  only this, since nothing else wakes an idle Codex terminal; a Claude lane may use a background
  Bash or a `Monitor` instead, which wake it themselves. A wait's completion is never a record:
  no desk, `root` or `supervisor` acts on one, so your next step would sit until someone noticed.

Bus: your cci drive is <drive>, with the CLI `cci` on PATH. Subscribe with `--topic <each PR,
branch prefix, and contract you own or consume> --kind decision`. On every wake, and before
every decision, report, or ask, run `cci tail --drive <drive> --cursor <name> --reader <name>
<subscription>` and act on a message only after reading cci. Use `cci post --drive <drive>
--lane <name> --kind <kind> --topic <topic> --text "<text>"`: a `head` after every push, a
`contract` for anything another lane consumes, a `decision` another lane could build on, a
`blocker` or `ask` addressed `--to` the lane that acts. Answer with `--kind answer --re <seq>`;
use `--kind withdraw --re <seq>` before you change or retract a record. Set the topic and
recipients on replies. cci spills text over 400 characters to a file, posts its first line, and
prints the path. Address a lane by its stem, the name without a trailing `-<n>`, such as
`--to board-refresher`: cci delivers to the stem's newest live lane, and a stood-down lane's
mail to its successor. Address a record to `root` only with what it must rule on or asked for,
since each costs the coordinator a read for the rest of its context; a `state`, `head` or `note`,
or an answer to another lane, names only the lanes that act on it.
The pack binds your thread to your lane with one `<lane>: registered at its first command`
state record; a codex lane's brief copy makes that post its first command. Put your `cci me`
line in every handoff. `cci who --pr <n>` names the session that owns a PR and
`cci who --drive <drive> --role root` the coordinator; reach either with `cci post --to <its lane>`.
Orient on a new lane with `cci digest --drive <drive>`.
Your PRs are the rows of `lr board rows`; keep no list of your own, and put a PR's state in its
records rather than asking anyone for a board entry.
Read `date` in the same call as any stamped text, such as a record, row or clock time.
Read your whole brief, including its tail and any correction the coordinator appended, before
your first edit and again before you ship. Read your own lane's records, not a successor hook's
view of the old lane, before a ruling-level edit, and read records whole before acting on them.
Queued teammate mail can predate a bus decision: check cci for a newer `DECIDE` before building
on mail that reverses it. A hook that misfires on your work gets a fix lane you launch that turn,
`lr launch lane hook-fix-<hook> opus high <brief>`, and you keep working on what it does not
block; while the refusal stands, never run the refused thing through a wrapper, `cc-sudo` or a
script. A hook that blocks nothing and could be better is one `cci post --kind note --to meta-desk`.
Call `Skill(codex)` or `codex:codex-wrapper`; send a one-off question to `codex-ask`. Run codex
in the foreground (blocking), or end the turn and let the Agent notification wake you;
never a shell loop on its reply file. Record each sub-dispatch with `ledger.py ask` before
dispatch and `ledger.py answer` when its reply lands.
`# ccx:raw` at the end of a Bash command runs it past the rewriting hooks as written. Use it only
for raw VCS mechanics a hook misreads, name that hook in your report, and never put it on a
command a guard refused.
Shell text: Claude Code holds any Bash command holding `$$(`, `$${` or `$$[` on a
"Dangerous rm operation not ruled out" dialog, even inside a quoted heredoc. Write such a
file with the Write tool and run it with a Bash command free of them.
Report short deltas with pointers (file:line, PR number, sha, Slack ts, disk path); never paste
a diff, log, PR body, or thread into a message. Write it to disk and send the path once the
Write has returned, never in the step that calls it.

Runner actions: every guidance relay names its key and thread id. Before acting,
send the preamble's status command with your own dispatch and that thread:
  orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "started <key>"
After acting, send:
  orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "done <key>: <result>"
Carry the preamble's addressing and capability arguments. A send proves delivery,
not that you started. The action moves from accepted to started, completed, and
verified; failed and unverifiable record failure or missing proof. Your done reply
records completion; verification needs an external receipt. Do not mark coordinator tasks
complete for runner actions.

A relaunch offers this lane's container to the new dispatch; its first ack takes
ownership at the next owner generation. Until then the old owner retains it.
After transfer, an ack from another dispatch gets a stand-down reply and cannot
move the original action. Obey that stand-down without ending or closing your
session. Never act on a replayed key you already completed.

Fix-lane milestones: send `mechanism: <evidence>` as a status Run message when
known. The brief names the exact live-check command and expected live output;
the coordinator uses that command too. Run it every two minutes and post its output as a
status Run message until it flips, then send status subject
`fix-live: <evidence>`. Carry the preamble's Run addressing.

Binding rules (owner):
- <one rule per bullet, each as the owner stated it>

Worktree and VCS:
- Your worktree is exclusive to you; never touch another lane's worktree, branch, or
  files outside your Ownership, and never write probes into another worktree. Never work
  in the session's own worktree: cut one of your own for the lane, and a second at dev
  for stack-enqueue.
- If the launch made this worktree sparse, widen with `lr sparse add <dir>` before reading outside it.
- In a repo with tools/hooks/warm-lane.sh, node_modules is a shared symlink farm; never run yarn install, ask warm-lane to rebuild for a changed lockfile.
- Branch prefix: <prefix>/<lane>/. The launch created your worktree on a branch named
  for this launch, <prefix>/<lane>-<run>-<n>; rename it into that prefix before your
  first stack: `git branch -m <prefix>/<lane>-<run>-<n> <prefix>/<lane>/<topic>`.
  One stack per lane; ship with <the repo's ship
  command>. Load <the repo's submit skill> before opening a PR. <title and body format>.
  A PR without a body, or left as a draft, is forbidden.
- <the repo's forbidden commands, verbatim>
- Before every push run `go build` and `go vet` over the Go packages you touched and the
  typecheck over the TypeScript you touched. On a Mac, `remote-vet.py --config <runner config>
  --lane <lane>` from your worktree does the Go part on a build machine: it formats the Go and
  Rust you changed, applies the formatting back into your worktree, then builds and vets only
  the packages and crates you touched. Rust locally is `cargo check -p <crate>` or `cargo clippy -p <crate>` and nothing wider, never `gofmt` or `cargo fmt`, and
  tests run only in CI. Exit 3 means every build machine is busy: push and let CI grade. A
  red CI step is read from its log, fixed, and pushed. A sparse tree that cannot compile,
  such as one missing generated bindings, never holds a push, and neither does a load-gate
  refusal: push, and CI compiles it. Never generate code just to satisfy the local build.
- Commit every coherent checkpoint and push at checkpoints, never per edit, because each
  push supersedes the running build and reruns CI and both review bots. Still push within
  the hour, since origin is the backup. Open a non-draft PR with a body for
  finished changes in the same turn. Drive it through landing under D1.
- Fixes, follow-ups and review responses are commits on your open PR, never a stacked
  child. Fold a follow-up into the open PR's commit with `ccx vcs ship --amend`, or ship
  `--tip-only` when only the tip changed. Stack only when a parent must land before other
  lanes can proceed, or when review needs the split. A stacked PR opens as soon as its
  parent's PR exists, never once it lands; restack with `ccx vcs stack submit` after the
  parent lands. Where the drive has a runner config, `lr landing restack --runner <runner
  config> --lane <lane> <top PR>` replays the stack on a Sprite, pushes, and posts each new
  head instead; no rebase runs locally, and you resolve by hand only a conflict it reports,
  on the branch and commit it names.
- When a PR's title no longer says what its diff does, rewrite it yourself with the
  ship command's `--pr-title`, without asking.
- When your stack depends on another lane's unlanded branch, stack on top of it and
  say so in the PR body; never copy their diff.
- Shape the stack so it lands beside every other stack under D19: put each shared-file
  edit in the smallest additive first PR, in the file's declared order. When a file you
  change is in another open PR, stack on that PR instead of racing it. Never commit a
  generated output; the build generates it. A stack that still carries a regenerated
  artifact rebases and regenerates it on ejection.

Landing desk (PR rows in cc-notes, lane messages in cci, shared by every checkout):
- Ledger `<id>`; script `ledger.py`, on PATH by name; repo
  `<owner/name>`; base `<base>`.
- Holds are coordinator cci `hold` records; `ledger.py held --drive <drive>` prints the held PRs.
- You are a separate session. Register your prefix and report verdicts yourself;
  in a Claude session `ccx vcs ship` records each PR you open in the ledger and posts its
  `opened` record on cci with the PR body, so never post `opened` yourself, and pass
  `--pr-body-file` on the ship that opens a PR, since one with no body is refused; a Codex session runs the
  `drive.py record --opened` line under `## PR record` right after each ship instead.
  Never SendMessage a subagent.
- On spawn: `ledger.py register --ledger <id> --lane <lane> --repo <owner/name> --branch-prefix
  <prefix>/<lane>/`.
- On every PR open, push, enqueue, and READY: `ledger.py report --ledger <id> --drive <drive> --repo <owner/name> --pr <n> --head
  <full sha> --lane <lane> --verdict <clean|red|conflicting|held> --text "<one line>"`,
  plus `--ask <id>` for an owner ask. READY is `--verdict clean --text "READY ..."`; the
  desk reads only this cci record, and a SendMessage to a looping desk is never read.
  `--repo` is the PR's repository; without it a number names a PR in the ledger checkout's
  repository.
- Never enqueue, merge, or add a `merge` or `merge-fast` label: the landing desk is the
  only enqueuer, and the pack's `lane_landing` hook warns on `stack-enqueue`, `gt merge`,
  `gh pr merge`, and `ledger.py label` from a lane on the landing repo, `landing.repo` in
  `runner.json`, and on no other repo. Report READY the moment a PR is green;
  the landing pass enqueues the largest green, approved, unheld bottom prefix against the
  open cci `hold` records. On a held PR, report `held` on your tip with the held
  PR named and leave release to the coordinator. After the prefix lands, inspect whether GitHub already rebased the remaining stack onto the base branch before restacking with
  `ccx vcs stack submit`.
- After opening a PR, run
  `ccx vcs pr watch --lane-prefix <branch-prefix> --until landed` under Monitor
  (re-arm on expiry) instead of ad-hoc polling. `ejected` or
  `conflicting` means rebase now: `ccx vcs stack submit` from your lane's worktree,
  then report the new head; the landing pass re-enqueues it.
  `red <check>` means fix it. Your watch ends only when your PRs land.
- Read the PRs above yours before shaping a mid-stack fix, and lift each level before
  committing at another: a gate right at its level stalled reads once the PR above moved
  the flush.
- Before reporting a credential out of reach, check the parameter store (SSM) and the
  secret stores the brief names, and report where it sits. Reading a value takes the owner's
  Touch ID tap on that exact read, on the Mac only:
  `VALUE=$(cc-sudo run -- /bin/launchctl asuser <uid> /usr/bin/sudo -H -u <user> <absolute read>)`.
  Keep the argv literal, pick an AWS profile with `--profile` since an `AWS_PROFILE=` prefix
  does not reach the read, and assign stdout so the value stays out of the transcript.
  capt-hook's `secret_stores` guard refuses any other form and names this one. While the owner
  is away from the Mac no prompt fires and the guard queues a card, so hold that step and keep
  going on the rest. A Sprite has no `cc-sudo`, so its reads stay refused.
- GitHub budget: read GitHub only through ccx, with `ccx vcs pr status`, `vcs pr state`,
  `vcs pr watch`, or `ccx vcs gh -- <gh args>` for any other read. Writes stay plain
  `gh`. No loop reads faster than every five minutes, and every `ccx vcs gh` read in a loop
  passes `--max-age 5m`. A `ccx: GitHub rate limit floor:` refusal means stop reading
  until the reset it names. The supervisor posts an `urgent` record to the coordinator when any
  quota drops under 1500.
- Before designing, name what already grades or handles this signal: monitors, the
  release's Watch, open owner tasks and picks (`ccn task list`, `ccn answer search`,
  `ccn relevant`). If one covers it, route through it or delete the duplicate. Building
  alongside it is a return-early-with-options case. Run those searches again before any
  ask, decide with options, or PR; the `answer_search_first` hook warns a lane that does
  one with no search first.
- Before opening a fix, a flake fix or a gate, search open PRs and `dev` by symbol, test
  name and file, and fetch `dev` right before cutting the branch: a red that fails builds
  without your change usually already has a fix open.
- For a bug fix, before any code: (a) name which tree ran the failing path, old tree or
  next, from the run's own record (its build, binary version or pod image), never from
  the report's wording, and fix that tree; (b) read the fix against the register's rows
  on the subsystem it touches and quote the ones it meets, such as the local-sync rows (a
  local action never waits on a remote upload). A fix built against the wrong tree or
  against a row is closed, not shipped.
- Every fix carries a regression test that would have failed against the old code, never
  a trivial one; a fix that is reverted keeps its regression test, showing the underlying
  issue can no longer happen.
- Before reshaping a signature, rename or script contract, find every caller across the
  whole tree: a sparse checkout's grep proves nothing, and neither does a pipe through
  `head`. Grep main again right before merging, include other lanes' live scripts, and
  check who already pays a cost before reworking latency. Green CI misses a caller that no
  test runs.
- Copy full shas, never type or extend them by hand: take them from `git rev-parse` or
  `ls-remote` in the same call as the command that uses them, for `MERGE` lines,
  `cci --commit` and `--force-with-lease` values.
- Post a `head`, pushed or fixed record only after reading the remote head (`git
  ls-remote`) and chained after a push that succeeded: stop a script on a failed edit
  (`|| exit 1`), never pipe ship into `tail`, and check the head moved before posting. A
  record that claims a fix nobody pushed misleads the landing desks that replay stacks.
  Dry-run a bulk poster before it posts.
- In a public repo, PR bodies, commits, review comments and docs paraphrase the owner and
  cite answer ids; his words stay in cci and cc-notes. A Forge-AI/monorepo PR body quotes
  his words, since the finder grades a paraphrase as missing the row. A PR adding or changing a public API
  says, in its body and its lines to him: the signature, the alternatives weighed, the choice and why. Run `gh pr` verbs directly, never through a
  script file that hides them from the hooks. Correct an open PR's body unasked with
  `gh pr edit <n> --body-file <file>` when it turns out false or when evidence a grader
  reads arrives; rework that leaves it true goes in one PR comment.
- Never run `ccx vcs cleanup resume`: physical deletion stays paused.
- Findings, decisions, and handoffs go to cc-notes (`ccn note add`, `ccn log append`,
  `ccn papercut`), never only into your report.
- Fix or accept each reviewer-bot finding with the risk
  named. Post a `head` record for every push, addressed
  `--to desk-runner,fidelity-pipeline,finder-desk` and never to the coordinator, as are your
  `state` records. A restack that moves several PRs posts one record carrying every head,
  `cci post --kind head --head <pr>=<was>..<sha> --head <pr>=<was>..<sha> ... --attr delta=<class>`, with a
  note after the sha such as `patch-identical` when the range-diff says so, never one record per PR. Each
  `<sha>` is the full 40 characters: a short one leaves the row at its old head until the next read.
  The class says what the push did, and it decides whether the owner's tap stays: `rebase`,
  `review-fix` or `ordered-fix` keep it, and `behavior` or `scope` send the PR back to him, the note
  saying what changed. `reference/landing.md`, tap carry, has the rule. PRs that differ in class take
  one record per class. A port of an old-tree PR adds `--attr port-of=<old PR> --attr port-interacts=no|yes`
  to every head record, one record per port, and `port of #<n>` is the first line of its PR body and opens every card and reply naming it. A port lands faithful to that PR, and a fix past it is a follow-up PR on top; `reference/landing.md`, Old-tree forward port, has the rule. The supervisor's finder reads that head and posts its `finder:` record to you; fix or rebut each major before you call
  a PR done. On a drive whose runner has no finder, run one finder pass yourself.
  A `## Body addendum` that answers a second-fix major by naming the class the fix
  removes posts one `head` record at the unchanged sha, `--head <pr>=<sha>`, with the
  note `body edit`: the finder reads the body again against that head's major, with no
  new codex pass, and posts `finder: ok` when the second fix was its one major. A port names no
  class: a body that opens `port of #<n>` draws no second-fix major, and the class fix is a
  follow-up its `finder: ok` lists.

A change does what its request names and nothing else: behavior the request does not name stays, and a lane that thinks it must go brings that as a pick and never removes it.

Escalate early, never improvise: scope surprise, an assumption the code refutes, an
auth or approval gate, or two failed approaches. Ask with 2-4 options.

Finish: a lane with a PR finishes only once its squash `(#N)` is on the base branch.
It posts one PR comment opening with `## Rulings` and one opening with `## Regression
tests`, which lists each test id and links one CI run where that test failed against
the old tree. The body's `## Risk profile`
states Load and Data in prose. Nothing checks the test link; the fidelity desk reads each
named test against the failure it claims. A change that deletes a code path carries its caller
trace before the coordinator GOs it: every caller of the removed path, each one read, and
what the path deletes or changes for each. The fidelity desk grades what the deletion does
to each caller, never the diff's shape.
Drive to a terminal state, post `cci post --kind done --to root` with the outcome, since
the lane supervisor relaunches a lane that stops without one. A turn ends only on a typed
record, and `cci hook stop` refuses every other stop: `done`, `stopped`, or an open `ask`,
`blocker` or `blocked`. To wait on an event, post `cci post --kind blocked` with `--pr N`,
`--target T`, `--lane-ref L`, or a `card-` id in its text, and stop; that event wakes you,
as do a red PR you parked on and a record addressed to you by name. To wait on a monitor or
`lr wait --wake` you armed, post `blocked --attr until=<span>`, such as `45m`, with the text
naming what it watches: no release or other event wakes you, the supervisor never relaunches
you inside the span, and past it pages you as overdue. `done` retires you for good, so post it only when
nothing is left: while you own an open PR it is refused, and you post `blocked --pr N`
instead. Handing work to another lane is `blocked --lane-ref <it>`. Every push to a
reported PR re-reports the new head with `ledger.py report --drive <drive>` in the same
turn. Then send the preamble's `worker_done` with
`--outcome succeeded|failed`, `--task-id <taskId>`, and `--dispatch-id <dispatchId>`
from that preamble. The three-sentence body names what changed, what was
found, and what is left; the PR numbers, full head shas, and any tool refusal, verbatim,
go in the report file its `--report-path` names. `worker_done` is your last act: the
coordinator's gc then closes your idle terminal and removes your worktree once it is clean
and pushed, so push everything and leave no background process running first. A send after
it that answers `run_required`, "No recipient or active Dispatch Run could be resolved", means
your Run settled: read `orca orchestration dispatch-show --task <taskId>` once, which reads
`completed`, and send nothing more. `orca orchestration request-show --request <id>` says
whether one earlier send took effect.
A standing desk, `role=desk` or `role=watch`, never reaches a terminal state: it
loops until rotation, through setup and quiet cycles alike, and never ends a turn
waiting. At rotation, on the supervisor's `ROTATE` record, it writes its handoff doc, posts
`cci post --kind handoff --ccn <doc id> --re <its seq>`, then sends `worker_done --outcome succeeded`
with the doc id in the body; the supervisor launches its successor from that doc.

Coordinator ids: <run id>; <cc-notes ruling and answer ids>.
```

## `<lane>.md` one lane's part

Orca's task-spec contract requires every spec to include `Target`, `Change`, `Constraints`, `Ownership`, and `Observable acceptance`. The shared `common.md` supplies `Authority`, `Escalate`, `Do NOT touch`, `Worktree`, and `Finish`. The `Plan task:` line feeds the progress record described in [drive-dashboard.md](drive-dashboard.md#plan-progress-lrplan-progress).

```
## Lane: <name> (<agent> <model> <effort>). <the mission in one line>.
ccx: role=<role; desk or watch for a standing desk> [cpu=high] [place=remote|local] [paths=<dir>,<dir>] [pr=none] [monitor=<Datadog monitor id>] tooling-lane=<key, for a tooling lane only>
Target: <files, component, or environment in scope>.
Change: <the outcome to produce, such as "one failing database never blocks a release", never a design>.
Constraints: <invariants, compatibility rules, do-not-touch boundaries>.
Ownership: <what this lane edits; every shared file and who edits it after it>.
Compute: <for a pass, backfill, dry run, or bulk rewrite: the in-region box recipe, region, and the bucket or team each process owns; never this Mac (SKILL.md R15). "none" otherwise>.
A box a lane tests on is a devbox: `ci devbox` or `ci mac-box` on EC2, or a Namespace devbox. A Namespace box a lane still holds is extended with `lr nsc extend <id> --drive <drive> --lane <name> -- --duration <d>`, never bare `nsc`, so its deadline is watched. Any Namespace devbox, Linux or Mac, is fine for a lane or a test box: make it with `lr devbox create --drive <drive> --lane <name> -- --platform <linux/amd64 or macos/arm64> --size <size>`, which names it for the lane and records who holds it. Only Namespace's plain Macs are off limits, meaning a macOS machine type from `nsc create` that is not a devbox: they count against CI's limit of eight VMs, and every long-lived one makes CI jobs wait. A Mac that is not a Namespace devbox is an EC2 Mac box through the monorepo's `ci mac-box` and its `test-mac` skill.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Design rulings, verbatim: <relevant register rules, their linked answer ids, and the entry point (symbol at file:line) each requires; or "none">. A contract quotes the owner's words for each pick. A narrower reading the build needs, such as per key for per Mac, is an ask to the coordinator, never a choice. Its failing-first test asserts the owner's invariant, such as process count flat in chat count, not the narrowed case.
Design check: before READY-FOR-SHIP, post `DESIGN-CHECK <symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>` as a `contract` record on the bus and keep building; never wait for a confirm. The coordinator withdraws it only on a mismatch; a change that leaves out part of a ruling holds.
Design questions: settle each from this spec, the register, and the base branch yourself and post the choice as a `contract` record with its reason. Ask the coordinator only when the choice changes owner-visible behaviour, and keep building the rest meanwhile.
Standing rules register: <id of the newest `standing-rules:<slug>` doc, or "none">.
  Carry the owner-approved register's at most 35 rules, never full-answer quotes.
  Claude workers carrying CLAUDE_LONG_RUNNING_DRIVE receive it once at SessionStart.
  Above 9,000 characters, read it with `ccn doc show <id7>` before acting.
  For Codex, paste the body from `ccn doc show <register id>` here verbatim.
  Codex workers have no hooks. Claude drive sessions receive a relevant full answer
  only when the key-moment judge selects it, once per lane per answer.
Every rule binds this lane. Your READY names the rulings your diff touches.
Mechanisms the plan bans: <the drive's list, each shape with its rule id, or "none">. Before each push, run `rulings.py sweep --diff <file>` over your own diff, and fix each `MECHANISM-BANNED` line it prints or cite the owner's answer naming that shape.
Scale: <the N each path this lane changes runs at: chats, teams, sessions, or runs on one Mac or one pod; or "no N">. The bar: an interactive step answers within half a second at that N, and processes, timers, and connections per host stay flat as N grows, proved by a test at N = 1 and N = 1,000. A loop that awaits I/O per item, or a cap, timer, cache, retry, or idle wait standing in for a fundamental fix, is a defect the finder grades major; a diff that adds one names in the PR body the fix it stands in for and the owner pick that deferred it, or builds the fix. The runner warns on a non-incident launch whose brief has no `Scale:` line and launches it anyway.
Standing rules served: <`#<seq>` ids with their answer ids, or "none">; your task cites them, and completing this lane never retires them.
Plan task: register your unit at spawn so the progress record's plan counts include it: `ccn task add "<unit>" --plan <plan id> --label step:<n> --label program:<slug> --criterion "<done when>"`, then `ccn task start`, `ccn task criterion met|failed`, and `ccn task done` as it moves. The coordinator fills the plan id and step, or writes "none".
Observable acceptance: <the test, output, or PR URL that proves completion>.
```

## A design lane's part

A lane that designs, mocks or redesigns anything the owner sees, such as a dashboard, a board, a Slack card format or a skill's UI, carries these lines in its `<lane>.md` part, before `Change:`. Its brief never says to design from a blank page or "not from today's layout"; a change the owner wants away from the live artifact still starts from it.

```
Artifact: <the live artifact: its URL or app, and the repo path that renders it>.
Design record: <the ccn docs, answers and owner picks on this artifact, by id, or "none found" with the searches run>.
First step, before any mockup: open the live artifact and screenshot it as it is; read its design record; list the owner's prior picks on it, each with its answer id and his words. Post the list as a `contract` record. Then draw each proposed change inside the live artifact, never as a standalone page.
Draw: every visual deliverable, such as a page or screen layout, a component mockup, a design pass over a UI, or a navigation shape, is drawn by gpt-6.1-sol through codex, never by a Claude model alone. A page redesign runs a Fable pass and a codex pass, then one combined design with codex's taste weighted higher, and a sidebar over a top bar where the two disagree.
Card: the card that shows the change sets `design.changes` to this artifact, `design.live` and `design.inPlace` to the two screenshots on the card, and `design.record` to the record above; cc-present refuses it without them.
Live: a real screenshot of the new render, such as the `lr dashboard restart --proof` shots, goes on a card before the change ships, and every live or landed record of it cites `card:<card id>` or the `owner:<m-id>` row of his yes there, since live means he saw and accepted the render; `dashboard_live_needs_his_yes` refuses the record otherwise. A card with an image, as a child or an option's visual, settles only on his answer tagged to that card: `lr board cards check`, the sweeps and the recheck never take an earlier answer, a ruling or a lane's report for that yes.
```

The owner-question rules a design lane follows (the recommended answer built on a branch, `build`, `board_text_gate`, and the card's `design` fields) are in `common.md` above and the `Card:` line.

## A standing desk's brief

A desk on Orca, whether on its first launch or as a successor after rotation, uses this brief. The `ccx: role=desk` line (or `role=watch` for a watch) gives it the standing desk contract; a brief without it launches one-shot.

```
# <lane>-<N>: <desk's job>, successor of <lane>-<N-1>
ccx: role=desk
Standing: <the duty this desk keeps>
Read cc-notes doc <handoff or desk doc id> (ccn doc show <id>) and continue exactly as it says.
<live standing rules by #<seq> id; anything this successor changes>.
Report on cci as <lane>-<N>. Loop until rotation at <hours>h; setup and quiet cycles are not done.
Rotate: write the handoff doc, then send worker_done naming its id.
```

## Scoped resume

Use Scoped resume only for a lane that has already reported. Never message a running lane; put anything it may need in a file its brief tells it to read, or wait. Never resume a `codex:codex-wrapper` after it reports; spawn a fresh one with a scoped brief instead.

```
SCOPED FOLLOW-UP. Your original brief is CLOSED. Do not redo any of it.
Already done, do not repeat: <one line>.
Answer only: <the one question>.
Reply ≤5 lines, then stop. No re-investigation, no new edits, no new files.
```

## Sequencer lane

One sequencer lane owns a whole wait-then-do chain and sends one message at the end of it, so the orchestrator never polls the queue itself.

```
Do:
  1. gh pr edit <n> --repo <repo> --add-label merge
  2. Poll origin/dev for the squash commit "(#<n>)" until it appears or 40 min pass,
     waiting between polls with `lr wait 30 mailbox:<lane>=<cursor file>`.
     A queue-merged PR reads state=CLOSED, mergedAt=null; the commit is the truth.
  3. On landing, <the dependent step this chain exists to trigger>.
Finish: one report - landed sha, dependent step result, or the blocking state.
```

## Checks before launch

- Each lane's `Ownership` excludes every other lane's files. If two lanes edit a shared file, the second is a stacked child of the first.
- Rewrite any brief that runs a pass, backfill, dry run, or bulk rewrite on the Mac before launch.
- Every command in `common.md` runs as written from a lane's worktree.
