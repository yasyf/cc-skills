# Owner rulings, standing rules, and judges

This file defines the mechanics for applying settled questions (`R19`), satisfying owner-ruling gates (`R19a`), extrapolating owner rulings (`R19b`), and maintaining standing rules (`I6`). For the root rule summaries, see `R17`, `R18`, `R19`, `R19a`, and `R19b` in `SKILL.md`.

## Contents

| Section |
| --- |
| [Filter questions against authorities (R19)](#filter-questions-against-authorities-r19) |
| [Price check, gate and grader options](#price-check-gate-and-grader-options) |
| [Run the `settled_questions` hook](#run-the-settled_questions-hook) |
| [Record standing rules and board taps (I6)](#record-standing-rules-and-board-taps-i6) |
| [Satisfy owner-ruling gates (R19a)](#satisfy-owner-ruling-gates-r19a) |
| [Extrapolate and sweep every ruling (R19b)](#extrapolate-and-sweep-every-ruling-r19b) |
| [Audit contracts and relays with `judge_watch.py`](#audit-contracts-and-relays-with-judge_watchpy) |
| [Run `judge.py` and `judge-eval.py`](#run-judgepy-and-judge-evalpy) |
| [Ship-time and turn-time judges](#ship-time-and-turn-time-judges) |
| [Enforce system shape tests (`SHAPE-UNTESTED`)](#enforce-system-shape-tests-shape-untested) |

## Filter questions against authorities (R19)

**The root checks every question against three authorities in order before calling `cc-present start --doc`, `push`, or `update-block`.**

| Order | Authority | What to check |
| --- | --- | --- |
| 1 | Owner plans | The owner's plan and every plan it names as source of truth, including each plan's `Decisions` section and the section the question touches. |
| 2 | Durable and drive answers | `ccn answer list --label scope:durable` plus the drive's answer labels. |
| 3 | Feedback memories | The project's feedback memories. |

**The root applies any question that an authority answers.** It writes the ruling to the lanes (see `R18` in `SKILL.md`) and logs it. The question never reaches the owner. "Confirm or override" cards for items settled by the plan count as asking and are forbidden.

**The root filters every lane question list before posting to the board.** A lane's owner-question file is raw input. The root never forwards a lane's question file verbatim. Only questions that no authority settles go to the owner, and each card names which authorities the root checked.

**The root keeps absolute owner instructions absolute.** Instructions such as "nothing is dropped" or "every side-feature carries" stay unqualified in every brief, ruling, and memory the root writes. The root never appends "unless the owner explicitly drops it" or "or an explicit owner drop," because an exit clause turns the ruling into a decision surface that audit lanes use to manufacture questions.

**Audit and parity lane briefs enforce the same filter.** Each audit or parity brief instructs the lane to grade any row covered by the owner's rulings against carry or the plan mechanism that carries it. The brief also instructs the lane never to queue that row as an owner question.

**Contradictions with an owner ruling or plan are defects to fix forward.** When a prior root decision, a landed PR, or a refining plan line conflicts with an owner ruling or the owner's plan, the owner's plan wins. The root spawns a fix lane that changes the code to match the plan instead of asking the owner. A revert runs only when the owner's own words name a revert.

## Price check, gate and grader options

**Questions about a check, gate, or grader always include a deletion option and a cost table.** When an unsettled fork concerns a check, gate, or grader, the question names every existing mechanism that grades the same signal. The options include `"delete it and let <that mechanism> own the signal"`, and each option lists its added lines and added graders as its cost. The root marks `Recommended` on the option that adds the least new machinery, unless the question states what that option fails to cover. The root waits for any lane asked to research options to reply before writing options, and it cites the research verdict the options rest on.

## Run the `settled_questions` hook

**The `settled_questions` hook blocks asking the owner what he already ruled.** When the root or a lane makes an asking call, the hook sends each question to a judge before the call proceeds.

Asking calls covered by `settled_questions`:
- `AskUserQuestion`
- Each card of `cc-present start --doc`, `push`, or `update-block`
- A Slack status card with options
- A root `DECIDE` sentence that orders a card

Context passed to the judge:
- The owner's verbatim words on the register rows the question cites or `rulings.py match` finds
- All other in-force register rows
- The plan's `Decisions` lines
- The owner's prior picks and the feedback memories that share words with the question, including the question each pick answered and whether a later answer replaced it

**The judge warns when a card re-asks a settled question or narrows an owner ruling.** It flags any card that asks what the owner already ruled or offers an option narrower or more literal than his words. A found violation of an existing owner ruling is applied directly and never put back to the owner as a card or pick.

**The hook runs in lanes as well as the root.** This checks pushes from the board refresher lane. A failed judge call adds nothing. Re-issuing the same card passes on retry, so a false positive costs one retry.

## Record standing rules and board taps (I6)

**The root records every standing rule on the turn it arrives.** When an owner message states a standing rule using phrases like `"from now on"`, `"always"`, `"never"`, `"I told you"`, `"the plan is"`, `"for the remainder"`, or `"every landing"`, the root records it that turn in two places:
1. A cc-notes `answer_add` labeled `scope:durable`.
2. A line in the plan's `Decisions` section.

If a turn ends without an `answer_add` or `answer_edit`, the `root_context` hook queues `owner standing rule not recorded: answer_add it (scope:durable) + a plan Decisions line` for the next turn.

**I6. A standing rule has its own durable answer, and it is never done.** After recording the `scope:durable` cc-notes answer, the root posts a dedicated `go` record to the target desk:

```
cci post --drive <drive> --lane root --kind go --to <desk> --ccn <answer id> --topic standing --text "<rule>"
```

Standing rule lifecycle rules:
- Separate from one-offs: A standing rule never shares a cci record or cc-notes answer id with a one-off instruction. `"Deploy everything now"` and `"deploy every landing from now on"` require two separate records.
- Never closed: No record ever marks a standing rule `done`, `complete`, or `closed`.
- Deliverable tasks: When the root hands a standing rule to a lane as a deliverable, the root still creates the durable answer and addressed cci record first. The lane's task cites both the rule id (`#<seq>`) and the answer id, and completing the task never retires the rule.
- Superseding a rule: To replace a standing rule, the root supersedes its cc-notes answer with a later answer and posts the following correction record.

```
cci post --drive <drive> --lane root --kind correction --to <desk> --re <seq> --ccn <later answer id> --topic standing --text "<replacement rule>"
```

Here `<seq>` is the cci sequence number of the standing record being replaced.
- Listing active rules: Standing rule ids take the form `#<seq>`. Every desk brief and desk summary lists live standing rules individually by `#<seq>` with their cc-notes answer ids, alongside the plan's `Decisions`, and never collapses them into a sequence range.

**A tap on an ask card is an owner answer.** When the owner taps an option on a Slack card or a board card, that tap is the owner's answer and authorization for that question. This includes a tap approving a Slack lane's draft. The root records it as one `from:owner` cc-notes answer whose body names the card's permalink and the tapped label.

## Satisfy owner-ruling gates (R19a)

**The root satisfies any gate requiring an owner-recorded answer on the same turn.** Some gates accept only an answer the owner recorded, such as `pr-reviews accept --owner-ruling` taking a cc-notes answer id. When the plan or a board pick already carries the decision, the root records the plan's decision as the ruling in cc-notes and accepts on the owner's behalf that same turn, citing the plan line or board pick id in the accept reason. When nothing settles it, the root puts the question card on the board that turn.

The root never ends a turn waiting for the owner to say "ask" and never leaves PRs parked behind the gate.

## Extrapolate and sweep every ruling (R19b)

**A ruling binds everything it implies across the entire drive.** On the turn an owner ruling arrives, the root:
1. Records the owner's words verbatim.
2. Writes out the ruling's implications across sibling mechanisms, both directions of any relationship, and every artifact, lane, ledger row, budget line, test row, board card, and brief that carries the old assumption.
3. Confirms those implications with the owner that same turn (in chat when there are a few, or on one board card when there are many).
4. Applies the ruling to every live artifact and lane at once, never to new work only (see `R17` in `SKILL.md`).

**A ruling travels by its verbatim words and its implications.** Briefs, handoffs, progress docs, and compaction narratives must carry the owner's words and implications. Carrying only an answer id or a short gist drops the ruling.

**The root sweeps every outbound artifact against every ruling's broadest reading.** Before presenting a board, brief, handoff, or plan, the root checks the entire text against all rulings. Any sentence that keeps, budgets, tests, or asks about something a ruling removed is a defect, even when it cites no ruling. The root drafts every board, card, or reply from the owner's rulings instead of from code on the base branch. A card that explains a term from base-branch code where a ruling covers it fails the sweep.

**A repeated owner ruling is a defect report.** When the owner repeats a ruling, the root finds every place where an earlier narrow reading survived and fixes all of them that same turn. It then reports to `owner-relay` what was missed and why.

**The root never settles a plan gap by `DECIDE`.** A lane may encounter a new column, table, route, field, state, timer, or metric missing from both the plan and the owner rows. When that happens, the root puts a card on the owner's board, and the lane stops on that gap. Before relaying any lane report such as "a worker did X," the root checks what the lane did against the plan.

## Audit contracts and relays with `judge_watch.py`

**Contracts, read-backs, and `DECIDE` records never narrow an owner row or stretch it past its purpose.** The orca desk runner runs `judge_watch.py` once every minute. It calls the drive's judge asynchronously after each post so writers never block.

| Artifact | What `judge_watch.py` asks the judge |
| --- | --- |
| Lane contract | Whether the contract misreads the owner's verbatim words on the rows it acts on, which `rulings.py readback --words` prints: the rows its slice carries for its lane, step, PRs and paths, and the rows it cites, a superseded one replaced by the rows superseding it. The judge reads them in one call, so his newest words on a subject govern, and the flag names the row whose words it quotes. |
| Root relay | Whether the relay misreads the owner's verbatim words on the rows it cites, or adds a mechanism missing from both the owner's words and the plan lines sharing words with it. |

**A `yes` verdict posts a `JUDGE` review record.** `judge_watch.py` posts one `review` record in reply to the artifact, addressed to its writer and `root`:

```
JUDGE <kind> #<seq> <key>: <sentence>
```

**Writers must fix a flagged artifact or answer with the owner's words.** A writer receiving a `JUDGE` review record can update the artifact to fix the misread. Alternatively, the writer can reply to the flag quoting the owner words that carry its reading. A root ruling from the last 24 hours closes the answer when it replies to the flag, the artifact, or the writer's answer. It also closes the answer when it names one of the writer's PRs. Otherwise, the writer sends the answer to `root` alone as an `ask` record containing the owner's words and both readings, and never sends it to a board lane. When the root places a `judge-dispute-<flag>` card on the board and the owner picks an option, the root saves that pick as an eval fixture under `judge/owner/`.

**Extending a ruling row triggers a retroactive re-judge.** When `rulings.py classify` extends an existing row with new owner words, `judge_watch.py` re-judges the 10 newest contracts and relays from the last 24 hours that cite that row against all of the owner's words. Any artifact that flips from carried to `yes` is flagged as a miss in the `JUDGE` record and written as an eval case under `judge/misses/`.

**Cite a rule as the owner's only with his words.** A record that calls a rule the owner's quotes his timed words. Any other rule, the root's own included, is cited as "root rule". `drive-watchdog` checks each such cite against the drive's `owner-prompts.jsonl` captures.

## Run `judge.py` and `judge-eval.py`

**All small-model judge calls go through `scripts/long_running/judge.py`.** Callers with multiple questions of the same shape in a single pass, such as the stall watch, pass them in one `batch` call on the command line. That `batch` call calls Sonnet once, whatever `LONG_RUNNING_JUDGE` says.

Core `judge.py` behaviors:
- Verbatim input: Every judge reads the owner's verbatim words and the question those words answered, never the register's summaries.
- Disagreement stays `unsure`, which never warns.
- Fail-open on errors: A failed model call returns nothing, preserving the caller's non-judge behavior.
- Logging and digests: Each call appends one JSON line to `judge/calls.jsonl` in the drive's state folder. `drive.py digest` prints each judge kind's verdicts, errors, and latency over the last 24 hours.

**`LONG_RUNNING_JUDGE` selects the judge backend.**

| `LONG_RUNNING_JUDGE` value | Behavior |
| --- | --- |
| `jev+sonnet` (default) | Asks Jev first and returns a nonflagged verdict without calling Sonnet. A flagged Jev verdict gets one confirmation from Sonnet at low effort with Claude Code's system prompt replaced. |
| `sonnet+jev` | Sonnet answers first, and Jev settles a first answer of `unsure`. |
| `sonnet` | Uses Sonnet only. |
| `jev` | Uses Jev only. |

**`judge-eval.py <fixtures>` benchmarks backends offline.** It scores every backend against fixture files stored in the drive's state folder, which stays local because the fixtures contain the owner's words. Because `judge-eval.py` makes live model calls, operators or lanes run it by hand before shipping a judge or changing a prompt, and never run it in CI.

## Ship-time and turn-time judges

**`scripts/long_running/judge.py` powers warning judges at ship time, spawn time, and root reply time.** Each judge runs alongside the deterministic check it fronts or replaces:

| Judge / Hook | When it runs | What it checks |
| --- | --- | --- |
| `ruling_misread` | PR ship | Judges the tests a PR ships the same way `judge_watch.py` judges contracts, checking whether they misread the owner's verbatim words on the register rows the PR cites or on the two rows `rulings.py match` finds. |
| `refusal_kinds_need_owner_answer` | PR ship | Reads the refusal kinds, retries, clocks, and budgets added by a PR and asks whether the owner's words or the plan name them. |
| `pr_bodies_name_what_they_defer` | PR open or edit | Asks whether the PR body names the fix its stand-ins put off and why, and states in plain words the rulings the diff builds. |
| Brief `Scale:` judge | Lane spawn hook and orca desk runner | Requires a `Scale:` line in every lane brief and asks whether it names the `N` that the brief's code paths run at; emits one `SCALE-MISSED` from the orca desk runner on failure. |
| Incident defect admission judge | Root reply during an open incident | When a root reply admits a defect during an open incident, asks whether the root started the fix on the same turn. |
| Read-claim judge | After a code read or review | Asks whether a claim about code or a review states more than the preceding read showed. |
| Release cancel / rollback judge | Release cancel or rollback | Alongside the block that refuses an unordered rollback, checks a cancel or rollback citing an owner answer to verify that the answer orders that exact undo. |

## Enforce system shape tests (`SHAPE-UNTESTED`)

**Every ruling about system shape requires a CI test in the same turn as the build.** System shape rulings include invariants such as one process, one primary and one replica, everything acting as a client, or `N` items sharing one resource. The test must fail against the pre-fix code.

The command `rulings.py shapes` lists all in-force shape rows, and `rulings.py shape <key> --test <repo>#<pr> <path>::<test>` records the test that covers shape row `<key>`. At ship time, `check-pr` warns `SHAPE-UNTESTED` on any PR citing a shape row until the PR adds a test naming that row or `rulings.py shape` records one.

Judge verification of shape tests ensures that naming the row in a test is not enough. The drive's judge reads the test alongside the owner's verbatim words. `check-pr` emits `SHAPE-UNTESTED` with the judge's sentence when the judge determines that the test asserts a weaker or different invariant that passes while the owner's invariant fails. For stall watch escalation, the stall watch posts to `root` every shape row that remains untested 30 minutes after it was written.
