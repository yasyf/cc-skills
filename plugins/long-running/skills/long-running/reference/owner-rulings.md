# Owner rulings, standing rules, and judges

This file holds the mechanics for settled questions (`R19`), owner-ruling gates (`R19a`), extrapolating rulings (`R19b`) and standing rules (`I6`). `SKILL.md` carries the one-line summaries as `R17`, `R18`, `R19`, `R19a` and `R19b`.

## Contents

- [Filter questions against authorities (R19)](#filter-questions-against-authorities-r19)
- [Price check, gate and grader options](#price-check-gate-and-grader-options)
- [Run the `settled_questions` hook](#run-the-settled_questions-hook)
- [Record standing rules and board taps (I6)](#record-standing-rules-and-board-taps-i6)
- [Satisfy owner-ruling gates (R19a)](#satisfy-owner-ruling-gates-r19a)
- [Extrapolate and sweep every ruling (R19b)](#extrapolate-and-sweep-every-ruling-r19b)
- [Read the owner's words before citing them](#read-the-owners-words-before-citing-them)
- [Audit contracts and relays with `judge_watch.py`](#audit-contracts-and-relays-with-judge_watchpy)
- [Run `judge.py` and `judge-eval.py`](#run-judgepy-and-judge-evalpy)
- [Ship-time and turn-time judges](#ship-time-and-turn-time-judges)
- [Enforce system shape tests (`SHAPE-UNTESTED`)](#enforce-system-shape-tests-shape-untested)

## Filter questions against authorities (R19)

Before `cc-present start --doc`, `push` or `update-block`, the coordinator checks every question against three authorities, in order:

| Order | Authority | What to check |
| --- | --- | --- |
| 1 | Owner plans | The owner's plan and every plan it names as source of truth, including each plan's `Decisions` section and the section the question touches. |
| 2 | Durable and drive answers | `ccn answer list --label scope:durable` plus the drive's answer labels. |
| 3 | Feedback memories | The project's feedback memories. |

- **An authority's answer is applied, not asked.** The coordinator writes the ruling to the lanes (`R18` in `SKILL.md`) and logs it. The question never reaches the owner. A "confirm or override" card for an item the plan settles counts as asking and is forbidden.
- **A lane's owner-question file is raw input.** The coordinator never forwards it verbatim. Only questions no authority settles go to the owner, and each card names which authorities were checked.
- **Absolute instructions stay absolute.** "Nothing is dropped" and "every side-feature carries" stay unqualified in every brief, ruling and memory. Never append "unless the owner explicitly drops it" or "or an explicit owner drop": an exit clause turns the ruling into a decision surface that audit lanes use to manufacture questions.
- **Audit and parity briefs enforce the same filter.** Each tells the lane to grade any row the owner's rulings cover against carry or the plan mechanism that carries it, and never to queue that row as an owner question.
- **A contradiction with an owner ruling or plan is a defect to fix forward.** When a prior coordinator decision, a landed PR or a refining plan line conflicts with an owner ruling or the owner's plan, the plan wins. The coordinator spawns a fix lane that changes the code to match the plan instead of asking the owner. A revert runs only when the owner's own words name a revert.

## Price check, gate and grader options

A question about a check, gate or grader always includes a deletion option and a cost table.

- The question names every existing mechanism that grades the same signal.
- The options include `"delete it and let <that mechanism> own the signal"`, and each option lists its added lines and added graders as its cost.
- Mark `Recommended` on the option that adds the least new machinery, unless the question states what that option fails to cover.
- When a lane was asked to research the options, wait for its reply before writing them, and cite the research verdict they rest on.

## Run the `settled_questions` hook

The `settled_questions` hook blocks asking the owner what the owner already ruled. When the coordinator or a lane makes an asking call, the hook sends each question to a judge before the call proceeds. The hook runs in lanes as well as the coordinator, so it checks the board refresher lane's pushes.

Covered asking calls:

- `AskUserQuestion`
- each card of `cc-present start --doc`, `push` or `update-block`
- a Slack status card with options
- a coordinator `DECIDE` sentence that orders a card

The judge reads:

- the owner's verbatim words on the register rows the question cites or `rulings.py match` finds
- all other in-force register rows
- the plan's `Decisions` lines
- the owner's prior picks and the feedback memories that share words with the question, including the question each pick answered and whether a later answer replaced it

It warns when a card re-asks a settled question or offers an option narrower or more literal than the owner's words. A found violation of an existing ruling is applied directly and never put back to the owner as a card or pick. A failed judge call adds nothing, and re-issuing the same card passes on retry, so a false positive costs one retry.

**A card that asks to end or restart a process names it, and his yes on it is enough.** The card's own words carry `pid <number>` or the script's file name, and the lane that ends it, such as "meta-desk restarts pid 74452 (board_watch.py)". The board stamps those words on its own log of his tap, and board-watch's answer row lets the process guard find it. The guard then lifts one kill of that process for that lane or its successor, within the hour of the tap, so he never types a line for something he already tapped. A row or an edit to the card lifts nothing, and live sessions, Orca terminals and an agent's ancestors stay protected whatever a card says. `lr ask-owner` and `lr board cards check`, which every `cc-present` draw reads through the `answer_search_first` hook, print a `WARNING` for a card that asks to end a process and names none.

**A card an owner ccn answer settles is never drawn and never stays.** `lr ask-owner`, `lr board cards check CARD_FILE|-` and every `cc-present` push in a drive refuse a card, through the `answer_search_first` hook, when:

- a card already on the board asks the same question text;
- the `confirm` judge reads it as asking the owner to grade a reading of the owner's own words, in any wording; or
- the `decided` judge reads one of the owner's ccn answers sharing its subject as deciding everything it asks.

The refusal names that answer by its question, in plain words, and its id. The same draws print a `WARNING` saying what is missing, and still draw, when the `set-options` judge reads an option as acting on a set whose members the card does not each name with a PR link and what it does for a user, or reads the recommended option as needing code while neither its `build` nor the card's words name a branch, or reads the card as having him choose between architecture designs with no architecture doc (arch/v1) drawing them side by side. A picture, a mermaid block or a link to a page is no such diagram.

- A card's background or Explain more embeds the write-up's section in the card, using cc-present's `display.designdoc` block or the architecture diagram component where one exists. A link may only sit under that text. The same check refuses a card that is only a URL or "see <link>", on `lr reply` as well.
- A lane that reads the owner's words acts on its reading and says what it did.
- Every `lr board cards sweep` judges each open card against the answers newer than its last check. Only his tap on a card, his numbered reply in its thread, or a relay's `ccn answer add` labelled `from:owner` and `card:<id>` with his words closes that card as answered, a tap with no judge once the board's 30-minute hold on a card he touched has passed, and a typed answer when the judge reads it as settling the question. A question in the thread, typed words with no number, a board write-in with no number, an answer to another question, and a lane's `done`, `landed` or `report` record never close it: the card stays up and the lane that drew it, else the sweeping lane, gets a `report` naming what was read, and that lane withdraws it with `lr ask-owner --withdraw` or says why it stays. A draw his answer settles is still refused, and the refusal names that answer. A card's own end event and a lane's deliberate `lr withdraw` still take it down, with their own reason, and so does every PR the card shows being merged or closed, whatever its end: the sweep withdraws it naming each PR's state, and its Slack card loses its buttons; a time end alone never takes down a question he has not answered, and a question's default end is `answered`, so no lane invents a date. A hand `cc-present remove-block` whose `--reason` says he answered is refused when none of those three stands behind the card.
- Only a yes, which the judge confirms with a second call, refuses or removes. An unsure or split verdict keeps the card. A failed call keeps it, prints a `WARNING` and judges it again next pass.
- The supervisor runs this sweep every five minutes and logs each line. A card the owner touched in the last two minutes, or that the board still holds for the owner, waits for a later sweep.

## Record standing rules and board taps (I6)

**Record every standing rule the turn it arrives.** When an owner message states a standing rule with phrases like `"from now on"`, `"always"`, `"never"`, `"I told you"`, `"the plan is"`, `"for the remainder"` or `"every landing"`, the coordinator records it that turn in two places:

1. A cc-notes `answer_add` labeled `scope:durable`.
2. A line in the plan's `Decisions` section.

If a turn ends without an `answer_add` or `answer_edit`, the `root_context` hook queues `owner standing rule not recorded: answer_add it (scope:durable) + a plan Decisions line` for the next turn.

**I6. A standing rule has its own durable answer, and it is never done.** After recording the `scope:durable` answer, the coordinator posts a dedicated `go` record to the target desk:

```
cci post --drive <drive> --lane root --kind go --to <desk> --ccn <answer id> --topic standing --text "<rule>"
```

- **Separate from one-offs.** A standing rule never shares a cci record or cc-notes answer id with a one-off instruction. `"Deploy everything now"` and `"deploy every landing from now on"` need two separate records.
- **Never closed.** No record ever marks a standing rule `done`, `complete` or `closed`.
- **Handed to a lane as a deliverable.** The coordinator still creates the durable answer and addressed cci record first. The lane's task cites both the rule id (`#<seq>`) and the answer id, and completing the task never retires the rule.
- **Superseding.** Supersede the rule's cc-notes answer with a later answer, then post the correction below, where `<seq>` is the cci seq of the standing record being replaced.
- **Listing.** Standing rule ids take the form `#<seq>`. Every desk brief and desk summary lists live standing rules individually by `#<seq>` with their cc-notes answer ids, alongside the plan's `Decisions`, and never collapses them into a range.

```
cci post --drive <drive> --lane root --kind correction --to <desk> --re <seq> --ccn <later answer id> --topic standing --text "<replacement rule>"
```

**A register row carries the owner's whole quote.** Graders and judges read a row's `words`, never the answer's implications, so a row cut short reads as a narrower ruling than the owner gave.

- `rulings.py classify --answer` refuses words that stop inside a quote of the answer, or inside a run of quotes joined by `and`, with `CUT-WORDS`, and prints the whole of it.
- A row already cut grows with `rulings.py amend KEY (--answer ID | --capture DIGEST) --words TEXT`. `TEXT` must be a verbatim span of that answer or owner capture and replaces every said item of the row it holds. With none, it folds on as a further capture that does not count as saying it again.
- A later owner answer joins any row, `exception/` rows included, with `rulings.py amend KEY --answer ID`, repeatable. It takes no tap time and records the link on the row's `amends`. It refuses with `NOT-OWNER` an answer labeled neither `from:owner` nor `slack:owner-words`.
- `authorize` stays the verb for a tap that lets a merge touch a reserved row.

**Every classified ruling is an answer.** When `rulings.py classify` writes a register row for an owner capture that has no answer, such as words said in Slack or a prompt that lived only in cci and the rulings files, it records the words as a ccn answer and puts its id on the row. The answer's title is the words' first sentence, cut at a word boundary to ccn's 256-byte title cap and ended with an ellipsis, and its body holds the words whole. The answer is labeled `from:owner`, `scope:durable`, `program:<program>`, the capture's `message:` and `proof:` labels and the owner's topics. A capture the standing-rule hook already recorded keeps that answer. Board and Slack taps, which board-watch records, and design and exception rows, which hold the plan's words, get none.

**An answer carries the topics it names.** The program's answer vocabulary is a ccn ledger labeled `answer-topics` with one row per topic: the phrasings that name it and the repo directories it covers.

- Each answer board-watch or the `root_context` capture writes gets `topic:<slug>` and those directories for every topic its question or words name, so `ccn answer search <slug>` finds it under any wording.
- `rulings.py topic <slug> --words <regex> --dirs <dir>` adds or changes a topic and labels every answer on file that it names, once. Run it again for each new topic.
- A brief's slice searches the topics its lane names and the topics whose directories its paths touch.
- Fidelity's rows carry the owner's answers on every topic a PR's text names or its files touch, keyed `answer/<id>`. A head that breaks one grades `missing` with "departs from ruling <id>".

**A ruling that changes the wording of a plan title also corrects the update.** The owner update reads plan wording from the register, so the plan text a later ruling supersedes never reaches the owner. The register lane classifies the ruling's capture as usual, then sets the phrase on that row:

```
ccn -R <checkout> ledger row set <register ledger> --key <row> --field plan_was='<the plan's words>' --field plan_now='<the ruling's words>'
```

The replacement stops when the row stops being in force. Keep `plan_now` short enough for a heading, since it renders in place of `plan_was`.

**A tap on an ask card is an owner answer.** When the owner taps an option on a Slack card or a board card, including a tap approving a Slack lane's draft, that tap is the owner's answer and authorization for that question. The coordinator records it as one `from:owner` cc-notes answer whose body names the card's permalink and the tapped label.

## Satisfy owner-ruling gates (R19a)

Some gates accept only an answer the owner recorded, such as `pr-reviews accept --owner-ruling` taking a cc-notes answer id. The coordinator satisfies any such gate on the same turn:

- When the plan or a board pick already carries the decision, record the plan's decision as the ruling in cc-notes and accept on the owner's behalf, citing the plan line or board pick id in the accept reason.
- When nothing settles it, put the question card on the board that turn.

The coordinator never ends a turn waiting for the owner to say "ask" and never leaves PRs parked behind the gate.

## Extrapolate and sweep every ruling (R19b)

**A ruling binds everything it implies across the whole drive.** On the turn an owner ruling arrives, the coordinator:

1. Records the owner's words verbatim.
2. Writes out the ruling's implications across sibling mechanisms, both directions of any relationship, and every artifact, lane, ledger row, budget line, test row, board card and brief that carries the old assumption.
3. Confirms those implications with the owner that same turn: in chat when there are a few, on one board card when there are many.
4. Applies the ruling to every live artifact and lane at once, never to new work only (`R17` in `SKILL.md`).

**A ruling travels by its verbatim words and its implications.** Briefs, handoffs, progress docs and compaction narratives carry both. An answer id or a short gist alone drops the ruling.

**A ruling or design that removes code owes the deletion a task.** When the owner approves a design, each row of its deletion table, whatever the design calls it, becomes a register row with a `removes` implication. The same holds for any ruling that removes a path or symbol. The coordinator anchors what goes with `rulings.py anchor <key> --symbols <name>...`, or `--paths <path>...` when whole files or directories go.

- Each anchor needs an open cc-notes task labeled `owed-deletion`, one per deletion, in the drive's checkout. The assignee is the owner lane; none means the coordinator still has to assign it.
- Its `--path` and `--dir` anchors name what goes, a `symbol:<name>` label names each symbol in them, and a `due:YYYY-MM-DD` label names the date. The body's first sentence names the decision.
- An `exception` row anchored to the same target records why nothing is owed instead.
- An anchor that marks the surface a row guards rather than code it deletes goes in the row's `guards` field with `rulings.py anchor KEY --guards ...`. The check skips it, while `check-pr` and sweeps still read the row's paths and symbols.
- A target no longer on trunk at the row's anchored paths owes nothing.
- The approver runs `lr deletions check` the turn the design or ruling is approved. It exits 1 naming each `removes` target with neither task nor exception, once per target, with every row that removes it.
- The supervisor runs the same check hourly and posts one blocker digest to the coordinator listing them, again only when the set changes.
- A task past its `due:` date with its code still on trunk shows on the owner update under "Owed deletions past due" and draws one blocker to its owner lane, or to the coordinator when that lane is not live.

**Sweep every outbound artifact against every ruling's broadest reading.** Before presenting a board, brief, handoff or plan, the coordinator checks the entire text against all rulings. A sentence that keeps, budgets, tests or asks about something a ruling removed is a defect, even when it cites no ruling. The coordinator drafts every board, card or reply from the owner's rulings, not from code on the base branch. A card that explains a term from base-branch code where a ruling covers it fails the sweep.

**A repeated owner ruling is a defect report.** When the owner repeats a ruling, the coordinator finds every place an earlier narrow reading survived and fixes all of them that same turn, then reports to `owner-relay` what was missed and why. The supervisor's choices check asks root to name the lane doing a repeated order and what holds it, since a repeated order is often waiting behind unrelated work.

### A choice that is the owner's

A choice that is the owner's has one drawer, the lane that needs it, with options recommended first:

```
lr ask-owner --title '<question>?' --background '<what he needs to decide it>' --option 'Keep it :: what it does' --option 'Hold it :: what it does' --nothing-to-build '<why>'
```

- The lane draws its own card, on his main board and at the root of his Slack direct message, and owns it. The call waits for the board link on the Mac and prints the card's link, or exits 1 with what to change.
- Until a lane is on the verb, board-refresher alone draws a lane's `CARD:` ask, through `lr ask-owner --card <file> --for-ask <seq> --until <end>`, so the asking lane owns the card and that ask gets one card. A second card for the same ask is refused naming the first. A `CARD:` ask whose answers are architecture designs is not drawn there, since that card draws no diagram: board-refresher sends it to owner-relay with the lane's arch/v1 doc, as the next line says, and asks the lane for the doc when the ask has none.
- owner-relay draws only the cards of its own replies, and never a lane's `CARD:` ask. A lane's choice between architecture designs reaches it as an `ask` carrying the arch/v1 doc, which it sends as its own reply with the doc as `diagram`, since `lr reply` is the one card route that draws the diagram. board-refresher also redraws and withdraws stale cards.
- A missing background, a repeat of a card on his board, a card he already answered, only ways to live with a failure and no fix for its cause, and a loss with no plan beside it are all refused at the link.
- The owner's answer wakes that lane through `lr board wake` the moment board-watch records it. The wake reaches any lane that is not retired, and `lr board wake late` raises one that never answers. A retired lane whose process is still there is woken; one whose process is gone is relaunched by the supervisor from its brief, once, with the answer in the new lane's brief, and a second answer goes to that new lane. A pick no lane asked for goes to owner-relay.
- Any record of the lane naming the card within ten minutes is the acknowledgement. Past that, meta-desk and the communicator hear once and nothing blocks.
- A decide with options, a plan fork, an open item in a design doc and an owner-pick line are the same case.
- A lane does not park on a card with nothing built. It builds the recommended option as a branch, no PR, and the owner can overturn it, unless the pick deletes data, sends a message or needs the owner's hand. This branches-only rule covers design questions the owner has not ordered built.
- An order to build is built, merged and installed without waiting on a card about its later cuts.
- A correction card cannot reorder a settled owner order: fixes go on top of it, and the gap is a one-line risk.
- A tapped PR never returns to the board for a restack. A re-ask is only for a named behavior change, and its first line names what changed.
- A pick of a mechanism does not authorize reading a secret store. Put the key step to the owner on the first card instead of briefing a lane to read Keychain keys.
- The supervisor's choices check lists what misses this path: a `CARD` record open past an hour, a waiting card with no Slack copy past an hour, an owner item in a recent design doc's open list naming no card (a one-pager's `your pick` line records his answer, so it is never an open item; a doc reaches the desk again only when it names an item it has not named before), prose reading as the owner's choice, and a lane blocked on the owner's pick with nothing built. It sends each once, to `board-refresher` for a `CARD:` ask or to the lane for prose and parking, as warnings that never block. A read that fails says nothing.

### Before a card is drawn

- **The draw step reads the owner's words.** The board desk lists each answer or Slack line of the owner's whose question shares at least three words and half of the card's title, then each order of the owner's that removes something and shares a word pair with that title, newest first. The desk refuses the card with those lines, or redraws it naming why they do not reach it.
- It reads answers, the owner's Slack lines and each register row's words, never a row's summary, because a summary can carry a wrong merge of two rulings. A card it passes can still be wrong, and a read that fails prints nothing.
- **Every question on a card carries a write-in answer ("Other")** the owner can type. Use the standard choice block, whose chrome has it, or add an input block labeled Other; a block with no write-in is not used.
- **Each board card carries a mirror-to-Slack field, true by default.** A complex or multi-part card sets it false and stays on the board, and Slack gets at most a one-line pointer. A proof or scratch board always runs with mirror false and a dry run first, and never posts a doc into the owner's Slack.
- **The recommended option is checked against the object-hierarchy doc before the card is drawn.** A pick that would mint an object the doc already provides is refused, naming the doc's row.
- **Every card names where it comes from, in one line labeled `From:`.** That line gives the plan step and date, the owner's ruling id if one applies, and whose PR it ports when it mirrors a change from the old tree. A card with none of those is drawn saying `New, not in the plan.`
- **A line to the owner that names something breaking names the plan to fix it beside it, and a card option is never drawn with a loss and no fix.**
- **A plan gap is never settled by `DECIDE`.** A lane may meet a new column, table, route, field, state, timer or metric missing from both the plan and the owner rows. The coordinator puts a card on the owner's board, and the lane stops on that gap. Before relaying any lane report such as "a worker did X", the coordinator checks what the lane did against the plan.

**A ruling reaches every live lane in its turn.** An owner routing or process instruction applies to the next spawn and to every live lane on that problem in the turn it arrives: stand the lane down, or relaunch it on the named route, and edit the routing table afterwards. Never record it for new lanes only, weigh it against the routing table, keep a lane because it is deep in the code, or defer it to the lane editing the skill. A desk runner gets a cci record addressed to it with the verb it keys on (`orca-desk: launch`), never a policy sentence, an inbox-file line, or a SendMessage alone; a line appended to `inbox/orca-desk.md` is no longer read. A running Agent lane gets a SendMessage and a cci record, and the root confirms the handoff on disk before treating the lane as stood down.

## Read the owner's words before citing them

Settle a route, grade a timer or state a design count only from the owner's words read in full.

- **Search the owner's recorded answers by subject, never filtered by the drive label**, which misses most of them. Also search cci and the rulings records: the owner's words in cci are invisible to answer search, and `cci grep` finds prompts where answer search does not. Grep the register and the bus for the owner's words before stating a design count or settling a route.
- **Read a row whole before citing it:** its repeats, and every row under a cited rule id. Read the capture behind a cut `words` field, the GO record behind a one-line relay, and a hold's own lift condition.
- **Read the Board question first**, and the card nearest the owner's words.
- **A pick on another question settles nothing.** A pick of a mechanism beats a general bar.
- **A pick on the outcome does not defer the fundamental fix.** A pick like "trim" is no pick on a loop that does work per item, and a requirement met halfway is a real finding that goes to the lane.
- **A card quote carries its row id and comes from the card's drawn text**, never from a lane's draft. An unticked implication is the lane's inference, not the owner's.
- **A rule with an end condition ends when it arrives.** Recheck a constraint's end on any owner activity.

## Audit contracts and relays with `judge_watch.py`

Contracts, read-backs and `DECIDE` records never narrow an owner row or stretch it past its purpose. The relay runs `judge_watch.py` once every minute. It calls the drive's judge asynchronously after each post, so writers never block. Each artifact is judged once: a pass saves its place after each record it acts on, so a pass the supervisor stops at its 15-minute limit resumes after the last record it judged. An artifact holding an open flag is never judged again, by a new pass or a recheck, and each flag is saved as it posts, so a recheck a pass dies partway through flags nothing twice when it reruns.

| Artifact | What `judge_watch.py` asks the judge |
| --- | --- |
| Lane contract | Whether the contract misreads the owner's verbatim words on the rows it acts on, which `rulings.py readback --words` prints: the rows its slice carries for its lane, step, PRs and paths, and the rows it cites, a superseded one replaced by the rows superseding it. The judge reads them in one call, so the owner's newest words on a subject govern, and the flag names the row whose words it quotes. |
| Coordinator relay | Whether the relay misreads the owner's verbatim words on the rows it cites, or adds a mechanism missing from both the owner's words and the plan lines sharing words with it. |

**A `yes` verdict posts a `JUDGE` review record.** `judge_watch.py` posts one `review` record in reply to the artifact, addressed to its writer and `root`:

```
JUDGE <kind> #<seq> <key>: <sentence>
```

**The writer fixes the artifact or answers with the owner's words.** The writer can update the artifact to fix the misread, or reply to the flag quoting the owner words that carry its reading.

- A coordinator ruling from the last 24 hours closes the answer when it replies to the flag, the artifact or the writer's answer, or names one of the writer's PRs.
- Otherwise the writer sends the answer to `root` alone as an `ask` record containing the owner's words and both readings, never to a board lane.
- When the coordinator places a `judge-dispute-<flag>` card on the board and the owner picks an option, the coordinator saves that pick as an eval fixture under `judge/owner/`.

**Extending a ruling row triggers a retroactive re-judge.** When `rulings.py classify` extends an existing row with new owner words, `judge_watch.py` re-judges the 10 newest contracts and relays from the last 24 hours that cite that row, against all of the owner's words. An artifact that flips from carried to `yes` is flagged as a miss in the `JUDGE` record and written as an eval case under `judge/misses/`.

**Cite a rule as the owner's only with the owner's words.** A record that calls a rule the owner's quotes the owner's timed words. Any other rule, the coordinator's own included, is cited as "coordinator rule". `drive-watchdog` checks each such cite against the drive's `owner-prompts.jsonl` captures.

## Run `judge.py` and `judge-eval.py`

All small-model judge calls go through `scripts/long_running/judge.py`. Callers with several questions of the same shape in one pass, such as the stall watch, pass them in one `batch` call on the command line. That call uses Sonnet once, whatever `LONG_RUNNING_JUDGE` says.

- **Verbatim input.** Every judge reads the owner's verbatim words and the question those words answered, never the register's summaries.
- **Disagreement stays `unsure`**, which never warns.
- **Fail open.** A failed model call returns nothing, preserving the caller's non-judge behavior.
- **Logging.** Each call appends one JSON line to `judge/calls.jsonl` in the drive's state folder. `drive.py digest` prints each judge kind's verdicts, errors and latency over the last 24 hours.

`LONG_RUNNING_JUDGE` selects the backend:

| `LONG_RUNNING_JUDGE` value | Behavior |
| --- | --- |
| `jev+sonnet` (default) | Asks Jev first and returns a nonflagged verdict without calling Sonnet. A flagged Jev verdict gets one confirmation from Sonnet at low effort with Claude Code's system prompt replaced. |
| `sonnet+jev` | Sonnet answers first, and Jev settles a first answer of `unsure`. |
| `sonnet` | Uses Sonnet only. |
| `jev` | Uses Jev only. |

`judge-eval.py <fixtures>` benchmarks backends offline. It scores every backend against fixture files stored in the drive's state folder, which stays local because the fixtures contain the owner's words. It makes live model calls, so operators or lanes run it by hand before shipping a judge or changing a prompt, and never run it in CI.

## Ship-time and turn-time judges

`scripts/long_running/judge.py` powers warning judges at ship time, spawn time and coordinator reply time. Each runs alongside the deterministic check it fronts or replaces:

| Judge / Hook | When it runs | What it checks |
| --- | --- | --- |
| `ruling_misread` | PR ship | Judges the tests a PR ships the same way `judge_watch.py` judges contracts, checking whether they misread the owner's verbatim words on the rows each opened PR acts on, which `rulings.py slice --required --words` prints: its global, pending and touched rows and the rows it cites, a superseded one replaced by the rows superseding it. The judge reads them in one call, so the owner's newest words on a subject govern, and the warning names the row whose words it quotes. |
| `refusal_kinds_need_owner_answer` | PR ship | Reads the refusal kinds, retries, clocks and budgets a PR adds and asks whether the owner's words or the plan name them. |
| `pr_bodies_name_what_they_defer` | PR open or edit | Asks whether the PR body names the fix its stand-ins put off and why, and states in plain words the rulings the diff builds. |
| Brief `Scale:` judge | Lane spawn hook and relay | Requires a `Scale:` line in every lane brief and asks whether it names the `N` that the brief's code paths run at; the relay emits one `SCALE-MISSED` on failure. |
| Incident defect admission judge | Coordinator reply during an open incident | When a coordinator reply admits a defect during an open incident, asks whether the coordinator started the fix on the same turn. |
| Read-claim judge | After a code read or review | Asks whether a claim about code or a review states more than the preceding read showed. |
| Release cancel / rollback judge | Release cancel or rollback | Alongside the block that refuses an unordered rollback, checks a cancel or rollback citing an owner answer to verify that the answer orders that exact undo. |

## Enforce system shape tests (`SHAPE-UNTESTED`)

Every ruling about system shape needs a CI test in the same turn as the build. System shape rulings include invariants such as one process, one primary and one replica, everything acting as a client, or `N` items sharing one resource. The test must fail against the pre-fix code.

- `rulings.py shapes` lists all in-force shape rows. `rulings.py shape <key> --test <repo>#<pr> <path>::<test>` records the test that covers shape row `<key>`.
- At ship time, `check-pr` warns `SHAPE-UNTESTED` on any PR citing a shape row until the PR adds a test naming that row or `rulings.py shape` records one.
- Naming the row in a test is not enough. The drive's judge reads the test alongside the owner's verbatim words, and `check-pr` emits `SHAPE-UNTESTED` with the judge's sentence when the test asserts a weaker or different invariant that passes while the owner's invariant fails.
- The stall watch posts to `root` every shape row that remains untested 30 minutes after it was written.
