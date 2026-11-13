# Migrate an old drive

Each move is done by hand:

- [From one driver to two sessions](#from-one-driver-to-two-sessions), in place.
- [Add a meta session](#add-a-meta-session), to a running drive.
- [Into an Orca terminal](#into-an-orca-terminal), for a session Orca does not show.
- [Read a 0.7.x drive](#read-a-07x-drive) through
  [Cards, stand-downs and what stays behind](#cards-stand-downs-and-what-stays-behind): a 0.7.x
  drive moves into a new one, carrying what is open and writing down the rest.

One move needs no hand. A picture the drive drew keeps its source in a `source.json` beside it,
which the dashboard draws live while Slack gets the picture. A code picture drawn before pictures
kept one gets it on the first `drive` command after install, from the fence a kept question or a PR
card's body still holds; the rest stay as drawn, counted in one line, and the pass never runs again.
Its Slack copy changes only when its card is redrawn, which draws it anew with today's renderer.

The same first command keeps an ASK file for each of his open question cards whose job's question
kept none, rebuilt from the card the drive stored, at `asks/<j>-<seq>.md` for its waits id, so
`drive cards redraw` redraws it too. The file holds the card's question, summary and options word for
word, the recommended one marked, each Show me picture as the fence its `source.json` keeps or as its
picture line where none is kept, and under `Findings:` the findings of the newest block `drive job
ask` kept for its job asking the same question, unchanged since, else `Root cause: none: rebuilt from
the posted card`. It also runs only once, and each file it writes meets every rule a new block meets
before it draws.

## From one driver to two sessions

A drive saved by one driver session loads as that session coordinating, so the driver becomes the
coordinator in place and keeps its jobs, whose agents answer only to it. A new session becomes the
communicator.

1. In the driver's session, run `drive start <name> --role coordinator` and follow the block it
   prints where it differs from the skill loaded earlier, since loading the skill again sends
   nothing new.
2. Still there, release his DM, because his reply in a thread the driver posted in reaches only the
   driver until it lets go. Of the watches `cc-slack follows` lists, run
   `cc-slack unfollow <watch id>` for his DM channel's watch with no thread and each `dm` watch on
   its threads. Keep the alert channels' watches.
3. That `drive start` registered the driver's session with cci as the coordinator's, whose alias
   owns the subscription that replaces the driver's.
4. Start the communicator under cci and show it in its own Orca terminal with its lines in
   [Start or resume](start.md), whose prompt runs that page's steps 1 to 4.
5. A driver in no Orca terminal moves by [Into an Orca terminal](#into-an-orca-terminal).
6. A question card he has not answered keeps its id, which names no job. The communicator forwards
   his answer with that id in the text, and the coordinator, which asked it, resumes the job that
   waits on it.
7. Check it: `drive status` names both sessions, and his next message gets the communicator's ack
   inside 5 to 10 seconds while the coordinator receives its `owner` record.

## Add a meta session

Every drive runs a meta session beside the communicator and the coordinator. One started before the
meta session existed reads `meta: not started` in `drive status`, and its `Drive tooling:` orders
are the meta session's already, whoever held them before.

1. Run `drive start <name> --role <its role>` again in the communicator's or the coordinator's
   session: while no meta session is bound, it starts one under cci and shows it in an Orca terminal
   of its own with the lines in [Start or resume](start.md), whose prompt binds it with
   `drive start <name> --role meta`. Run those lines yourself only when the start printed that the
   meta session did not start.
2. From the moment it binds, the `record` tool and the post hooks send it each `defect` record, the
   `record` tool alone each `owner` record about the drive with the order it adds first, and the
   watch hands it its orders' lines.
3. Check it: `drive status` reads `meta: session <id>`.

## Into an Orca terminal

A session moves under cci and into a fresh Orca terminal, resuming the same session, when its
`drive status` line reads `in no Orca terminal`, as under `nohup` or `claude --bg`, or
`not visible in Orca until restarted into a terminal`. [Start or resume](start.md) sends one here
for a model or build change too: cci starts it again with the command its last `drive start`
registered. A session cci already runs in its tmux session that Orca does not show needs no move:
step 5's command shows it, with no restart and no word from him.

- Move a session only on his word naming it, because ending a session is his decision, which `drive move` records. A
  coordinator whose process has already exited is restarted at once by Start or resume, with no
  word.
- A hook refuses ending a role's session (`/exit` to its terminal, `claude stop`) while an
  incident order is open: move it once the fix is live.
- A hook refuses resuming a session that still runs, and names [Start or resume](start.md)'s
  repository setting when Orca only hides its terminal: that setting shows it where it runs.
- Moving the coordinator is a restart: its background agents end with the old process, and Start
  or resume step 5 gives each job that needs one a fresh agent.

1. Run `drive move <role> --his-word <the permalink of his message naming the move>`. It refuses
   without his own message or while an incident order is open, records his word on the role,
   registers its session with cci by its line in [Start or resume](start.md), and prints steps 2
   to 4 with the role's lane, terminal, session and process id filled in; it stops and starts
   nothing. Under tmux,
   `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_start_command}'`
   lists each pane with the `claude` line that started it.
2. Ask the old session to let go, and wait for its reply: `cci post --lane <your lane> --kind ask
   --to <root or communicator>@<drive> --text "Moving you into an Orca terminal on his word: run
   cc-slack unfollow for each of your Slack watches, cci unsubscribe, stop your drive watch Monitor
   if you run one, reply released, and take no more work."` His messages in the gap wait in
   `cc-slack dm-owed`, and records on the role's cci cursor.
3. End the old session, which his word for the move covers: `cci session type <drive>-<role> /exit`
   for one cci runs, `tmux send-keys -t <pane> /exit Enter` under another tmux,
   `orca terminal send --terminal <old handle> --text /exit --enter` for an Orca terminal, or
   `claude stop <id>` for a `claude --bg` session.
4. Wait until `ps -p <pid>` lists nothing, `<pid>` being the one whose
   `~/.claude/sessions/<pid>.json` names the session: a resume while the old process runs makes a
   copy under a new id answering for the same role, so a hook refuses it.
5. cci session run then resumes that session in the tmux session `<drive>-<role>`, which the old
   one's channel starts as it closes. Show it in a new terminal by the command `drive manual migration`
   prints, which starts that tmux session itself when it is down:

   ```sh
   orca terminal create --worktree path:<cwd> --title <drive>-<role> --command "cci session attach <drive>-<role>" --json
   ```

   Its resume prompt runs Start or resume, which records the new terminal and takes back the
   role's Slack watches, cci cursor and, for the coordinator, the drive's watch, which
   `drive start` starts unless one runs.
6. Check the terminal as [Start or resume](start.md) checks a create, never by another restart.
   Then the role's `drive status` line reads `open it with: orca terminal switch --terminal
   <handle>`, and `orca terminal read --terminal <handle> --screen --json` shows the session at its
   prompt.
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
2. Hand the watches over: one session holds a channel at a time, and stopping the old holder's
   Monitor releases nothing. Ask the old holder: `cci post --drive <old> --lane root --kind
   ask --to <its lane> --text "Run cc-slack unfollow for each of your Slack watches and reply with
   the ids; keep answering him until drive <new> says it holds them."` Once every `slack_watch`
   and `cc-slack subscribe` of Start or resume step 2 succeeds, the coordinator sends `cci post
   --drive <old> --lane root --kind handoff --to <its lane> --text "Drive <new> holds <DM> and
   <channels>."`; the old holder answers him until that record arrives.
3. Carry each order whose deliverable is not live yet, unless he dropped or replaced it, in his
   words verbatim, because his words are the spec:
   `drive order add "<his words>" --from migrated:<old row> --project <name> --workstream <name> --title "<a few words>" --thread <ts> --due <the time he was promised>`,
   reusing a pair `drive order unfiled` lists as in use where one fits. Leave `--due` and `--thread`
   (his DM thread's ts) off when he was promised no time. Leave `--kind` off, because a `build`
   order refuses step 4's builder jobs until a reader's research is kept. An open incident without a `fix-live` record is an order too, run by
   [incidents.md](incidents.md).
4. Carry each open drive PR as a job with no agent, under the order it serves, else under the
   migration order. Run `drive job add builder --order <o> --goal "<PR title>" --repo <owner/name>
   --branch <branch>`, then `drive job report <j> -` with the line `WAITING <url>` on stdin, which
   sets it waiting, then `drive pr add <url> --job <j>`, which records it waiting on his tap, with
   `--port-of <N>`, `--exempt <class>` or, outside `Forge-AI`, `--no-tap --why "<reason>"` when it
   lands without one.
5. Carry a tap he gave when the old drive's `lr board rows` show it in force, so he is never asked
   again: `drive pr set <url> --tapped Approve --tap-source <the permalink of his message or card>`,
   or `--tapped Hold`. A hold the old drive or a desk made itself is not his, and his PR card would
   say "Held by you": it carries as `--waits` on what it waits for, or, when nothing it waits on is a
   card, job, PR or release, such as a deletion held for the cutover, as a `hold` label on GitHub,
   which keeps it off his tap queue and card and makes the landing guard refuse it. `drive pr set`
   refuses `--tapped` with no permalink of his, and one already recorded as his tap drops with
   `drive pr set <url> --no-tapped --why "<what holds it now>"`, in the same call as its `--waits`,
   or once `drive status <url>` shows its label. On Nov 8 a root's hold on a PR he had just tapped
   "Approve both" on carried as his hold for more than a day. A recorded exemption carries the same way:
   `drive pr set <url> --exempt "<class>"`.

## Adopt live work

- An adopted PR's job stays `waiting` with no agent only when nothing is owed on it, since no event
  comes for work nobody is doing. A PR with work owed, such as a review finding not yet answered, a
  step after its merge like a fill, or a plan a ruling of his touches, gets a builder with a handoff in
  the same turn. On Nov 9 a builder stopped at 3:04 AM with a review finding open, its adopting job
  had no agent, and nothing touched it for two days. Otherwise start a builder only when an event
  needs action: failed checks, requested changes, a new head, or his tap.
- Ask an implementation session that is mid-change to push and stop: `cci post --drive <old> --lane
  root --kind ask --to <its lane> --text "Push your branch and PR now, reply with the branch and
  head, then stop work: drive <new> adopts it."` When it replies, carry its PR by
  [Carry](#carry) step 4.

## Cards, stand-downs and what stays behind

- In `~/.config/cc-slack/drive.toml`, set `[pr_links] card_command = ["drive", "card"]` and delete
  `preview_command`; `drive setup` put `drive` where cc-slack finds it. Before drawing any card,
  check one with `echo '{"numbers":[<n>]}' | drive card`.
- Send a stand-down to every old standing session, helpers included: `cci post --drive <old> --lane
  root --kind stand-down --to <its lane> --text "Drive <new> replaces <old>; take no more work. The
  owner closes this session."` A stand-down retires the session in the old registry, so nothing
  relaunches it. Leave every old session running for him to close; stop, close, or signal none.
- Write each order you do not carry into one cc-notes note (`note_add`) titled
  `Not carried from <old>`, one row per order: its id, his words, and why it stayed (done, replaced
  by a later order, or stale). He revives one with Carry step 3's `drive order add`.
- Tell him in one line: the counts carried and left behind, the note's link, and that the old
  sessions are his to close.
