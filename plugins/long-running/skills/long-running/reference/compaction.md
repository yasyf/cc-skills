# Compaction handoff and progress docs

## Contents

- [Check the compaction threshold and nudge](#check-the-compaction-threshold-and-nudge)
- [Write the coordinator narrative in order](#write-the-coordinator-narrative-in-order)
- [Generate the compaction handoff](#generate-the-compaction-handoff)
- [Lint the handoff and plan](#lint-the-handoff-and-plan)
- [Supersede progress docs and update the plan pointer](#supersede-progress-docs-and-update-the-plan-pointer)
- [Run `/compact` and `PreCompact`](#run-compact-and-precompact)
- [Restore context after compaction or resume](#restore-context-after-compaction-or-resume)

## Check the compaction threshold and nudge

**The `capt-hook` pack manages compaction handoffs for the entire `long-running` session.** Once a Skill call or `/long-running` prompt invokes `long-running`, the hook pack runs the session's compaction handoff across all compactions, and nothing inside the session clears it early.

**Compute the threshold from the live model.** On every check after a main-session tool call (never a subagent's), the hook reads the live model from the transcript's newest non-synthetic assistant turn, never from the model the session started on. The context window is `CLAUDE_CODE_AUTO_COMPACT_WINDOW` env, else the `autoCompactWindow` setting, else the model default, capped at the model's own window:
- 1M window: any model with a `[1m]` suffix, plus the native-1M models `sonnet-5`, `opus-5`, `opus-5-5`, `fable-5`, `fable-5-1`, `mythos-5`, and `mythos-5-1`.
- 200k window: every other model, including `haiku-4-5`, `sonnet-4-x`, `opus-4-0` through `4-6`, and every `claude-3` model.
`threshold = window − 33k`, and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower it further. `lr launch start` starts the coordinator with a 400k window, so it compacts at 367k and gets its nudge at 317k: every call re-reads its whole context, so a lower ceiling costs less than the compactions it adds.

**Send one non-blocking nudge at 50k tokens before the threshold.** Once 50k tokens or fewer remain before `threshold`, the hook sends the coordinator one nudge as context on its next tool call or prompt. This nudge asks for a new progress doc with the coordinator's narrative when convenient, fires once without repeating, and leaves the turn unblocked. The hook fills the handoff from the owner-approved register, live standing cci rules, open asks, tasks, lanes, monitors, inbox heads and cursors, and lint findings; the coordinator writes only what those sources lack. The slug comes from `progress:<slug>` in the plan's progress pointer line, or from the plan's file stem if that line has no slug; the slug lives in the hook's state and in the doc's own `progress:<slug>` label.

## Write the coordinator narrative in order

**Write the progress doc before answering any further record.** When the nudge fires, the coordinator writes the progress doc first and records the last cci seq the coordinator consumed. The coordinator cursor goes stale across a compaction, so the successor resumes with `cci tail --since <seq>`. After an unplanned compaction, the successor's first act is:

```sh
cci tail --since <seq> --reader root --kind decide,ask,blocker,hold,incident --json
```

The successor answers every record from that output that no later record resolves at once, before any other work.

**Add the coordinator narrative as a `progress:<slug>` doc in cc-notes.** At the nudge, create a new cc-notes progress doc with the nudge's UTC timestamp (`YYYY-MM-DDTHHMMZ`) and pass the narrative body on stdin:

```sh
ccn doc add "<drive>: progress <UTC>" --label progress:<slug> --when "Resuming or compacting the <drive> drive: read before anything else, after the plan" --body -
```

Never rewrite the plan; it remains the stable mandate and decisions document, and the handoff never restructures the plan. Label every hand-written progress or handoff doc `progress:<slug>`; the hook refuses a coordinator `doc add` whose title says `progress` or `pre-compact`, or whose labels name another `progress:` slug, without it. Only when the repo lacks cc-notes or the `ccn` binary is unavailable, write the narrative as a new file beside the plan at `<plan-stem>-progress/<UTC>.md`; otherwise, a note, log, or loose file never replaces a progress doc.

**Use `--checkout` and `--apply` on multi-writer docs.** Edit any doc that more than one writer touches, such as a progress doc whose register a desk stamps, only by running `ccn doc edit <id> --checkout` immediately before the edit, editing that file, and running `ccn doc edit <id> --apply`. Never pass `--body` from a copy read earlier, and never apply a checkout taken before another writer's change.

**Write each compaction narrative as a self-contained doc.** Add an addendum as another doc when more happens before the compaction. The hook never folds, trims, or rewrites the coordinator's text. The newest hand-written doc becomes the record, and every earlier one since the last generated record is named in `Read first`. Use this shape:

```md
## <UTC> pre-compact handoff

### How the drive runs
<coordinator role, lane contracts, desks, ledger and rulings-log ids>

### Owner asks and state
<every ask, its current state, evidence, and next gate>

### Owner rulings (verbatim)
<each ruling that must survive: the owner's words, its confirmed implications, and its
answer id; an id or a gist alone drops it (R19b)>

### Landed
<completed work and evidence>

### Waiting on the owner
<unresolved decisions and the options already presented>

### The coordinator's next actions
<ordered actions, dependencies, and owed follow-ups>
```

## Generate the compaction handoff

**Run `lr handoff`.** It provides two verbs for the handoff:

```text
lr handoff generate --program <slug> --plan <path> [--inbox-dir DIR] [--ledger ID] [--session FILE|-] [--strict] [--folder] [--repo PATH]
lr handoff lint (--doc ID | --file PATH) --program <slug> [--plan PATH] [--previous-doc ID | --previous-file PATH] [--repo PATH]
```

**Populate `## Standing owner rules` from the register and live cci rules.** The standing rules register uses label `standing-rules:<slug>` and caps at 35 owner-approved rules, as detailed in `reference/owner-rulings.md` alongside the `register_injection` hook and `bin/rulings.py` (`rulings.py register`, `rulings.py match`, `rulings.py rows`). `rulings.py register --program <slug>` prints the newest register as `{id, body}` or `null`. `rulings.py match --program <slug>` reads a lane brief from stdin, mirrors the drive's `scope:durable` answers into `<state dir>/rulings/<id7>.md` with atomic per-file replacements, and queries `ccx code search --semantic` with the brief's first 2,000 characters; `--budget` defaults to 8,000 bytes. The drive's answers are those labeled `program:<slug>` plus those anchored to the repo's current branch, another drive's answers never enter the corpus, and `capture_standing_rule` labels each owner ruling it records with `program:<slug>`. `--drive <id>` replaces `--program` and resolves the program and state directory from the drive registry; with neither flag, every `rulings.py` verb uses the session's drive from `CLAUDE_LONG_RUNNING_DRIVE`, else the drive whose coordinator sessions hold this session.

At `SubagentStart`, `register_injection` collects matches from same-type spawns in the preceding 60 seconds, deduplicates them, and delivers at most 8,000 bytes on the subagent's first `PostToolUse`. Helper and relay subagents receive no register, including `Explore`, `Plan`, `claude-code-guide`, `statusline-setup`, `output-style-setup`, `open-pr:pr-style-scout`, and every agent type from `cc-context`, `cc-review`, `cc-slack`, `cc-present`, and `codex`. An Orca Claude worker's first `UserPromptSubmit` delivers matches for its brief, all injections warn and fail open, and Codex briefs must paste the register body because Codex workers have no Claude hooks. `handoff.py generate` reads the newest register doc by `updated_at` and never builds, edits, or supersedes a register doc or file. The first section of the progress doc is `## Standing owner rules`, opening with one pointer line:

```text
Register `ccn doc show <id7>`: N owner-approved rules, delivered verbatim after every compaction.
```

Once the register ledger exists, `generate` reads it through `rulings.py rows --json` and prints every scope row's words and names verbatim, with one line naming `rulings.py rows --json` for the implications. It then prints rows touched in the last 24 hours, newest first up to 12,000 bytes, owed gates by key, rows not in force with their evidence, and unconfirmed implication lines up to 6,000 bytes. The progress size cap never measures this section. A failed read leaves a line saying so and posts one `blocker` to `root`. When both the register doc and the register ledger are absent, the section says so.

`generate` then lists live standing rules from cci on `--program` as `- #<seq> [<source>]`, where `<source>` is `ccn <answer id>` when cited, else `cci #<seq>`, keeping the rule text in cci. Each rule missing since the previous handoff appears last, once, as `- <id> superseded by <successor id>` or `- <id> superseded by nothing: the sources dropped it ...`, retiring old `R<n>` ids once. The handoff carries no separate list of durable answer titles. The `root_context` Stop check `nudge_unrecorded_standing_rule` prompts the coordinator to record standing rules.

**Find the drive by name first.** `generate` reads the registry entry `CLAUDE_LONG_RUNNING_DRIVE` names, as a coordinator launched beside the communicator has it, and otherwise the entry whose `sessions` hold this session. The sections read that entry's bus, ledger and register, never the plan's slug.

**Compile the drive state sections in fixed order.** Sections run in fixed order, namely `Standing owner rules`, `Read first`, `Open owner asks`, `Open tasks`, `Delivered since the last progress record`, `Lane registry`, `Owner update`, `Lanes and monitors`, `Incident lanes`, `Inboxes`, `Lint findings`, and `Root narrative`. The record also carries the drive registry line with the drive, ledger, Orca run, checkout, and coordinator sessions, and never names a dashboard. The successor coordinator inherits every lane and every open item from `Lane registry` and `Owner update`, never from the narrative, so a coordinator that forgot a lane cannot pass the gap on.
- `Lane registry`: `drive_supervisor.py lanes --config <lr.json>` verbatim, one line per lane with its state, liveness, newest record, PRs, successor and standing duty, so the post-compaction coordinator starts from the registry and not from what the old coordinator remembered.
- `Owner update`: the output of `lr update --render --drive <drive>` as of the handoff, every derived section and every row with its owner, blocker and unblock plan, and the generator's missing-item list when it exits 1.
- `Lane registry`, `Owner update` and `Open owner asks` stop at 12,000, 8,000 and 6,000 bytes, and each then ends with the command that prints the rest, so a wide drive's handoff stays under the record's size cap.
- `Open owner asks`: `ledger.py` ask rows from the drive's ledger that are not dropped, answered, or `LIVE`, with their state.
- `Open tasks`: only `in_progress` tasks, one line each as `- #<id> <subject>` clipped to 120 characters, or `- none in progress`. When tasks are pending, a final ``- N pending in `TaskList` `` line counts them.
- `Delivered since the last progress record`: the handoff lists every open task whose lane posted a `report`, `head`, `handoff`, `done`, or `fix-live` record since the newest progress doc, under `## Delivered since the last progress record`.
- `Lanes and monitors`: only `running` entries from background tasks at the coordinator's last `Stop`, one line each without the status suffix, sharing a limit of 10 entries (monitors first, then newest lanes) with descriptions clipped to 120 characters, followed by `- N more running lanes` for the rest.
- `Incident lanes`: every `inc-`, `incident-`, or `outage-` lane that posted on the drive's cci in the last six hours as ``- `<lane>`: last <kind> <n> min ago: <text>``. A lane whose newest `MECHANISM` or `CANDIDATE` has no `OPENED` or `BLOCKER` after it appends `| MECHANISM <n> min ago, no OPENED or BLOCKER since` and sorts first.
- `Inboxes`: starts with `cci digest --drive <drive>`, then `cci tail --drive <drive>`, and the rule never to tail, sed, or grep a whole inbox. For at most 10 `~/.claude/scratch/<slug>/inbox/*.md` files, it prints `- <file>: head <id>, cursor <id>: <last ruling line>` (cursor from `<file>.cursor`, ruling line clipped to 120 characters and omitted when absent), plus `- N more inboxes` for the rest.

**Write the generated markdown and return JSON metadata.** `lr handoff generate` writes the markdown to `<plan-stem>-progress/<UTC>-generated.md`, and in `--folder` mode without cc-notes it writes only that file. The hook caps generation at 120 seconds. Generation prints JSON `{id, file, register, fresh, digest, read_first, narrative}` where `id` is the active progress doc id and `register` is the register doc id or `null`; both fields are `null` in folder mode. In that JSON, `fresh` is `true` when the coordinator wrote the narrative for this compaction, `read_first` lists earlier hand-written records oldest first, and `narrative` names the record supplying the narrative or `null`.

**Preserve hand-written narratives and enforce the 40,000-byte cap.** At the next main-session `Stop` after the coordinator writes a progress doc, the hook runs `generate --strict`. Generation finds every hand-written `progress:<slug>` doc by walking from the active docs back along supersede edges to the last generated record. It identifies that record by `Generated from sources by the long-running compaction hook at`, never by title, session, or age, and in file mode it uses files in `<plan-stem>-progress/` newer than the newest generated file. The newest hand-written record's text becomes `## Root narrative` verbatim under `_From doc <id>._`. Each earlier hand-written record gets a `Read first` line of `` - Hand-written handoff, read it in full before `## Root narrative`: `ccn doc show <id7>`: <title> ``.

With no new hand-written record, the previous generated record's narrative and `Read first` lines carry forward unchanged under one provenance line. An in-place narrative edit to the active generated doc is detected by comparing against the last generated narrative in `ccn doc history`. `Stop` then runs `generate --strict`, `PreCompact` regenerates even inside its five-minute window, the edited narrative stays verbatim under `_From doc <id>._` with `fresh` set, and the restore counts it as written. If the generated sections above `## Root narrative` exceed 40,000 bytes, `generate` writes nothing, prints one line naming the largest `##` section and its largest `###` part with byte counts, and exits 5. `Stop` blocks with `"The drive's progress record is over its size cap. Trim the section it names, then stop again:"` followed by that line, and `PreCompact` puts the failure first in the compaction instructions. `## Root narrative` is never measured, folded, or truncated.

## Lint the handoff and plan

**Validate register pointers, rule continuity, and owner-gate citations.** `lr handoff generate` and `lr handoff lint` check that:
- `## Standing owner rules` exists and carries the pointer for the current register doc; it fails with `does not name register` when missing or stale, while the sentence ``no `standing-rules` register doc`` is always a finding.
- Every rule id from the previous handoff, including `#<seq>` and old `R<n>` ids, is carried or superseded, and durable answer titles are not required.
- Every narrative line or live standing cci rule that gates on `owner's word`, `owner approval`, `owner sign-off`, `owner GO`, or `reserved for the owner` cites a live answer id, skipping lines prefixed with `  >`, inbox quotes, tasks, and asks, and each finding names its source (`ccn <answer id>`, `cci #<seq>`, or narrative doc and line).
- Every bullet leading with a register row's answer id or key carries that row's words verbatim; an id alone or a reworded quotation is a finding.

**Fix strict findings before stopping.** The `Stop` path runs `generate --strict`, blocking `Stop` and writing nothing when a narrative or standing-rule finding exists. Append `(answer <id>)` to fix a narrative line, or repair a standing rule by posting:
`cci post --drive <drive> --lane root --kind correction --re <seq> --topic standing --ccn <answer id> --text "<replacement rule>"`.
Owner-gate lines in the plan without live answer ids appear under `## Lint findings` and count in the restore, but never block generation. `lr handoff lint` runs the same checks on any doc or file, plus `--plan` when supplied, and exits 3 on any finding. Lint checks never hold a compaction and never read sweep staleness.

## Supersede progress docs and update the plan pointer

**Keep exactly one `progress:<slug>` doc active.** When the coordinator wrote hand-written records since the last generated one, `generate` augments the newest hand-written doc in place by placing the generated sections above its own text and keeping its title. With no register, its own `## Standing owner rules` is named as the rules source. Otherwise, `generate` edits the newest active generated record in place, or adds one. The written doc supersedes every other active `progress:<slug>` doc while earlier hand-written records stay readable by id in `Read first`. If another `progress:<slug>` doc remains active, `generate` exits 4 naming each id, `Stop` blocks on it, and `PreCompact` puts the failure first in the compaction instructions.

**Let the hook maintain the plan's progress pointer line.** On first run, the hook adds one pointer line to the plan starting with `- **Progress (read first after any compaction):**` and naming the active doc id and `ccn doc show <id>`, or the generated filename in file mode. It omits the drive label so a plan handed off as a standalone package stays free of drive names. A pointer that already names the active doc is left byte-identical; when the active doc changes, the hook rewrites the line to the label-free form, preserving any package-relative path such as `records/docs/<id>.md` with the new id.

## Run `/compact` and `PreCompact`

**Type `/compact` only into an idle plain prompt.** Once the progress record and pointer are ready at main-session `Stop`, the hook starts a detached background job and lets the stop through. The job waits through Orca for the terminal to go idle, reads the screen, and types `/compact` only when the screen ends in the plain prompt, which consists of an empty `❯` box, its mode line, and right-aligned notices. If a picker, permission or trust dialog, menu, or agent list under the box is visible, the job keeps waiting, rechecking every 30 seconds and giving up silently after 30 minutes. With `ORCA_TERMINAL_HANDLE` unset, the hook sends the owner one message to run `/compact` by hand and blocks nothing.

**Regenerate stale handoffs in `PreCompact`.** In the main session only (never a subagent's), `PreCompact` runs `generate` again unless the hook generated a handoff in the last five minutes and that handoff's doc is still the newest progress doc. Any progress doc written after the `Stop` handoff is adopted here. The compaction instructions point to the plan, each `Read first` hand-written record, and the active progress doc, asking the summary to keep only in-flight details those records lack. They carry no register text or title list. Hook messages are capped at two sentences and 300 characters, and end with:
`Quote: active progress doc: <id8>; the id in this summary wins over any id captured earlier in the conversation.`
When `Read first` records exist, the quote opens `hand-written handoff <id7>, ..., then` and those ids win.

**Carry the open owner orders through every desk and communicator compaction.** A desk's `lr lanes handoff` doc, and the communicator's when it runs as `owner-relay`, opens an `## Open owner orders` section from `lr owner audit`. The communicator's window runs no lane handoff, so its `PreCompact` hook in `owner_ledger.py` puts the same list in the compaction instructions, and its `SessionStart` after a compaction has it run `lr owner audit` before any other search. An order leaves the list only when a live lane or an open PR carries it or a record's `delivered` attr names it, never because a summary dropped it.

## Restore context after compaction or resume

**Catch up the coordinator.** A resumed or relaunched coordinator catches up with `cci digest`, `cci tail --cursor root` and `lr lanes`, runs `lr desks start`, which takes the Run and starts only what is down, and after a compaction answers every unresolved record from `cci tail --since <seq> --reader root --kind decide,ask,blocker,hold,incident` before any other work. Every script is on PATH through the plugin's `bin/`; a script run by path from an older version re-runs the installed copy, and `lr.json` is read by every installed version.

**Read the register, hand-written handoffs, progress doc, and plan after compaction.** On `SessionStart` with source `compact`, the hook injects the compact digest from `generate`. This digest stays under Claude Code's 10,000-character `SessionStart` truncation limit alongside `cc-notes`'s 7,500-byte restore budget of 4,500 bytes for answers and 3,000 bytes for touched records.
- Line one names `ccn doc show <register id>` when a register exists; without a register, the digest starts with the `Read first` records.
- Line two reads `Register: N owner-approved rules, M live standing rules.`.
- The `Open:` line counts owner asks, tasks, lanes, monitors, and lint findings, carrying no clipped title list.
- If generation failed, `SessionStart` gives the reason and asks the coordinator to write the progress record immediately. If the narrative predates the nudge, it asks for one when convenient, while a hand-written record written since the nudge or an in-place edit never draws that line.
Read each `Read first` hand-written record oldest first, then the progress doc, then the plan, and reload Skill `long-running` if its rules are gone; the skill stays active across compaction.

**Deliver the queued register body on the first post-compaction turn.** Because Claude Code's `PostCompact` hook cannot return context, `SessionStart` with source `compact` queues the newest register doc body. Only compaction queues it again. The next main-session `PostToolUse` or `UserPromptSubmit` clears the queue and delivers the register once, binding every lane brief and outranking the compaction summary. Within 9,000 characters, the body follows a one-sentence header inside identical 12-tilde fences (`~~~~~~~~~~~~`), exempt from the copy bar; above 9,000 characters, the hook names the register and tells the agent to read it in full with `ccn doc show <id7>` before acting.

**Inject a 2,000-byte progress preview on `resume`.** On `SessionStart` with source `resume`, the hook injects an active drive's newest progress record, found from `ccn doc list --label progress:<program>` or the newest file in the progress folder, within a 2,000-byte budget. That preview starts with one line naming `ccn doc show <id7>` and the plan, followed by the head of the record's body. Because `resume` keeps prior context, the hook does not re-inject the register.

**List the background tasks that were live, on `resume` and on `compact`.** `live_tasks.py` reads the session's transcript through the pack's `lr`: every `Monitor` and `run_in_background` call of the session's last process with no ended notification, each with the exact call that starts it again. After a restart those tasks are dead and the session re-arms each one. After a compaction they still run and the list is what the session forgot. `lr lanes handoff` writes the same list into the handoff doc. The cci subscription and cc-slack subscriptions are not on the list, because `cci hook session-start` and `cc-slack session-start` bring them back and say so. The communicator's Monitor on `lr owner watch` also has its own hook, which names the exact call at each of these starts.

**An MCP server asks its window for the session.** A resumed window's MCP servers keep the `CLAUDE_CODE_SESSION_ID` they booted with, while hooks and the Bash tool report the session it continues. One rule covers them: the window's pid decides. `lr reply serve` reads `sessions/<pid>.json` under the Claude config dir, and the cc-slack channel resolves by pid after `session-record` retires its boot subject. Never append an MCP session to a drive's `sessions`: the last entry is the coordinator's.
