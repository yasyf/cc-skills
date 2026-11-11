# Migrate an old drive

Three moves, by hand. A drive run by one driver session moves to two sessions in place
([From one driver to two sessions](#from-one-driver-to-two-sessions)). A session running outside
Orca moves into an Orca terminal of its own ([Into an Orca terminal](#into-an-orca-terminal)). A
long-running 0.7.x drive moves into a new drive: carry what is still open, write down what is not,
and leave every old session running for him to close.

## From one driver to two sessions

A drive saved by one driver session reads as that session coordinating, so the driver becomes the
coordinator in place and keeps its jobs, whose agents answer only to it. A new session becomes the
communicator.

1. In the driver's session, load the `long-running` skill again, then run
   `drive start <name> --role coordinator`; it prints the coordinator's block.
2. Still there, give up his DM. `cc-slack follows` lists this session's watches: run
   `cc-slack unfollow <watch id>` for the watch on his DM channel with no thread, and for every `dm`
   watch on one of its threads. Those are the threads the driver posted in, and his reply there
   reaches only the session that posted, until it lets go. Keep the alert channels' watches.
3. The `Subscribe:` line of that block, which replaces the driver's subscription.
4. Start the communicator in its own Orca terminal with the communicator's command in
   [Start or resume](start.md), whose `--channels` let records and his messages
   wake it. It runs Start steps 1 to 4 for its role: `drive start <name> --role communicator`,
   `slack_watch` his DM, `cci drive use <name>`, `cci subscribe --reader communicator --cursor
   communicator`, and an answer for each reply `cc-slack dm-owed` lists. A driver running outside
   Orca shows `in no Orca terminal` on the coordinator's `drive status` line and moves by
   [Into an Orca terminal](#into-an-orca-terminal) on his word.
5. A question card he has not answered yet keeps its id, which names no job: the communicator
   forwards his answer with that id in the text, and the coordinator, which asked it, resumes the
   job that waits on it.
6. Check it: `drive status` names both sessions, and his next message gets the communicator's ack
   inside 5 to 10 seconds while the coordinator receives its `owner` record.

## Into an Orca terminal

A drive session in no Orca terminal, one in tmux, under `nohup` or headless, or one whose Orca
terminal no longer holds it, moves into a fresh Orca terminal of its own, resuming the same session.
Only on his word naming that session, and never while an incident order is open: the move waits
until its fix is live. A session whose line reads `not visible in Orca, which hides the worktrees it
did not make for this repository` never moves: the repository setting in
[Start or resume](start.md) shows it where it runs, with no restart. Moving the
coordinator is a restart: its background agents end with the old process, and Start step 5 gives
each job that needs one a fresh agent.

1. Read the role's `session`, `terminal` and `cwd` from `drive status --json` under `roles`. Under
   tmux, find the old pane: `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}
   #{pane_start_command}'` lists each pane with the `claude` line that started it.
2. Ask the old session to let go, and wait for its reply: `cci post --lane <your lane> --kind ask
   --to <root or communicator>@<drive> --text "Moving you into an Orca terminal on his word: run
   cc-slack unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor
   if you run one, reply released, and take no more work."` His messages in the gap wait in
   `cc-slack dm-owed`, and records wait on the role's cci cursor.
3. End the old one, which his word for the move covers: `tmux send-keys -t <pane> /exit Enter`
   under tmux, `orca terminal send --terminal <old handle> --text /exit --enter` for an Orca
   terminal, `claude stop <id>` for a `claude --bg` session. Then wait until its process has
   exited: `ps -p <pid>` lists nothing, `<pid>` being the one whose `~/.claude/sessions/<pid>.json`
   names the session. A resume while the old process runs makes a copy under a new id, and two
   sessions then answer for one role.
4. Only then start the new terminal, resuming that session:

   ```sh
   orca terminal create --worktree path:<cwd> --title <drive>-<role> --command "claude --resume <session> -n <drive>-<role> --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --settings '{\"bashOutputMaxChars\":6000}' --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the <role>: run Start or resume for that role'" --json
   ```

   The command is the session and nothing after it: it never ends in `exec zsh -l` or any other
   shell, which would keep a login shell running in the terminal once the session ends.

   Its prompt runs Start or resume, so `drive start` records the new terminal and the role takes
   back its watches, its cursor and, for the coordinator, `drive watch`.
5. Check it: 10 seconds after the create, `orca terminal show --terminal <the handle the create
   printed> --json` reads `"orphaned": false`, and the repository setting in
   [Start or resume](start.md) is the fix when it reads true. Then the role's
   `drive status` line reads `open it with: orca terminal switch --terminal <that handle>`, and
   `orca terminal read --terminal <handle> --screen --json` shows the session at its prompt with
   its Start steps done.
6. The coordinator's next `report` tells him which session moved and the command that opens it.

## Read a 0.7.x drive

Set `CLAUDE_LONG_RUNNING_DRIVE=<old>` first; the old `lr` refuses without it. Then read
`lr owner list --drive <old>` (his rows, with their due times), `lr board rows --drive <old> --all --json`
(each PR's head, tap, and hold), `lr board waits` (PRs waiting only on his tap), `lr incident list`, and
`cci who --drive <old>` with `lr lanes --json` (the sessions still standing). His rulings already live
in cc-notes answers.

## Carry

1. Start the new drive's two sessions, each in its own Orca terminal, with the commands in
   [Start or resume](start.md). In the coordinator's session, run `drive setup`,
   then `drive start <name> --role coordinator --channel <his DM>` with a `--watch <channel id>` for
   every channel the old drive watched beside his DM, its alert channels above all.
   `cc-slack follows --session <old holder's session id>` lists them. In the communicator's
   session, run `drive start <name> --role communicator`.
2. Hand the watches over. cc-slack lets one session hold a DM or channel watch at a time, and stopping
   the old holder's Monitor releases nothing. The old holder runs `cc-slack unfollow <watch id>` for
   each watch, finding the ids with `cc-slack follows --session <its session id>`; ask it with
   `cci post --drive <old> --lane root --kind ask --to <session> --text "Run cc-slack unfollow for each
   of your Slack watches and reply with the ids; keep answering him until drive <new> says it holds
   them."` Then the communicator holds his DM and the coordinator the other channels (Start step 2)
   and, once every `slack_watch` and `cc-slack subscribe` succeeds, the coordinator
   posts `cci post --drive <old> --lane root --kind handoff --to <session> --text "Drive <new> holds
   <DM> and <channels>."` The old holder keeps answering him until that record arrives.
3. Each order still open (its deliverable is not live, and he neither dropped nor replaced it), in his
   words verbatim, never a summary:
   `drive order add "<his words>" --from migrated:<old row> --due <the time he was promised>`.
   An open incident without a fix-live record is an order too, run by [incidents.md](incidents.md).
4. Each open drive PR, as a job with no agent under the order it serves (else the migration order):
   `drive job add builder --order <o> --goal "<PR title>" --repo <owner/name> --branch <branch>`,
   `drive job set <j> --state waiting --pr <url>`, then `drive pr add <url> --job <j>` (`--tap` if it waits on him).
5. A tap he gave carries over when the old ledger shows it in force, so he is never asked again:
   `drive pr set <url> --tapped Approve --tap-source migrated:<old>` (or `--tapped Hold`).
   A recorded exemption carries the same way: `drive pr set <url> --exempt "<class>"`.

## Adopt live work

- An adopted PR's job stays `waiting` with no agent. Start a builder on it only when an event needs
  action: failed checks, requested changes, a new head, or his tap.
- An implementation session mid-change is asked to push and stop:
  `cci post --drive <old> --lane root --kind ask --to <session> --text "Push your branch and PR now, reply
  with the branch and head, then stop work: drive <new> adopts it."` When it replies, carry its PR as in
  step 4.

## Cards, stand-downs and what stays behind

- In `~/.config/cc-slack/drive.toml`, set `[pr_links] card_command = ["drive", "card"]` and delete
  `preview_command`. `drive setup` put `drive` where cc-slack finds it. Check one card with
  `echo '{"numbers":[<n>]}' | drive card` before drawing any.
- Post one stand-down per old standing session, his communicator, the coordinator, and each standing
  helper: `cci post --drive <old> --lane root --kind stand-down --to <session> --text "Drive <new> replaces
  <old>; take no more work. The owner closes this session."` A stand-down retires the session in the old
  registry, so nothing relaunches it. Stop, close, or signal none of them: he closes them.
- Everything not carried goes in one cc-notes note (`note_add`), titled `Not carried from <old>`, one row
  per old order: its id, his words, and why it stayed (done, replaced by a later order, or stale). He
  revives one with `drive order add "<his words>" --from migrated:<old row>`.
- Tell him in one line: counts carried and left behind, the note's link, and that old sessions are his.
