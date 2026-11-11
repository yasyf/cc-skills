# Start or resume

Each session runs in its own Orca terminal, so he finds both in Orca and opens either with one
command. His ruling, Nov 9 4:42 PM: "ok so fix the fucking skil, also open the communicator in orca I
cant find it, thers a CLI to open it". A bare tmux session, `nohup`, `claude --bg` or any other
headless start is forbidden: Orca shows nothing for it.

Whoever starts the drive starts both sessions with these three commands, `<worktree>` being the
drive's checkout, which for a monorepo drive is the monorepo checkout's root. A repository Orca does
not list is added first with `orca repo add --path <its main checkout>`. The first command names the
worktree after the drive in Orca's sidebar.

```sh
orca worktree set --worktree path:<worktree> --display-name <drive>
orca terminal create --worktree path:<worktree> --title <drive>-coordinator --command "claude -n <drive>-coordinator --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the coordinator: run Start or resume for that role'" --json
orca terminal create --worktree path:<worktree> --title <drive>-communicator --command "claude -n <drive>-communicator --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000,\"env\":{\"CLAUDE_CODE_SILENT_TURN_REMINDER\":\"0\"}}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the communicator: run Start or resume for that role'" --json
```

`--settings` caps each Bash result at 6,000 characters: a longer one comes back as its first 2 KB and
the path of the whole, so output a session reads once never sits in its context until compaction.
It also turns off Claude Code's reminder to say what the session is doing after a few silent
turns: nobody reads either terminal, and each reminder drew a paragraph that cost output tokens
and stayed in the context until compaction.
Then check each terminal 10 seconds after its create: `orca terminal show --terminal <the handle the
create printed> --json` must read `"orphaned": false`. Orca draws every new terminal for about 5
seconds, so an earlier read proves nothing. `"orphaned": true` means Orca dropped the terminal
because it hides the worktrees it did not make for this repository; `orca worktree show` still finds
such a worktree, so finding it proves nothing either. The fix is the repository's setting, never a
restart: `orca repo set --repo path:<main checkout> --external-worktree-visibility show`. Both
sessions keep running and their terminals read `"orphaned": false` once it is set. It changes what
his Orca sidebar lists for that repository, so it runs for the drive's own repository alone, and
whoever runs it says so.

For a drive that does not exist yet, the coordinator's prompt ends `; his DM is <channel id>`, which
its `drive start` passes as `--channel`. Each session's `drive start` records the Orca terminal it
runs in and ends with that role's line, the proof; `drive status` prints the line for both roles,
read only. Each reads `orca terminal show` for the terminal, which must read `"orphaned": false`,
and checks that its process tree holds the session's Claude Code process (`~/.claude/sessions/<pid>.json` names the session); only then
does the line read `open it with: orca terminal switch --terminal <handle>`, the command that brings
the session forward in Orca. He gets a switch command from that line alone, never from a create's
output, an environment variable or a record. Any other line gets no switch command, and he hears it
in its own words:

- `not visible in Orca, which hides the worktrees it did not make for this repository`: Orca dropped
  the terminal, and the repository setting above shows it with no restart. `drive status` prints the
  command; `drive start` runs it for the drive's own repository and says so.
- `not visible in Orca until restarted into a terminal`, where Orca shows the terminal but its
  process tree no longer holds the session, or `in no Orca terminal`, tmux included: the session
  moves into a fresh Orca terminal by
  [reference/migration.md](migration.md#into-an-orca-terminal), only on his word.

Then each session runs these steps for its role. A restart, a resume and a compaction land here
too. A compaction leaves almost nothing behind; his ruling, Nov 9 9:28 PM: "make sure we asap
install something that makes compactions result in very minimal context, and even the state on
disk needs to be maximally compact and very token efficient". The `PreCompact` hook gives the
summary its instructions, so it keeps only what the disk does not hold: for the communicator, each
handler still out and each acked message of his with no handler yet, in at most 1,500 characters;
for the coordinator, each operation it owns that the drive file does not record, his words no order
or answer holds yet, and the step it was in, in at most 3,000. The `SessionStart` hook then prints
the role's block: its role, what it holds, its rules, for the coordinator one line per order whose
promised line is owed, and the reads for the rest (`drive status`, `drive view`, `cci digest`),
never the drive's orders, jobs or PRs. The block stays the same size however many orders are open,
well under Claude Code's 10,000-character hook cap, past which Claude Code keeps a 2,000-character
preview and drops the rules and the first call at the block's end. Claude Code also brings back on
its own the five files the session last read or wrote, each skill body it loaded, each cut at about
20,000 characters, and every tool it loaded. So neither session reads or writes a file with Read,
Write or Edit, the skill's body is a short map of `drive manual` pages, and a session loads a tool
only for the step that needs it. Before that, on Nov 9 at 10:45 PM, the coordinator's compaction
brought back 25,000 characters of briefs and logs it had written, the first 20,000 of a
67,000-character skill body, and 31 tools whose definitions ran to 73,000 characters.

A skill body comes back as the session first loaded it: no install since replaces it, and loading
the skill again in the same Claude Code process sends nothing new; only a restart loads it afresh.
So the role's block names the long-running version installed now and wins wherever the two
disagree. On Nov 10 the communicator, which had loaded 1.0.14 on Nov 9, kept passing a thread to
`drive heard` from that old text after 1.0.129 dropped the flag, and the drive refused each one.

1. Never switch a live session's model with `/model`. A Claude Code build older than its model
   runs it at a 200k window it does not know is wrong: on Nov 9 the communicator, on 2.1.319, ran
   Fable 5.2 that way and compacted every 6 to 10 minutes. A session on another model, or one whose
   line in `drive status` says its build is older than the installed one, moves into a fresh Orca
   terminal on the installed build by [reference/migration.md](migration.md#into-an-orca-terminal),
   only on his word.
   `drive start <name> --role <communicator or coordinator> [--channel <his DM id>] [--watch <channel id>]...`
   creates the drive or binds that role to this session and its Orca terminal, and prints the
   role's block, then the role's line above. His DM is the communicator's. Each alert channel the drive reads is a `--watch`,
   kept in the drive across restarts and held by the coordinator; a later `--watch` adds one. His own
   words in such a channel reach the communicator, which holds his DM, and never the coordinator.
2. Hold every channel the block's `Hold with` line names, each with no thread: his DM with
   `slack_watch(channel_id=<id>)`, and each of the coordinator's channels by running
   `cc-slack subscribe --kind alerts,releases,posts --channel <id>` in Bash, which delivers one line
   per alert, release state or top-level post in place of each message. Subscribing a channel the
   session already watches turns that watch into the subscription. One session holds a channel at a time, so a
   channel another session holds is not yours until that session runs `cc-slack unfollow`
   ([reference/migration.md](migration.md)).
3. `cci drive use <name>`, then the block's `Subscribe:` line: `cci subscribe --reader communicator
   --cursor communicator`, or the coordinator's `cci subscribe --reader root --cursor coordinator`
   with a `--to-kind` list that adds `refuse` to the kinds cci's root hears by default, so a
   refused `ask` wakes it, and `handoff`, the watch's lines it acts on now, with `--batch-kinds
   done,evidence` for the watch's other lines. The named cursor
   resumes where the role's last session stopped, so a record sent while it was down arrives now.
4. The communicator handles each reply `cc-slack dm-owed` lists by its table: he wrote them while
   no session held his DM.
5. The coordinator's `drive start` starts the watch as its own process unless one runs, and each
   start of a coordinator session, compactions included, does the same and points its cci channel
   at the watch: a line it acts on now arrives as a `handoff` record, the rest as `evidence`
   records together at most every five minutes. It arms no Monitor for the watch; a `WATCH stopped`
   line says the process ended, and `drive watch` starts it again. Then it reads `drive status`. A restart ends every background agent, so a job whose agent is gone gets
   a fresh one only when something needs it: a `running` job now, a `waiting` job when its next
   event arrives. The fresh brief carries a handoff: branch, PR, the job's last `said` line, and
   the earlier brief's `Order`, `Serves` and `Decisions that bind` in his words, never an answer id
   in their place. The disk is the record: never re-derive state from memory. A ruling that comes
   back after a compaction as an id or a question title, as cc-notes prints the answers a session
   read, is read whole with `cc-notes answer show <id>` before anything acts on it: the title is
   the question, and his answer is in the body.
6. The coordinator runs `drive dashboard`, which starts the drive's dashboard, finds it running, or
   replaces one an earlier install started, and prints its links: first the tailnet one he opens from his
   phone or laptop, on this machine's bare MagicDNS label, then loopback. It listens on loopback and
   this machine's own tailnet addresses only, never the LAN or Funnel.

A drive saved by the earlier one-session driver reads as that session coordinating;
[reference/migration.md](migration.md#from-one-driver-to-two-sessions) moves it to two.
