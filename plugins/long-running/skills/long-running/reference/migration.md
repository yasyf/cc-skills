# Migrate an old drive

Three manual moves: a single-driver drive splits into two sessions in place ([From one driver to two sessions](#from-one-driver-to-two-sessions)); a session outside Orca moves into an Orca terminal ([Into an Orca terminal](#into-an-orca-terminal)); a 0.7.x drive moves into a new drive, carrying open work, recording the rest, and leaving old sessions running for him to close.

## From one driver to two sessions

A single-driver drive loads as the coordinator, so the driver becomes the coordinator in place to keep its jobs and agents, and a new session becomes the communicator.

1. In the driver's session, reload `long-running` and run `drive start <name> --role coordinator` to print the coordinator's block.
2. Release his DM so his replies stop routing to the driver: check `cc-slack follows` and run `cc-slack unfollow <watch id>` for the top-level DM watch and every thread `dm` watch, keeping alert channel watches.
3. Run the block's `Subscribe:` line to replace the driver's subscription.
4. Start the communicator in its own Orca terminal using the command in [Start or resume](start.md), whose `--channels` let records and his messages wake it. Run Start steps 1 to 4: `drive start <name> --role communicator`, `slack_watch` his DM, `cci drive use <name>`, `cci subscribe --reader communicator --cursor communicator`, and answer each reply in `cc-slack dm-owed`. If the coordinator's `drive status` line shows `in no Orca terminal`, move it via [Into an Orca terminal](#into-an-orca-terminal) once he asks.
5. Unanswered question cards keep their jobless ids: the communicator forwards his answer with that id, and the coordinator resumes the waiting job.
6. Verify `drive status` names both sessions, and that his next message gets a communicator ack within 5 to 10 seconds and an `owner` record at the coordinator.

## Into an Orca terminal

Move a session that is in no Orca terminal, in tmux, under `nohup`, headless, or missing from its Orca terminal into a fresh Orca terminal by resuming the same session. Move a session only when he names it and no incident order is open, waiting until any incident fix is live. If a session shows `not visible in Orca, which hides the worktrees it did not make for this repository`, do not move it; apply the repository setting in [Start or resume](start.md) to show it without a restart. Moving the coordinator ends its background agents with the old process, so Start step 5 gives each active job a fresh agent.

1. Read `session`, `terminal` and `cwd` under `roles` in `drive status --json`. For tmux, find the pane and its `claude` command with `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_start_command}'`.
2. Tell the old session to release its state and wait for its reply while his messages queue in `cc-slack dm-owed` and records wait on the cci cursor: `cci post --lane <your lane> --kind ask --to <root or communicator>@<drive> --text "Moving you into an Orca terminal on his word: run cc-slack unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor if you run one, reply released, and take no more work."`
3. Stop the old session, which his move order authorizes: `tmux send-keys -t <pane> /exit Enter` in tmux, `orca terminal send --terminal <old handle> --text /exit --enter` in Orca, or `claude stop <id>` for `claude --bg`. Wait until `ps -p <pid>` returns nothing for the `<pid>` whose `~/.claude/sessions/<pid>.json` names the session, because resuming before exit forks a duplicate session for the role.
4. Start the new terminal with the session command alone—never trailing `exec zsh -l` or another shell that would outlive the session—so `drive start` records the terminal and restores watches, cursor, and `drive watch`:

   ```sh
   orca terminal create --worktree path:<cwd> --title <drive>-<role> --command "claude --resume <session> -n <drive>-<role> --model claude-fable-5-2 --effort xhigh --permission-mode bypassPermissions --channels plugin:cc-inbox@cc-inbox plugin:cc-slack@cc-slack -- '/long-running <drive> as the <role>: run Start or resume for that role'" --json
   ```

5. After 10 seconds, verify `orca terminal show --terminal <the handle the create printed> --json` shows `"orphaned": false` (fixing it via [Start or resume](start.md) if true), `drive status` shows `open it with: orca terminal switch --terminal <that handle>`, and `orca terminal read --terminal <handle> --screen --json` shows the prompt after Start completes.
6. Include the moved session and its open command in the coordinator's next `report`.

## Read a 0.7.x drive

Set `CLAUDE_LONG_RUNNING_DRIVE=<old>` so `lr` runs, then inspect open state: `lr owner list --drive <old>` (orders and due times), `lr board rows --drive <old> --all --json` (PR heads, taps, holds), `lr board waits` (PRs awaiting his tap), `lr incident list`, and `cci who --drive <old>` with `lr lanes --json` (live sessions). His prior rulings are in cc-notes answers.

## Carry

1. Start both new sessions in Orca terminals per [Start or resume](start.md). In the coordinator, run `drive setup`, then `drive start <name> --role coordinator --channel <his DM>` with `--watch <channel id>` for every non-DM channel listed by `cc-slack follows --session <old holder's session id>`, especially alert channels. In the communicator, run `drive start <name> --role communicator`.
2. Transfer Slack watches, since cc-slack allows one holder per watch and stopping a Monitor does not release them. Ask the old holder (whose watches are in `cc-slack follows --session <its session id>`): `cci post --drive <old> --lane root --kind ask --to <session> --text "Run cc-slack unfollow for each of your Slack watches and reply with the ids; keep answering him until drive <new> says it holds them."` Once the communicator holds his DM and the coordinator holds the channels via `slack_watch` and `slack_subscribe`, release the old holder from answering: `cci post --drive <old> --lane root --kind handoff --to <session> --text "Drive <new> holds <DM> and <channels>."`
3. Record each open order (not live, dropped, or replaced) in his exact words: `drive order add "<his words>" --from migrated:<old row> --due <the time he was promised>`. Carry any open incident without a fix-live record as an order per [incidents.md](incidents.md).
4. Record each open PR under its order (or the migration order) with no agent: `drive job add builder --order <o> --goal "<PR title>" --repo <owner/name> --branch <branch>`, `drive job set <j> --state waiting --pr <url>`, and `drive pr add <url> --job <j>` (adding `--tap` if awaiting him).
5. Carry active taps and exemptions so he is not re-asked: `drive pr set <url> --tapped Approve --tap-source migrated:<old>` (or `--tapped Hold`) and `drive pr set <url> --exempt "<class>"`.

## Adopt live work

- Keep an adopted PR's job `waiting` without an agent until checks fail, changes are requested, the head moves, or he taps.
- Have any active implementation session push and stop before carrying its PR as in step 4: `cci post --drive <old> --lane root --kind ask --to <session> --text "Push your branch and PR now, reply with the branch and head, then stop work: drive <new> adopts it."`

## Cards, stand-downs and what stays behind

- In `~/.config/cc-slack/drive.toml`, set `[pr_links] card_command = ["drive", "card"]` and remove `preview_command` (`drive setup` installs `drive` on the path). Verify with `echo '{"numbers":[<n>]}' | drive card`.
- Retire each old standing session in the old registry so nothing relaunches it, while leaving the processes running for him to close: `cci post --drive <old> --lane root --kind stand-down --to <session> --text "Drive <new> replaces <old>; take no more work. The owner closes this session."`
- Record uncarried orders in one cc-notes note (`note_add`) titled `Not carried from <old>` with each order's id, his words, and why it stayed behind (done, replaced, or stale), which he can revive via `drive order add "<his words>" --from migrated:<old row>`.
- Report in one line the counts carried and left behind, the note link, and that old sessions remain for him to close.
