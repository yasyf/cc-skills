# Migrate an old drive

- [From one driver to two sessions](#from-one-driver-to-two-sessions), in place.
- [Add a meta session](#add-a-meta-session), to a running drive.
- [Into an Orca terminal](#into-an-orca-terminal), for a session Orca does not show.
- [Read a 0.7.x drive](#read-a-07x-drive) through
  [Cards, stand-downs and what stays behind](#cards-stand-downs-and-what-stays-behind): a 0.7.x
  drive moves into a new one, carrying what is open and writing down the rest.

Two moves run themselves, once, on the first `drive` command after install:

- A code picture drawn before pictures kept a `source.json` beside them gets one from the fence a
  kept question or a PR card's body still holds, so the dashboard draws it live. The rest stay as
  drawn, counted in one line. A picture's Slack copy changes when its card is redrawn.
- Each of his open question cards whose job's question kept no ASK file gets one at
  `asks/<j>-<seq>.md` for its waits id, rebuilt word for word from the card the drive stored, so
  `drive cards redraw` redraws it too. Its `Findings:` are those of the newest block
  `drive job ask` kept for the same question, else
  `Root cause: none: rebuilt from the posted card`. A card holding an attached picture no file
  under the drive's `pictures/` holds gets no ASK file, and `drive cards redraw` names it. A
  rebuilt file redraws under no rule while it matches the card he was shown.

## From one driver to two sessions

A drive saved by one driver session loads as that session coordinating, so the driver becomes the
coordinator in place and keeps its jobs, whose agents answer only to it. A new session becomes the
communicator.

1. In the driver's session, run `drive start <name> --role coordinator`. The block it prints is
   newer than the skill loaded earlier, so follow the block where they differ. The start also
   registers the session with cci as the coordinator's, whose alias owns the subscription that
   replaces the driver's.
2. Release his DM there, because his reply in a thread the driver posted in reaches only the driver
   until it lets go. Of the watches `cc-slack follows` lists, run `cc-slack unfollow <watch id>`
   for his DM channel's watch with no thread and each `dm` watch on its threads. Keep the alert
   channels' watches.
3. Start the communicator under cci and show it in its own Orca terminal with its lines in
   [Start or resume](start.md), whose prompt runs that page's steps 1 to 4.
4. A driver in no Orca terminal moves by [Into an Orca terminal](#into-an-orca-terminal).
5. A question card he has not answered keeps its id, which names no job. The communicator forwards
   his answer with that id in the text, and the coordinator, which asked it, resumes the job that
   waits on it.
6. It worked when `drive status` names both sessions, and his next message gets the communicator's
   answer while the coordinator receives its `owner` record.

## Add a meta session

A drive started before the meta session existed reads `meta: not started` in `drive status`. Its
`Drive tooling:` orders are the meta session's already, whoever held them before.

1. Run `drive start <name> --role <its role>` again in the communicator's or the coordinator's
   session. While no meta session is bound, it starts one under cci and shows it in an Orca terminal
   of its own with the lines in [Start or resume](start.md), whose prompt binds it with
   `drive start <name> --role meta`. Run those lines yourself only when the start printed that the
   meta session did not start.
2. From the moment it binds, it gets each `defect` record, each `owner` record about the drive with
   the order added for it, and the watch's lines on its orders.
3. It worked when `drive status` reads `meta: session <id>`.

## Into an Orca terminal

A session moves under cci and into a fresh Orca terminal, resuming the same conversation, when its
`drive status` line reads `in no Orca terminal`, as under `nohup` or `claude --bg`, or when
[Start or resume](start.md) sends it here for a model or build change: cci starts it again with the
command its last `drive start` registered.

What needs no move:

- A session cci already runs in tmux that Orca does not show: step 5's command shows it, with no
  restart.
- A coordinator whose process has already exited: Start or resume restarts it at once from step 5.
- A session running outside the drive's checkout: an install's reload or `drive reload` ends it
  once idle, an open incident order included, and cci resumes it in the checkout. For one no tmux
  session of cci's runs, the reload then opens step 5's terminal.

Moving a live session ends it, and with it every background agent it started. That is the one
thing here that takes his word: `drive move` wants the permalink of his message naming the session.

- A hook refuses ending a role's session (`/exit` to its terminal, `claude stop`) while an incident
  order is open. Move it once the fix is live.
- A hook refuses resuming a session that still runs, since the resume makes a copy under a new id
  answering for the same role. When Orca only hides the terminal, the hook names
  [Start or resume](start.md)'s repository setting, which shows it where it runs.
- After a coordinator move, Start or resume step 5 gives each job that needs one a fresh agent.

1. Run `drive move <role> --his-word <the permalink of his message naming the move>`. It refuses
   without his own message or while an incident order is open. It records his word on the role,
   registers its session with cci by its line in [Start or resume](start.md), its `--cwd` the
   drive's checkout, and prints steps 2 to 4 with the role's lane, terminal, session and process id
   filled in. It stops and starts nothing itself. Under tmux,
   `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_start_command}'`
   lists each pane with the `claude` line that started it.
2. Ask the old session to let go on the cci `post` tool, and wait for its reply: `kind: ask`,
   `lane: <your lane>`, `to: [<root or communicator>@<drive>]`, `text: Moving you into an Orca
   terminal on his word: run cc-slack unfollow for each of your Slack watches, cci unsubscribe, stop
   your drive watch Monitor if you run one, reply released, and take no more work.` His messages in
   the gap wait in `cc-slack dm-owed`, and records on the role's cci cursor.
3. End the old session, which his message for the move covers: `cci session type <drive>-<role> /exit`
   for one cci runs, `tmux send-keys -t <pane> /exit Enter` under another tmux,
   `orca terminal send --terminal <old handle> --text /exit --enter` for an Orca terminal, or
   `claude stop <id>` for a `claude --bg` session.
4. Wait until `ps -p <pid>` lists nothing, `<pid>` being the one whose
   `~/.claude/sessions/<pid>.json` names the session.
5. cci session run then resumes that session in the tmux session `<drive>-<role>`, which the old
   one's channel starts as it closes, in the drive's checkout wherever it ran before. Show it in a
   new terminal by the command `drive manual migration` prints, which starts that tmux session
   itself when it is down:

   ```sh
   orca terminal create --worktree path:<cwd> --title <drive>-<role> --command "cci session attach <drive>-<role>" --json
   ```

   Its resume prompt runs Start or resume, which records the new terminal and takes back the
   role's Slack watches, cci cursor and, for the coordinator, the drive's watch, which
   `drive start` starts unless one runs.
6. Check the terminal as [Start or resume](start.md) checks a create. Then the role's
   `drive status` line reads `open it with: orca terminal switch --terminal <handle>`, and
   `orca terminal read --terminal <handle> --screen --json` shows the session at its prompt.
7. The coordinator's next `report` tells him which session moved, the command that opens it and,
   after a coordinator move, each job given a fresh agent. Remote lanes need no word of the move:
   the watch reads each one's mail from its own Run.

## Read a 0.7.x drive

Set `CLAUDE_LONG_RUNNING_DRIVE=<old>` first, because the old `lr` refuses without it. Then read:

- `lr owner list --drive <old>`: his rows, with their due times.
- `lr board rows --drive <old> --all --json`: each PR's head, tap, and hold.
- `lr board waits`: the PRs waiting only on his tap.
- `lr incident list`: the incidents since midnight Pacific, none older.
- `cci who --drive <old>` with `lr lanes --json`: the sessions still standing, each with its
  `lane=`, the `<its lane>` that `--to` takes below, because cci addresses no session id.

His rulings already live in cc-notes answers.

## Carry

1. Start the new drive's sessions by [Start or resume](start.md). The coordinator runs
   `drive setup` first, and its `drive start <name> --role coordinator` adds a
   `--watch <channel id>` for each other channel the old drive watched, its alert channels above
   all, which `cc-slack follows --session <old holder's session id>` lists.
2. Hand the watches over. One session holds a channel at a time, and stopping the old holder's
   Monitor releases nothing. Ask the old holder: `cci post --drive <old> --lane root --kind
   ask --to <its lane> --text "Run cc-slack unfollow for each of your Slack watches and reply with
   the ids; keep answering him until drive <new> says it holds them."` Once every `slack_watch`
   and `cc-slack subscribe` of Start or resume step 2 succeeds, the coordinator sends `cci post
   --drive <old> --lane root --kind handoff --to <its lane> --text "Drive <new> holds <DM> and
   <channels>."`; the old holder answers him until that record arrives.
3. Carry each order whose deliverable is not live yet, unless he dropped or replaced it. Use his
   words where the old row holds them, since they brief the next job; a paraphrase marked as one
   is fine:
   `drive order add "<his words>" --from migrated:<old row> --project <name> --workstream <name> --title "<a few words>" --thread <ts>`,
   reusing a pair `drive order unfiled` lists as in use where one fits. Leave `--thread`
   (his DM thread's ts) off when the order has no thread. An open incident without a `fix-live`
   record is an order too, run by [incidents.md](incidents.md).
4. Carry each open drive PR as a job with no agent, under the order it serves, else under the
   migration order. Run `drive job add builder --order <o> --goal "<PR title>" --repo <owner/name>
   --branch <branch>`, then `drive job report <j> -` with the line `WAITING <url>` on stdin, which
   sets it waiting, then `drive pr add <url> --job <j>`, which records it waiting on his tap. Add
   `--port-of <N>`, `--exempt <class>` or, outside `<guarded org>`, `--no-tap --why "<reason>"` when
   it lands without one.
5. Carry a tap he gave when the old drive's `lr board rows` show it in force, so he is not asked
   twice: `drive pr set <url> --tapped Approve --tap-source <the permalink of his message or card>`,
   or `--tapped Hold`. `drive pr set` refuses `--tapped` with no permalink of his. A recorded
   exemption carries the same way: `drive pr set <url> --exempt "<class>"`.
6. A hold the old drive or a desk made itself is not his tap. It carries as `--waits` on what it
   waits for by its id: a job, PR, release or part, or an order he has not scheduled, such as a
   deletion held for the cutover, which the drive keeps in state `waiting` so no card asks him on
   it until he schedules it. A `hold` label on GitHub is the wrong carrier, since it is a check
   that holds the PR whatever he taps. A hold already recorded as his tap drops with
   `drive pr set <url> --no-tapped --why "<what holds it now>"`, in the same call as its `--waits`,
   or once `drive status <url>` shows its label.

## Adopt live work

- An adopted PR's job stays `waiting` with no agent when nothing is owed on it. A PR with work
  owed, such as a review finding not yet answered, a step after its merge like a fill, or a plan a
  ruling of his touches, gets a builder with a handoff in the same turn, since no event comes for
  work nobody is doing.
- Otherwise the watch's wake starts its builder when an event needs action: failed checks,
  requested changes, a conflict, a new head, or his tap. It runs
  `drive job launch <j> --handoff "<the PR> at <its head>: <the line>"`.
- Ask an implementation session that is mid-change to push and stop on the cci `post` tool:
  `drive: <old>`, `kind: ask`, `lane: root`, `to: [<its lane>]`, `text: Push your branch and PR now,
  reply with the branch and head, then stop work: drive <new> adopts it.` When it replies, carry its
  PR by [Carry](#carry) step 4.

## Cards, stand-downs and what stays behind

- In `~/.config/cc-slack/drive.toml`, set `[pr_links] card_command = ["drive", "card"]` and delete
  `preview_command`; `drive setup` put `drive` where cc-slack finds it.
  `echo '{"numbers":[<n>]}' | drive card` checks one card before any is drawn.
- Send a stand-down to every old standing session, helpers included: `cci post --drive <old> --lane
  root --kind stand-down --to <its lane> --text "Drive <new> replaces <old>; take no more work. The
  owner closes this session."` A stand-down retires the session in the old registry, so nothing
  relaunches it. The old sessions are not yours to stop, close or signal: they stay running for him
  to close.
- Write each order you do not carry into one cc-notes note (`note_add`) titled
  `Not carried from <old>`, one row per order: its id, his words, and why it stayed (done, replaced
  by a later order, or stale). He revives one with Carry step 3's `drive order add`.
- Tell him in one line: the counts carried and left behind, the note's link, and that the old
  sessions are his to close.
