# Start or resume

Run this page when a drive starts and after every restart, resume and compaction of either session, because each leaves you knowing only what the disk and your role's block hold.

- No word from him is needed for this page's commands, the repository setting below, running `drive watch` again, a fresh agent for a job, or restarting a coordinator whose process has exited.
- Moving a live session into a fresh Orca terminal, onto another model or build included, needs his word naming that session.

## Open both sessions in Orca

1. Start each session in its own Orca terminal with the commands below, because he finds both in Orca's sidebar and opens either with one command. A tmux session, `nohup`, `claude --bg` or other headless start shows nothing in Orca.
2. Whoever starts the drive runs them. `<worktree>` is the drive's checkout, the monorepo checkout's root for a monorepo drive. Add a repository Orca does not list first with `orca repo add --path <its main checkout>`. The first command names the worktree after the drive in Orca's sidebar.

```sh
orca worktree set --worktree path:<worktree> --display-name <drive>
orca terminal create --worktree path:<worktree> --title <drive>-coordinator --command "claude -n <drive>-coordinator --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the coordinator: run Start or resume for that role'" --json
orca terminal create --worktree path:<worktree> --title <drive>-communicator --command "claude -n <drive>-communicator --model claude-opus-5-5 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the communicator: run Start or resume for that role'" --json
```

3. `--settings` keeps each context small: a Bash result over 6,000 characters comes back as its first 2 KB and the path of the whole, and the reminder to narrate after silent turns is off, since nobody reads either terminal.
4. Ten seconds after each create, check that `orca terminal show --terminal <the handle the create printed> --json` reads `"orphaned": false`. Orca draws a new terminal for about five seconds, so an earlier read proves nothing.
5. `"orphaned": true` means Orca dropped the terminal because it hides the worktrees it did not make for this repository; `orca worktree show` still finds such a worktree, so that proves nothing. Fix it with the repository's setting, never a restart: `orca repo set --repo path:<main checkout> --external-worktree-visibility show`. Both sessions keep running, and their terminals read `"orphaned": false` once it is set.
6. Run that setting for the drive's own repository only, and tell him you ran it, because it changes what his Orca sidebar lists.

## The line that says where he opens a session

1. For a new drive, the coordinator's prompt ends `; his DM is <channel id>`, and its `drive start` passes that id as `--channel`.
2. Each session's `drive start` records the Orca terminal it runs in and ends with that role's line; `drive status` prints the line for both roles and changes nothing.
3. The line reads `open it with: orca terminal switch --terminal <handle>` only once Orca reads `"orphaned": false` for the terminal and its process tree holds the Claude Code process whose `~/.claude/sessions/<pid>.json` names the session.
4. Give him a switch command only from that line, never from a create's output, an environment variable or a record, because only the line is checked against what Orca shows; a hook refuses a switch command naming a terminal no session of the drive runs in.
5. Any other line carries no switch command. Tell him what it says in its own words, and act on it:
   - `not visible in Orca, which hides the worktrees it did not make for this repository`: the repository setting above shows it with no restart; `drive start` runs it for the drive's own repository and says so, and `drive status` prints it.
   - `not visible in Orca until restarted into a terminal` (Orca shows the terminal, but its process tree no longer holds the session) or `in no Orca terminal`, tmux included: move the session into a fresh Orca terminal by [migration.md](migration.md#into-an-orca-terminal), on his word.
6. When the coordinator's Claude Code process has exited, the communicator starts a handler that restarts it at once with step 5 of [migration.md](migration.md#into-an-orca-terminal), resuming its session, and tells him it did. He ruled that a coordinator that is down is restarted, never only reported.

## What a compaction keeps

1. He ruled that a compaction leave minimal context and the state on disk stay compact, so the `PreCompact` hook has the summary keep only what the disk does not hold:
   - the communicator keeps each handler still out, each acked message of his with no handler yet, and each builder it started that is still out, in at most 1,500 characters;
   - the coordinator keeps each operation it owns that the drive file does not record, his words no order or answer holds yet, and the step it was in, in at most 3,000.
2. The `SessionStart` hook then prints the role's block: its role, channels, `Subscribe:` line, rules, the coordinator's owed promised lines, and where to read the rest, never the orders, jobs or PRs.
3. Claude Code also brings back the five files last read or written, each loaded skill body cut at about 20,000 characters, and every loaded tool, so load a tool only for the step that needs it.
4. The `no_file_tools` hook refuses Read, Write and Edit in either session, because each file they open comes back after every compaction, and a Bash call that reaches a memory file (use `ccn note add`) or `cat`s a whole saved output (use `tail -c 2000 <file>`). A hook refuses a read of a page in Claude Code's plugin cache, which can be older: read a page with `drive manual <page> <row>`, a job's return with `drive job report <j>`, and a record with cci.
5. A skill body comes back as the session first loaded it until a restart, whatever was installed or loaded since. The role's block names the long-running version installed now, so follow the block where the two disagree.

## Steps for each role

Each role runs its steps here in order.

1. Check the model, then bind the role:
   - When a session runs a model other than its `orca terminal create` command names, or its `drive status` line adds `runs <model> on Claude Code <version>, which has no entry for that model`, restart it on the installed build by [migration.md](migration.md#into-an-orca-terminal), on his word. Change a live session's model only this way, never with `/model`, because a build older than its model silently runs it at a 200k window and the session then compacts every few minutes.
   - `drive start <name> --role <communicator or coordinator> [--channel <his DM id>] [--watch <channel id>]...` creates the drive or binds this role to this session and its Orca terminal, then prints the role's block and line.
   - His DM is the communicator's. Each alert channel the drive reads is a `--watch`, kept across restarts and held by the coordinator; a later `--watch` adds one. His own words in such a channel reach the communicator, never the coordinator.
2. Hold every channel the block's `Hold with` line names, each with no thread: his DM with `slack_watch(channel_id=<id>)`, and each coordinator channel by running `cc-slack subscribe --kind alerts,releases,posts --channel <id>` in Bash, which delivers one line per alert, release state or top-level post. Subscribing a channel the session already watches turns that watch into the subscription. One session holds a channel at a time, so a channel another session holds becomes yours only after that session runs `cc-slack unfollow` ([migration.md](migration.md)).
3. Run `cci drive use <name>`, then the block's `Subscribe:` line exactly as printed. The coordinator's `--to-kind` list includes `refuse`, so a refused `ask` wakes it, and `handoff`, the watch's lines it acts on now, with `--batch-kinds done,evidence` for the watch's other lines. The named cursor resumes where the role's last session stopped, so a record sent while it was down arrives now.
4. The communicator handles each reply `cc-slack dm-owed` lists as a message of his that just arrived, because he wrote them while no session held his DM.
5. The coordinator resumes its work:
   - Its `drive start`, and the `SessionStart` hook at each coordinator session start, compactions included, start the watch as its own process unless one runs and point the session's cci channel at it. A line it acts on now arrives as a `handoff` record, the rest as `evidence` records together at most every five minutes. Arm no `Monitor` for it; when a `WATCH stopped` line says the process ended, run `drive watch`.
   - Read `drive status`, and take state from it, records and cc-notes, never from memory, because the disk is the record.
   - After a compaction, run the `drive manual <page> <row>` your summary names for its step before acting, because a compaction brings back no page.
   - A restart ends every background agent. A job whose agent is gone gets a fresh one only when something needs it: a `running` job now, a `waiting` job when its next event arrives.
   - Write its brief with `drive job brief <j>`, passing the earlier brief's `--serves`, `--done-when`, `--where` and each `--ruling <answer id>`, plus `--handoff` with the branch, PR and last `said` line; the verb writes his order's and each ruling's words whole. The fresh agent's first step moves the job onto it, since the brief's `Drive` line names the job and the agent that held it ended with its session.
   - When a ruling comes back after a compaction as an id or a question title, read it whole with `cc-notes answer show <id>` before anything acts on it: the title is the question, and his answer is in the body.
6. The coordinator runs `drive dashboard`, which starts the drive's dashboard, finds it running, or replaces one serving other code or addresses. Its first link is for his phone or laptop: HTTPS on this machine's full tailnet name when the tailnet issues certificates, else HTTP on its bare MagicDNS label; loopback follows. It listens on loopback and this machine's own tailnet addresses only, never a wildcard address, the LAN or Funnel.

A drive saved by the earlier one-session driver reads as that session coordinating; [migration.md](migration.md#from-one-driver-to-two-sessions) moves it to two.
