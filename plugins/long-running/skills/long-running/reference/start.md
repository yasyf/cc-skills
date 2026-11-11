# Start or resume

Run this page when a drive starts and after every restart, resume and compaction of any session, because each leaves you knowing only what the disk and your role's block hold.

- No word from him is needed for this page's commands, the repository setting below, running `drive watch` again, a fresh agent for a job, or restarting a coordinator whose process has exited.
- Moving a live session into a fresh Orca terminal, onto another model or build included, needs his word naming that session.

## Open each session in Orca

1. Start each session in its own Orca terminal with the commands below, because he finds each in Orca's sidebar and opens it with one command. A tmux session, `nohup`, `claude --bg` or other headless start shows nothing in Orca.
2. Whoever starts the drive runs them. `<worktree>` is the drive's checkout, the monorepo checkout's root for a monorepo drive; add a repository Orca does not list first with `orca repo add --path <its main checkout>`. The first command names the worktree after the drive in Orca's sidebar. The fourth starts the meta session, which holds drive tooling. The last starts a sub-coordinator, the coordinator session for one project beside the top, once an order is filed under `<project>`; `<lane>` is that name lowercased, each run of other characters a dash (`Iris` is `iris`).

```sh
orca worktree set --worktree path:<worktree> --display-name <drive>
orca terminal create --worktree path:<worktree> --title <drive>-coordinator --command "claude -n <drive>-coordinator --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"outputStyle\":\"default\",\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the coordinator: run Start or resume for that role'" --json
orca terminal create --worktree path:<worktree> --title <drive>-communicator --command "claude -n <drive>-communicator --model claude-opus-5-5 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"outputStyle\":\"default\",\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the communicator: run Start or resume for that role'" --json
orca terminal create --worktree path:<worktree> --title <drive>-meta --command "claude -n <drive>-meta --model claude-opus-5-5 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"outputStyle\":\"default\",\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox -- '/long-running <drive> as meta: run Start or resume for that role'" --json
orca terminal create --worktree path:<worktree> --title <drive>-<lane> --command "claude -n <drive>-<lane> --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"outputStyle\":\"default\",\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the sub-coordinator for <project>: run Start or resume for that role'" --json
```

3. `--settings` keeps each context small: a Bash result over 6,000 characters comes back as its first 2 KB and the path of the whole, and the reminder to narrate after silent turns is off, since nobody reads a drive's terminals. `outputStyle` `default` overrides the repository's own output style, such as `Concise`, whose reminder Claude Code attaches to about half of a session's model calls: 8,500 tokens an hour in the communicator, 10,400 in the coordinator.
4. Ten seconds after each create, check that `orca terminal show --terminal <the handle the create printed> --json` reads `"orphaned": false`. Orca draws a new terminal for about five seconds, so an earlier read proves nothing.
5. `"orphaned": true` means Orca dropped the terminal because it hides the worktrees it did not make for this repository; `orca worktree show` still finding it proves nothing. Fix it with the repository's setting, never a restart: `orca repo set --repo path:<main checkout> --external-worktree-visibility show`. Every session keeps running, and its terminal reads `"orphaned": false` once it is set.
6. Run that setting for the drive's own repository only, and tell him you ran it, because it changes what his Orca sidebar lists.

## Each session

`drive manual start <session>` prints that session's row of each table below.

| Session | Model, cci lane and cursor | Owns | Never |
|---|---|---|---|
| communicator | Opus 5.5; lane `communicator`, cursor `communicator` | His DM: the ack of each message of his, one background handler for it, and a handler for each `report`, `ready` and `ask` record another session sends it, so every post he gets is a handler's. | Builds, adds an order's jobs, or starts a drive tooling reader, designer or builder while a meta session is bound; a defect it hits is a `defect` record, which reaches the meta session. |
| coordinator | Fable 5.2; lane `root`, cursor `coordinator` | His orders other than drive tooling and their jobs, the watch's lines on them, the alert channels its `--watch` names, landing, installs of his product work and production cuts. | Posts to Slack, does an order's work itself, or starts a drive tooling reader, designer or builder; a defect it hits is a `defect` record, which reaches the meta session. |
| sub-coordinator | Fable 5.2; lane `<lane>`, cursor `coordinator-<lane>` | One project's orders, jobs, watch, landing and installs, beside the top coordinator. | Holds a Slack channel, or anything the coordinator never does. |
| meta | Opus 5.5; lane `meta`, cursor `meta` | Drive tooling, by [meta.md](meta.md): every fix to a drive tool, every rewrite of this skill, its agents or role rules a ruling of his calls for, and every install of a released tool. It holds each `Drive tooling:` order, and `drive order add` gives it each new one; the `record` tool and the post hooks send it each `defect` record and each `owner` record carrying a `ruling` attr; the watch hands it the lines of its orders; and `drive job brief <j> --serves "drive tooling"` gives it that job's order. | Posts to Slack: what he should see is a `report` record to `communicator@<drive>` opening `POST`, and a question for him an `ask` record there, which the communicator's handler posts. It touches no product order of his: `drive order add` refuses it an order not opening `Drive tooling:`. A drive with no meta session bound keeps all of this with the communicator. |

| Session | Binds with | A compaction keeps |
|---|---|---|
| communicator | `drive start <name> --role communicator` | Each handler still out, each acked message of his with no handler yet, and each agent it started that is still out, in at most 1,500 characters. |
| coordinator | `drive start <name> --role coordinator [--channel <his DM id>] [--watch <channel id>]...` | Each operation it owns that the drive file does not record, his words no order or answer holds yet, and the step it was in, in at most 3,000. |
| sub-coordinator | `drive start <name> --role coordinator --project <project>` | As the coordinator, for its project. |
| meta | `drive start <name> --role meta`, which binds it and takes every open drive tooling order the communicator held, his read-only questions aside, then prints its block | Each agent it started that is still out, each record it took whose `job` call has not run, and the step it was in, in at most 1,500. |

## The line that says where he opens a session

1. For a new drive, the coordinator's prompt ends `; his DM is <channel id>`, and its `drive start` passes that id as `--channel`.
2. Each session's `drive start` records the Orca terminal it runs in and ends with that role's line, a sub-coordinator's opening `sub-coordinator for <project>`; `drive status` prints the line for each role and each sub-coordinator, and changes nothing.
3. The line reads `open it with: orca terminal switch --terminal <handle>` only once Orca reads `"orphaned": false` for the terminal and its process tree holds the Claude Code process whose `~/.claude/sessions/<pid>.json` names the session.
4. Give him a switch command only from that line, never from a create's output, an environment variable or a record, because only the line is checked against what Orca shows; a hook refuses a switch command naming a terminal neither role runs in, a sub-coordinator's included.
5. Any other line carries no switch command. Tell him what it says in its own words, and act on it:
   - `not visible in Orca, which hides the worktrees it did not make for this repository`: the repository setting above shows it with no restart; `drive start` runs it for the drive's own repository and says so, and `drive status` prints it.
   - `not visible in Orca until restarted into a terminal` (Orca shows the terminal, but its process tree no longer holds the session) or `in no Orca terminal`, tmux included: move the session into a fresh Orca terminal by [migration.md](migration.md#into-an-orca-terminal), on his word.
6. When the coordinator's Claude Code process has exited, the communicator starts a handler that restarts it at once with step 5 of [migration.md](migration.md#into-an-orca-terminal), resuming its session, and tells him it did. He ruled that a coordinator that is down is restarted, never only reported.

## What a compaction keeps

1. He ruled that a compaction leave minimal context and the state on disk stay compact, so the `PreCompact` hook has the summary keep only what the disk does not hold:
   - each session keeps what its row of [Each session](#each-session) names, at most 1,500 characters for the communicator and the meta session and 3,000 for the coordinator.
2. The `SessionStart` hook then prints the role's block: its role, channels, `Subscribe:` line, rules, the coordinator's owed promised lines, and where to read the rest, never the orders, jobs or PRs. A sub-coordinator's names its project, the top, the communicator, its cci lane and `Hold no Slack channel`, and only its project's owed lines.
3. Claude Code also brings back the five files last read or written, each loaded skill body cut at about 20,000 characters, and every loaded tool, so load a tool only for the step needing it.
4. The `no_file_tools` hook refuses Read, Write and Edit in a drive session, because each file they open comes back after every compaction, and a Bash call that reaches a memory file (use `ccn note add`) or `cat`s a whole saved output (use `tail -c 2000 <file>`). A hook refuses a read of a page in Claude Code's plugin cache, which can be older: read a page with `drive manual <page> <row>`, a job's return with `drive job report <j>`, and a record with cci.
5. A skill body comes back as the session first loaded it until a restart, whatever was installed or loaded since. The role's block names the long-running version installed now, so follow the block where the two disagree.

## Steps for each role

Each role runs its steps here in order.

1. Check the model, then bind the role:
   - When a session runs a model other than its `orca terminal create` command names, or its `drive status` line adds `runs <model> on Claude Code <version>, which has no entry for that model`, restart it on the installed build by [migration.md](migration.md#into-an-orca-terminal), on his word. Change a live session's model only this way, never with `/model`, because a build older than its model silently runs it at a 200k window and the session then compacts every few minutes.
   - `drive start <name> --role <communicator, coordinator or meta> [--channel <his DM id>] [--watch <channel id>]...` creates the drive or binds this role to this session and its Orca terminal, then prints the role's block and line.
   - His DM is the communicator's. Each alert channel the drive reads is a `--watch`, kept across restarts and held by the top coordinator; a later `--watch` adds one. His own words in such a channel reach the communicator, never the coordinator.
   - A sub-coordinator runs `drive start <name> --role coordinator --project <project>`, which binds this session for that project beside the top, replacing any session bound for it, starts the project's watch and prints its block. It refuses a project no order is filed under, naming those in use, a session holding a role or another project, and a lane already taken: another sub-coordinator's, one of the drive's own such as `root`, one its records were posted as, or one ending in `-<digits>`.
2. Hold every channel the block's `Hold with` line names, none for the meta session, each with no thread: his DM with `slack_watch(channel_id=<id>)`, and each coordinator channel by running `cc-slack subscribe --kind alerts,releases,posts --channel <id>` in Bash, which delivers one line per alert, release state or top-level post. Subscribing a channel the session already watches turns that watch into the subscription. One session holds a channel at a time, so a channel another session holds becomes yours only after that session runs `cc-slack unfollow` ([migration.md](migration.md)).
3. Run `cci drive use <name>`, then the block's `Subscribe:` line exactly as printed: a sub-coordinator's reads its lane at cursor `coordinator-<lane>`. The coordinator's `--to-kind` list includes `refuse`, so a refused `ask` wakes it, and `handoff`, the watch's lines it acts on now, with `--batch-kinds done,evidence` for the rest. The named cursor resumes where the role's last session stopped, so a record sent while it was down arrives now.
4. The communicator handles each reply `cc-slack dm-owed` lists as a message of his that just arrived, because he wrote them while no session held his DM.
5. The coordinator resumes its work:
   - Its `drive start`, and the `SessionStart` hook at each coordinator session start, compactions included, start the watch as its own process unless one runs and point the session's cci channel at it; a sub-coordinator's start its project's watch, which posts to `<lane>@<drive>`. A line it acts on now arrives as a `handoff` record, the rest as `evidence` records together at most every five minutes. Arm no `Monitor` for it; when a `WATCH stopped` line says the process ended, run the command it names: `drive watch`, or `drive watch --project <project>`, which runs only from that project's sub-coordinator's session.
   - Read `drive status`, and take state from it, records and cc-notes, never from memory, because the disk is the record.
   - After a compaction, run the `drive manual <page> <row>` your summary names for its step before acting, because a compaction brings back no page.
   - A restart ends every background agent. A job whose agent is gone gets a fresh one only when something needs it: a `running` job now, a `waiting` job when its next event arrives.
   - Write its brief with `drive job brief <j>`, passing the earlier brief's `--serves`, `--done-when`, `--where` and each `--ruling <answer id>`, plus `--handoff` with the branch, PR and last `said` line; the verb writes his order's and each ruling's words whole. The fresh agent's first step moves the job onto it, since the brief's `Drive` line names the job and the agent that held it ended with its session.
   - When a ruling comes back after a compaction as an id or a question title, read it whole with `cc-notes answer show <id>` before anything acts on it: the title is the question, and his answer is in the body.
6. The top coordinator runs `drive dashboard`, which starts the drive's dashboard, finds it running, or replaces one serving other code or addresses. Its first link is for his phone or laptop: HTTPS on this machine's full tailnet name when the tailnet issues certificates, else HTTP on its bare MagicDNS label; loopback follows. It listens on loopback and this machine's own tailnet addresses only, never a wildcard address, the LAN or Funnel.

A drive saved by the earlier one-session driver reads as that session coordinating; [migration.md](migration.md#from-one-driver-to-two-sessions) moves it to two.
