# long-running

**Delegating work doesn't save your context window if you still read the logs.** The orchestrator still drains its context reading logs, parsing JSON, and answering duplicate notifications itself. Here the coordinator reads verdicts and holds decisions while standing desks and a lane bus carry the traffic.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install long-running@skills
```

- The `captain-hook` plugin, version 12.94.0 or later, from the `captain-hook` marketplace, and the `live-dashboard` plugin, version 0.5.0 or later, from this marketplace.
- `ccx` for PR state, `cci` for the lane bus, and `ccn` for cc-notes, where drive state and the ledger live.
- Orca, when the drive launches worker lanes through `lr launch start` and `lr relay run`.
- Optional: a Cerebras key in the login Keychain item `cerebras-api-key` for the drive dashboard's assistant, whose controls stay hidden without it.

## Use it

### Run a multi-lane migration across dozens of units

Three or more lanes in flight flood the coordinator's context if every PR report, CI wait, and blocker comes back to the main window. With `long-running` installed, ask:

```text
Drive this migration across all 40 packages as a long-running drive, one lane per package
```

The coordinator initializes a `cc-notes` ledger with `ledger.py init --title "desk: $DRIVE"` and starts the drive with `drive.py start --ledger "$LEDGER"`. It spawns one lane per unit from the skill's lane brief template and boots standing desks from `reference/` briefs. It follows the skill's twenty-four hard rules, among them R1 "Take ground truth from the owning lane." and R2 "Delegate anything with a body." Lanes post decisions, heads, contracts, blockers, and asks once to `cci` and read from their own cursors. The supervisor, `lr supervisor run` under launchd, and its relay, `lr relay run`, manage worker launches, inbox traffic, and the merge queue. The coordinator checks status through `ledger.py summary --drive <drive>` or `ledger.py show --asks`.

### Land a pile of open PRs through the merge queue

Tracking more than ten open PRs at once turns the orchestrator into a human polling loop if lanes enqueue themselves or report raw CI output back to the coordinator. With `long-running` installed, ask:

```text
Land these 15 open PRs through the merge queue and tell me when they're all in
```

The ledger holds one row per PR the drive's lanes shipped, and owner asks sit beside them as rows. Lanes never enqueue themselves; the landing process is the drive's merge queue, enqueuing each ready stack's largest green bottom prefix as one batch and reconciling landings from the base branch. The coordinator owns holds, which are cci `hold` records it posts and resolves, and reads what a PR waits on with `lr board waits --pr <n>`.

### Rejoin an active drive in a fresh session

A drive that outlasts one context window loses its thread if owner rulings and in-flight state only live in chat history. With `long-running` installed, ask:

```text
Resume the drive
```

The resumed the coordinator runs `drive.py start --drive <id> --ledger <id>` to join the existing drive in the new session. The compaction handoff carries owner rulings verbatim, what landed, what waits on the owner, and the coordinator's next actions.

## What's inside

| Piece | Kind | What it does |
| --- | --- | --- |
| `long-running:long-running` | skill | Context discipline for multi-lane orchestration: numbered rules (R1-R25) plus landing D-rules applied with judgment first, ready-to-paste `reference/` lane briefs, and templates for the lane brief, scoped resume, sequencer lane, merge train lane, polling loop, and compaction handoff |
| `long-running:lane` | agent | A desk, shard, sequencer, poller, or other lane that invokes no skill; restricted tool allowlist that leaves out `Skill`, `ToolSearch`, and every Model Context Protocol tool except `cc-slack`, 1h prompt cache so a nine-minute poll never rewrites its context |
| `long-running:lane-ship` | agent | `lane` plus the `Skill` tool, for an implementation lane that ships a PR or calls a skill such as `submit-pr`, `open-pr`, or `codex` |
| `capt-hook/hooks/` | hook pack | About 30 captain-hook hooks, among them `root_context`, `task_list`, `lane_landing`, and `settled_questions`. They warn by default and block only on trust: an unapproved merge, revert, or rollback, hook-file edits, and session-protection guards. A judgment reminder re-injects the rules every 30 minutes; `judgment_reminder_minutes` changes it. |
| `drive.py`, `lr handoff` | CLI | Drive lifecycle and compaction handoff on `PATH` through `bin/` |
| `ledger.py`, `rulings.py` | CLI | Landing desk and merge queue tools over `cc-notes` for queueing reports, grading heads, guarding the merge label, managing holds, and inspecting queue state |
| `lr supervisor`, `lr relay run`, `lr launch`, `sprite-job.sh`, `lr launch start`, `orca-check.sh`, `lr watch prompts`, `inboxes.py`, `inbox-digest.py`, `inbox-rotate.py` | CLI | The supervisor and its relay, finder and fidelity passes on a remote Sprite box, Orca worker launchers, and inbox management tools |
| `lr incident`, `actions.py`, `monitor-watch.py` | CLI | Production incident lifecycle, lane helpers, and monitor transition watchers |
| `board_watch.py`, `plan-table.py`, `dashboard/app/server.py` | CLI | Owner board helpers and the drive web dashboard app |
| `sprite-cargo.sh`, `lr landing restack`, `remote-vet.py` | CLI | Lane helpers that build a ref on a remote Sprite box, restack a PR and the open PRs below it on a Sprite, or build and vet a lane's uncommitted change on a Sprite |

Part of [cc-skills](../../README.md).
