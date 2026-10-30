# The long-running tooling stack, rebuilt on small-model judgment

## Why

On Oct 30 at 6:24 AM, the owner wrote: "we shouldhave a non-matchanical soltuon to this too, we need to evolve our tooling to become more agentic using small sonnet/haiku agetns etc, do a sweep/lane spiking on this, to avoid bitter lesson and going full retard".

At 6:35 AM on Oct 30, he added: "use this as another exmapel to shift your toolign to be more agentic so you stop going full retard".

At 6:53 AM on Oct 30, picking option 1 (one judge at every check), he wrote: "1 but this is lazy, go deep and make our entire tooling stack for long-running agentic as a follow-up".

These follow his standing rules and earlier rulings:
- Oct 23 at 3:11 PM (`rule/3f10b16`): "the only haed mechanical rule being that i aporoce each PR rhat gere merged. also slim up a lane to make all our hooks tooling etc less mechsnjcal more amenable to uoir jdgemebt, do a theorouhh clean up but do not betray thay trist."
- Oct 23 at 10:33 AM (`rule/4236611`): "delete all the overcomcisye slogiv use simole heiristics, fsil olen, make things warnings instead if blocks, etc".
- Standing rule 14: "Fail open".
- Standing rule 15: "lower teh erffort level, and in gernal just make ti all faster, get rid of all teh crazy refuters etc".

On Oct 29, two of his rulings were misread at every step while every word-matching check passed them:
- "One upload process per Mac" became "one process per destination and key" in the card option he tapped, the root's relay of his pick, the lane's contract, and the test that shipped (it asserts two processes for two keys).
- "Background work only when everything is idle" was applied to attachment copies, dream jobs, index posts and memory captures, none of which hold a lock a request needs. He asked about copies at 2:43 and 2:46 AM and said it again at 6:28 AM.

The spike (lane `agentic-tooling-spike-1`, prototype `judge.py`, PR `yasyf/cc-skills#1219` open) showed:
- One bounded question to Sonnet at low effort caught case 1 at the card, relay, contract and test, and cleared the corrected contract.
- Case 2 was caught at every step only once the judge had his 2:46 AM questions and was told to apply the reason behind a correction to items he did not name. With only his 5:41 PM sentence, it read him as literally as the lanes did.
- It raised a false flag when his words were a bare button label and the judge never saw the card's question.
- Haiku agreed on both cases but raised more false flags, so every Haiku flag needs Sonnet anyway. Sonnet alone is simpler.
- The register's own summaries carried both misreadings, so no judge may read a summary as his words.
- A judge call measured 13 to 40 s, 20 s on average. Prompts were 2,600 to 14,600 characters, 5,000 on average.
- Through the `claude -p` command line a call costs 8 cents on Sonnet, because it sends about 19,000 tokens of Claude Code's own prompt. With that prompt replaced, a call carries only its 700 to 3,700 tokens. Scaling the measured call, that is about 0.5 to 1.5 cents. The eval run measures it before anything ships.

## What the stack is today

- The long-running pack (`yasyf/cc-skills`, `plugins/long-running`) has 32 `capt-hook` hook files with about 70 handlers, and 41 scripts. The landing runner, `desk-runner.py`, is 8,848 lines and posts about 95 record kinds.
- `cc-inbox` (`cci`) is the drive's record bus. Every refusal it makes checks input, signatures or state.
- `cc-present` is the owner's board. It has no text checks of its own. The board checks live in the pack (`board_text_gate.py`, `settled_questions.py`, `board_rows.py`).
- Models already in the stack: `stall_watch.py` asks Sonnet low about restacks and silences and falls back to its mechanical answer on error. `ruling_judge.py` asks a small model which owner answer an action bears on. `finder.py`, `fidelity.py` and `rules-review.py` each run a codex `gpt-6.1-sol` review per PR head. `pr-prose.py` drafts PR text.
- Volume in the last 24 hours: about 31,000 records, 168 contracts, about 520 root relays citing his words, about 90 cards, about 100 lane briefs, about 280 monorepo PRs, about 335 owner answers, and about 1,700 blocker records.

## The design

### One judge

- One library, `skills/long-running/scripts/long_running/judge.py`, is lifted out of `#1219`'s `ruling_misread.py`. Hooks call it through `capt-hook`'s `evt.llm`. Scripts call the same function directly over `spawnllm`, the library `capt-hook` already uses for `evt.llm`. No second judge is built anywhere.
- One call shape: a bounded prompt, a JSON answer, Sonnet at low effort with Claude Code's own system prompt replaced, at most two candidate rows per artifact, and one plain sentence out.
- Each judge kind is one question plus its answer fields. The answer always has a verdict (yes, no or unsure), the phrase from the artifact, the phrase or log line it rests on, and one sentence the writer or owner reads without the drive's vocabulary.
- Inputs for any question about his words: his words verbatim from the register row and its repeats, oldest first, what each one answered (meaning the card's question and options for a tap, or the status line he replied to), the sibling rulings the artifact cites, and the artifact. The register's implications and summaries never go in. Where a grader needs the root's reading, it goes in labeled as the root's reading.
- A yes is asked a second time. When the two answers differ, the verdict is unsure, and unsure never warns. This handles the wobble the spike saw on one credential contract, which flipped between yes and no across three runs.
- Every judge warns and never blocks. A judge that fronts an alarm only silences a false alarm, so on a model error the alarm posts as it does today. A judge that checks a writer's text adds a warning, so on a model error nothing is added. That is standing rule 14 in both directions.
- Regexes and `rulings.py match` stay only to pick which rows or records a judge reads. Every regex verdict a judge replaces is deleted in the same PR.
- Metrics: the library appends one line per call to the drive's state folder (`judge/calls.jsonl`: kind, verdict, seconds, tokens, error). `drive.py digest` prints one count per kind and verdict, the error count, and the median and slowest latency per kind. There is no metrics service on this Mac, so the digest is where they are read.

### Re-check when he adds words

When `rulings.py classify` records new owner words on a row, the judge re-checks the open work that cites that row: unaccepted contracts, open PR heads, open cards, and briefs of live lanes, the ten newest at most. The prompt tells it to apply the reason behind his correction, not only the item he named. Each flag goes to the writer and the root as one record. This is what would have caught case 2 at 2:46 AM, three hours before he had to say it again.

### Where each judge runs

- Before the post, as a hook warning, where the writer is still deciding: card text, a lane brief's `Scale:` line, a PR body at ship, the refusals and waits a PR adds, owner-facing replies.
- After the post, from the bus, where nobody should wait: contracts, root relays, tests at a PR head, the fidelity grade's input, tap carry, red routing, the landing runner's "admitted but skipped" and "lane gone" alarms.
- `#1219` runs its judge inside the poster's hook, so the poster waits 10 to 25 s on each contract, card and cited relay. In this design the contract and relay checks move after the post. The card check stays before the card reaches his board.

### A judge never weakens a trust-bearing block

His one hard rule stays mechanical: he approves each PR that merges. Rule `3f10b16`'s third line keeps these as blocks: merge approval and review, revert and rollback, hook edits, load tests, paging monitors, credentials, and Slack writes. A judge may add a warning beside one of them or route work toward his tap. It never clears one.

## Every gate, and what it becomes

Legend: Judge = a small-model question replaces the regex verdict. Front = the mechanical check still decides, and a judge silences its false alarms or adds a warning. Keep = stays mechanical.

### His words in the work

| Gate | Where | Today | Becomes | Question |
|---|---|---|---|---|
| Card gate | `board_text_gate.py`, `settled_questions.py` | Regexes on "recommended", "whether", mechanism words; word overlap with settled decisions | Judge, before the card goes up | Does this card ask what he already ruled, or offer an option that reads him narrower or more literally than his words? |
| Contract grade | `rulings.py` readback, accepted, narrowed; `narrowed_contracts.py` | NARROWING regex on "per", "only", "except" | Judge, after the post | Does this contract carry his words, or read them narrower or too literally? |
| Root relay | `root_decide_gate.py` | Mechanism words without a cite | Judge, after the post | Does this relay carry his pick in full, and add nothing he and the plan never named? |
| Shape tests | `rulings.py` shapes, `check-pr` SHAPE-UNTESTED | Count-word regex; the test must name the row | Judge at the PR head | Does this test assert his invariant, or a different one that passes while his fails? |
| Register classify | `rulings.py` classify, implications | Agent-written implications | Judge on each new implication | Does this line say what his sentence says, and does his sentence require it or only permit it? |
| Fidelity | `fidelity.py`, codex per head | A model, fed register summaries | Keep on codex; change its input | Same grader, fed his verbatim words and the cards they answered; the root's reading is labeled as such |
| Rules review | `rules-review.py`, codex per head | A second codex review against the same rulings | Fold into fidelity | One codex grade per head against his words, not two |
| Ruling match | `ruling_judge.py` | Small model: which answer bears on this call | Judge | Extend "bears on" to "carries in full" |
| Open owner question | `register_gates.py` `owner_question_gate` | Warns when a brief touches an open question without quoting it | Keep | It reads state: a question is open or it is not |

### Text the owner reads

| Gate | Where | Today | Becomes | Question or reason |
|---|---|---|---|---|
| Plain words | `owner_facing.py` plain-words handlers, `board_text_gate.py` | ID, codename and capital-letter regexes | Judge; the id regex stays as candidate selector | Would he follow this without the drive's vocabulary, and does it read as sentences, not fields? |
| Draft in a question | `owner_facing.py` | Regex | Judge folded into plain words | Same question |
| Code claims | `root_replies.py` `code_claims_follow_a_read` | A read of the file in the same turn | Front | The read check stays; a judge warns when the claim says more than the read showed |
| Narrated defect | `root_replies.py` `narrated_defects_ship_their_fix` | Admission phrases | Judge | Does this reply admit a defect, and did the same turn start its fix? |
| Pacific time | `owner_facing.py`, `root_replies.py` time labels | Time arithmetic | Keep | A time is right or wrong; a program reads it exactly |
| Numbers cite their query | `root_replies.py` | A number with no record beside it | Keep | Format, read exactly |
| Waits on his tap | `root_replies.py` `tap_claims_match_the_board` | Board answers | Keep, block | Board state, and trust-bearing |

### What a PR adds

| Gate | Where | Today | Becomes | Question or reason |
|---|---|---|---|---|
| Refusals, retries, timers, waits | `refusal_kinds_need_owner_answer.py`; `check-pr` MECHANISM-BANNED | Name, retry and clock regexes; the idle waits passed as "not a timer" | Judge; the shape regex stays as candidate selector | Does this PR add a refusal, retry, timer or wait that his words and the plan do not name? |
| PR body | none | Nothing | Judge at ship | Does the body name the fix it puts off, and why? |
| Rulings cited and explained | `check-pr` RULINGS-UNCITED, RULINGS-PLAIN | Heading and id regexes | Judge, folded into the PR body question | Does the body name the rulings the diff builds, in plain words? |
| Removed names, removed tests, pinned test corpus | `check-pr` NAME-HIT, TEST-REMOVED, TESTS-CORPUS | Exact name, file and hash matches | Keep | A removed name or test is literal by nature; a hash is exact |
| PR body stays as opened | `pr_body_frozen.py` | Edit detection | Keep | State |

### The landing flow

| Gate | Where | Today | Becomes | Question or reason |
|---|---|---|---|---|
| Tap carry | `desk-runner` tap carry; `board_rows.py` `CARRY`, `REASK`, `REKEY` and `ONE_TAP` regexes | Phrases in the first sentence of a fidelity grade | Front. A patch-identical head still carries mechanically. The judge decides the other cases in the safe direction only: "changes behavior" puts a fresh row on his board at once; "no behavior change" goes to the root as a proposal, and only a root go carries it | Does the new head change any behavior he tapped at the old head? |
| Red routing | red routing, `dev-red:<step>` holds | Step name and build state | Front | Is this red caused by this PR's diff, inherited from trunk, or flaky? One sentence with the log line. On error it routes as today |
| Admitted but skipped | `desk-runner` ADMITTED-SKIPPED | No enqueue, merge or refusal record in ten minutes | Front | Given the head's records, is it admitted and unhandled? On error it posts as today. Six false alarms at 6:05 AM came from reading finder and fidelity refusals as skips |
| Lane gone | `desk-runner` LANE-GONE | Post cadence | Front | Is this lane dead, or waiting on something its last records name? |
| Stall watch | `stall_watch.py` | Sonnet low with a mechanical fallback | Keep; move onto the shared library | It is already the pattern the rest follows |
| The other landing records, about 85 kinds | `desk-runner.py` | Quota, merge mode, queue membership, launch and read failures, config errors, holds, forks | Keep | Each reports a fact a program reads exactly. A model adds cost and a way to be wrong and judges nothing |
| Lanes never enqueue; only the root steers the queue; hand enqueue needs his tap; no admin merge; incident fixes merge by plain squash | `lane_landing.py` | Blocks | Keep, block | Merge approval, trust-bearing |
| "Green except" reads as red | `lane_landing.py` `green_except_is_red` | Phrase, block | Keep, block | Merge review, trust-bearing |

### Spawns and briefs

| Gate | Where | Today | Becomes | Question or reason |
|---|---|---|---|---|
| Scale line | `register_injection.py`, `orca` desk refusal | A `Scale:` line exists | Judge; the line's presence stays checked | Does the Scale line name the N the brief's paths actually run at? |
| Register slice line | `register_injection.py` | Line present and fresh | Keep | State |
| Incident lanes on sol, launch load, desk liveness | `owner_facing.py`, `desk-launch.py`, `desk_liveness.py` | Route and load checks | Keep | Routing and machine state |

### Process and protection

| Gate | Where | Becomes | Reason |
|---|---|---|---|
| Revert and rollback need his order | `incident_commands.py` | Keep, block; a judge warns when the cited answer does not order this undo | Trust-bearing; the judge may only add a warning |
| Hook edits, register writes, labels only `rulings.py` writes | `register_gates.py` | Keep, block | Trust-bearing and state |
| Slack writes, credentials | `cc-slack` permission hook | Keep, block | Trust-bearing |
| Task list drift, lane rotation, compaction, worktree notice, PR ledger | `task_list.py`, `lane_rotation.py`, `compaction_handoff.py`, `worktree_announce.py`, `pr_ledger.py` | Keep | They track state |
| Judgment reminder | `judgment_reminder.py` | Keep | It is a reminder, not a check |
| Buildkite and GitHub poll pacing | `buildkite_budget.py` | Keep | Quota |
| `cc-inbox` refusals | `cc-inbox` | Keep | Input, signature and state checks |

Count: 18 gates get a judge (11 replace a regex verdict, 6 front a mechanical alarm or block, 1 is new: the PR body). The rest stay mechanical because they track state, quota, an exact format, or a trust-bearing block.

## Fixtures and the eval

- `scripts/judge-eval.py` runs every judge kind over fixture folders and prints one pass or fail line per case. The lane runs it before each judge ships and after each prompt change. It never runs in CI, because it calls a model (standing rules 11 and 12).
- The three cases from the spike card are the first fixtures.
  - Case 1, one upload process per Mac: must flag the card option, the root relay, the contract and the test. It must clear the corrected contract.
  - Case 2, background work only when idle: with words up to 5:41 PM the honest result is a miss. With words up to 2:46 AM it must flag the job list's copy-up row, the copy-up PR summary, the dreams and index-post tests, and the dreams card.
  - Case 3, false flags: must clear the progress-bar contract once given the card his "Delete: a moving bar with no total, for good" answered. It must clear two other contracts. The credential contract that flips is reported unsure, never yes.
- Each judge kind gets fixtures from its own failure in the gate tables above: the six false ADMITTED-SKIPPED alarms at 6:05 AM, the inherited reds routed to PR lanes, the idle waits that passed as "not a timer", and the test that named the row and asserted two processes.
- The set grows: every flag he rules on and every correction of his the judge missed becomes a fixture.

## How a wrong judgment is caught

- Before it ships: the eval over the fixtures.
- A false flag: it costs the writer one sentence back. If the writer disputes it, he gets one card with his words and both readings, with an Explain more button. His answer becomes a fixture.
- A miss: when he corrects a reading, the re-check reports whether the judge had flagged that work before. A miss becomes a fixture and the prompt is fixed against it.
- A wobble: a yes is asked twice, and a split is unsure, which never warns.
- A drift: the digest shows the yes rate per kind. A kind whose yes rate jumps is read by the root that hour.
- An alarm judge that wrongly silences: each silenced alarm is logged with its sentence, and `stall_watch` still reports anything stalled past its clock.

## Build order

1. `#1219`, open: the misread hook (contracts, relays, cards, test changes).
2. The shared library and the eval: lift `#1219`'s call into `long_running/judge.py`, replace Claude Code's system prompt, add the double-ask on yes, the calls log, the digest lines, and `judge-eval.py` with the three cases.
3. Complete inputs: the card a tap answered, and the re-check when he adds words.
4. His words in the work: contracts, relays, shape tests, cards and settled questions, register implications, ruling match. Delete the regex verdicts.
5. Fidelity reads his words and the cards. `rules-review` folds into it.
6. Text and PR judges: plain words, PR body, refusals and waits, `Scale:` line, narrated defect, code claims.
7. Landing judges: red routing, admitted but skipped, lane gone, tap carry.
8. `stall_watch` onto the shared library.

## Cost per drive-hour

At Oct 29 volume, per hour: about 45 artifacts after the post (contracts, relays, PR heads), about 15 re-checks, about 15 before-post checks (cards, briefs, PR bodies, owner replies), up to about 70 alarm candidates (the blocker rate, once per PR head and step), and about 10 second asks on a yes. That is about 150 Sonnet calls an hour at most.

| | Calls an hour | Cents a call | Per drive-hour |
|---|---|---|---|
| Option 1 | about 150 | 0.5 to 1.5 | $0.75 to $2.25 |
| Option 2 | about 140 | 0.5 to 1.5 | $0.70 to $2.10 |
| Option 3 | about 100 agent runs, 5 to 10 tool turns each | 10 to 25 a run | $10 to $25 |

The per-call cent figure is scaled from the spike's measured 8-cent call and is measured by the eval before anything ships. The codex fidelity grade per head is unchanged by every option. Options 1 and 2 remove the second codex review per head that `rules-review` runs today.

## Options

### Option 1: one judge library, placed where the writer acts (recommended)

- What it is: the 18 judges above, one library, Sonnet low. Judges on text the writer is still deciding (cards, briefs, PR bodies, owner replies) run in the hook before the post. Judges on posted work (contracts, relays, tests, tap carry, red routing, landing alarms) run after the post from the bus, so nobody waits. Every regex verdict they replace is deleted.
- Becomes a judgment: the 18 gates listed.
- Stays mechanical: about 85 landing record kinds, every `cc-inbox` refusal, time labels, removed names and tests, task and lane state, and every trust-bearing block. Why: they read state, quota or an exact format, or they protect his one hard rule and the trust-bearing list.
- Cost: about $0.75 to $2.25 per drive-hour.
- Wrong judgment caught by: the fixture eval before ship, a disputed flag goes to him as one card with both readings, a miss he corrects becomes a fixture, a yes is asked twice and a split stays silent, and alarms fail toward posting.
- Speed: a writer waits 13 to 40 s only on a card, brief, PR body or owner reply. Posted work is judged within about a minute.

### Option 2: the same judges, all after the post

- What it is: the same 18 judges, all run from the bus after the post. Hooks keep only state checks and the trust-bearing blocks.
- Becomes a judgment: the same 18.
- Stays mechanical: the same.
- Cost: about $0.70 to $2.10 per drive-hour.
- Wrong judgment caught by: the same, but a flag arrives as a record about a minute after the post. A card judged after it reaches his board can be on his screen before the warning reaches its writer, so a misread card can still reach him.
- Speed: no writer ever waits.

### Option 3: three small agents with tools instead of per-gate judges

- What it is: three Sonnet low agents with read-only tools (reading records, PRs, the register and the board): one checks every artifact against his words, one watches the landing flow, one reads everything he sees. Each runs per event. The per-gate code shrinks the most. The bounded questions go away.
- Becomes a judgment: everything the three agents choose to look at, including the 18 gates.
- Stays mechanical: the trust-bearing blocks and quota pacing.
- Cost: about $10 to $25 per drive-hour.
- Wrong judgment caught by: an agent's misses are silent, because nobody can list what it did not check. It can be graded against the same fixtures, but each run reads different records, so a pass on the fixtures says less. It can misread his words itself as its context fills.
- Speed: one to several minutes per event.

### Recommendation

Option 1. It keeps the bounded question the spike proved on both cases, it catches a misread card before it reaches him, and it costs about two dollars a drive-hour. Option 2 is the same with nobody waiting, at the price of a misread card reaching his board first. Option 3 is the fullest bet on models doing the work, but its misses cannot be counted and it costs about ten times more.
