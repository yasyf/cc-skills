# Migrate an old drive

A one-time move, by hand, from a long-running 0.7.x drive into a new drive. Carry what is still open,
write down what is not, and leave every old session running for him to close.

## Read the old drive

Set `CLAUDE_LONG_RUNNING_DRIVE=<old>` first; the old `lr` refuses without it. Then read
`lr owner list --drive <old>` (his rows, with their due times), `lr board rows --drive <old> --all --json`
(each PR's head, tap, and hold), `lr board waits` (PRs waiting only on his tap), `lr incident list`, and
`cci who --drive <old>` with `lr lanes --json` (the sessions still standing). His rulings already live
in cc-notes answers.

## Carry

1. In the new driver session, run `drive setup`, then `drive start <name> --channel <his DM>`.
2. Each order still open (its deliverable is not live, and he neither dropped nor replaced it), in his
   words verbatim, never a summary:
   `drive order add "<his words>" --from migrated:<old row> --due <the time he was promised>`.
   An open incident without a fix-live record is an order too, run by [incidents.md](incidents.md).
3. Each open drive PR, as a job with no agent under the order it serves (else the migration order):
   `drive job add builder --order <o> --goal "<PR title>" --repo <owner/name> --branch <branch>`,
   `drive job set <j> --state waiting --pr <url>`, then `drive pr add <url> --job <j>` (`--tap` if it waits on him).
4. A tap he gave carries over when the old ledger shows it in force, so he is never asked again:
   `drive pr set <url> --tapped Approve --tap-source migrated:<old>` (or `--tapped Hold`).
   A recorded exemption carries the same way: `drive pr set <url> --exempt "<class>"`.

## Adopt live work

- An adopted PR's job stays `waiting` with no agent. Start a builder on it only when an event needs
  action: failed checks, requested changes, a new head, or his tap.
- An implementation session mid-change is asked to push and stop:
  `cci post --drive <old> --lane root --kind ask --to <session> --text "Push your branch and PR now, reply
  with the branch and head, then stop work: drive <new> adopts it."` When it replies, carry its PR as in
  step 3.

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
