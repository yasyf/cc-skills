# Migrate an old drive

Two one-time moves, by hand. A drive run by one driver session moves to two sessions in place
([From one driver to two sessions](#from-one-driver-to-two-sessions)). A long-running 0.7.x drive
moves into a new drive: carry what is still open, write down what is not, and leave every old
session running for him to close.

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
3. `cci subscribe --reader root --cursor coordinator`, which replaces the driver's subscription.
4. Open a new Claude Code session the way the driver was opened, with `--channels` carrying
   `plugin:cc-inbox@cc-inbox` and `plugin:cc-slack@cc-slack` so records and his messages wake it,
   and run `/long-running <name>`. It starts as the communicator (Start steps 1 to 4): `drive start
   <name> --role communicator`, `slack_watch` his DM, `cci drive use <name>`, `cci subscribe
   --reader communicator --cursor communicator`, and an answer for each reply `cc-slack dm-owed`
   lists.
5. A question card he has not answered yet keeps its id, which names no job: the communicator
   forwards his answer with that id in the text, and the coordinator, which asked it, resumes the
   job that waits on it.
6. Check it: `drive status` names both sessions, and his next message gets the communicator's ack
   within seconds while the coordinator receives its `owner` record.

## Read a 0.7.x drive

Set `CLAUDE_LONG_RUNNING_DRIVE=<old>` first; the old `lr` refuses without it. Then read
`lr owner list --drive <old>` (his rows, with their due times), `lr board rows --drive <old> --all --json`
(each PR's head, tap, and hold), `lr board waits` (PRs waiting only on his tap), `lr incident list`, and
`cci who --drive <old>` with `lr lanes --json` (the sessions still standing). His rulings already live
in cc-notes answers.

## Carry

1. In the new coordinator's session, run `drive setup`, then `drive start <name> --role coordinator
   --channel <his DM>` with a `--watch <channel id>` for every channel the old drive watched beside
   his DM, its alert channels above all. `cc-slack follows --session <old holder's session id>` lists
   them. In the new communicator's session, run `drive start <name> --role communicator`.
2. Hand the watches over. cc-slack lets one session hold a DM or channel watch at a time, and stopping
   the old holder's Monitor releases nothing. The old holder runs `cc-slack unfollow <watch id>` for
   each watch, finding the ids with `cc-slack follows --session <its session id>`; ask it with
   `cci post --drive <old> --lane root --kind ask --to <session> --text "Run cc-slack unfollow for each
   of your Slack watches and reply with the ids; keep answering him until drive <new> says it holds
   them."` Then the communicator holds his DM and the coordinator the other channels (Start step 2)
   and, once every `slack_watch` succeeds, the coordinator
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
