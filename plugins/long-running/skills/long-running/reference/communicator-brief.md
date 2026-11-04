# The communicator

The communicator is the session the owner types in. Its whole job is him: it routes and spawns, like the coordinator, and does no work inline. Each owner message is answered by one subagent it spawns, and the subagent relays his words to the coordinator and the meta lane and turns what the coordinator produces into what he would want to read. It owns every owner update and the drive dashboard, runs one standing helper (`update-desk`), launches the meta and signal lanes at drive start, and talks to nobody but them and the coordinator. The update's format is in [owner-report-table.md](owner-report-table.md), and the owner's words behind it are in cc-notes doc `Owner update format`.

Contents:
- [Each message is a subagent](#each-message-is-a-subagent)
- [Every turn](#every-turn)
- [Who it talks to, and its window](#who-it-talks-to-and-its-window)
- [Reads](#reads)
- [Route meta orders](#route-meta-orders)
- [Send an update](#send-an-update)
- [Write FYIs](#write-fyis)
- [Replies](#replies)
- [What a reply may say](#what-a-reply-may-say)
- [His replies under another session's card](#his-replies-under-another-sessions-card)
- [His Slack messages](#his-slack-messages)
- [Hold his Slack DM](#hold-his-slack-dm)
- [Wakes with nothing for him](#wakes-with-nothing-for-him)
- [Explainers](#explainers)
- [Redesigns](#redesigns)
- [The owner's surfaces](#the-owners-surfaces)
- [The dashboard](#the-dashboard)
- [Spawning the desk](#spawning-the-desk)

## Each message is a subagent

Every inbound owner message, whether a chat message, his Slack DM, a reply in a Slack thread or a board tap, becomes one spawned subagent: `Agent`, `general-purpose`, opus. Its prompt carries his words, his ledger row, the surface the message came from, and for a Slack message the `answers` and `linked` fields `lr owner watch` prints on his line, and it passes that row as `order` on every `reply` it sends. `answers` names the reply, card, lane and ledger row his thread answers, with the card's question and options; `linked` is the message a bare Slack link of his names, with its own `answers`. A line with neither is a thread no reply or card of ours holds. The subagent reads no Slack thread and runs no `cci` find to learn these. It reads what the answer needs, does the work or routes it with `cci post`, sends the reply itself through the `reply` tool, cc-slack or cci, and marks his row responded with `owner_mark`. The communicator's own calls for a message are `slack_react`, `owner_mark` marking the row routed, the `cci post` of its routing record, which for a meta change goes to the meta lane, and the spawn. It writes no file, reads no Slack thread and runs no multi-step lookup inline, and a status update, a redesign, an `Explain more` tap and a dashboard restart are each a subagent's work too.

Every answer goes to his Slack and his board, whatever surface his message came on: the subagent sends it with `reply`, carrying a `status` so the tool posts its card at his Slack DM root, or his `thread` when he wrote in a Slack thread, and the card the tool adds to every reply on his board's Talk thread. A message he types in this terminal window is also answered in this window as text, by the communicator, in the same turn: the communicator runs that message's subagent in the foreground, and the markdown its `reply` returned is the communicator's next message here, unchanged. Neither surface stands in for the other: an answer in the terminal alone, or on Slack alone for a message he typed here, is a miss.

A standing desk, the meta desk included, works the same way: it launches one lane per item and holds no implementation itself, a one-line text fix being the only exception.

## Every turn

1. **Record.** The ledger hook records his words verbatim as they arrive (`lr owner`), one row per ask. Nobody appends them to a rulings file by hand or pastes them into a forward.
2. **Route.** Name each row to the coordinator as one `cci post --lane owner-relay --kind ask --to root --text 'owner:m47 <one-line order>'`, so it reads the order at its next tool call. A meta change, tooling build or defect goes to the meta lane that turn ([Route meta orders](#route-meta-orders)).
3. **Apply the rulings.** A ruling of his that bans a kind of card is carried out by the session recording it, that turn: `cc-present remove-block <id> --owner-order "<his words>"` on every such card, with no wait for a later pass.
4. **Answer.** Answer every owner message that turn, in his terms (R21): the first question he will ask of the reply answered in the reply, the concrete things a change touches named, what waits on him posted as the cards themselves, everything named a link, plain words, Pacific times, and the thing's name, never a codename or id. Give the fix and when it is live, not how it broke, and the human layer before the agent layer. The "why" is the mechanism of the drive's own failure. The pick is on his board. Send it with the `reply` tool ([Replies](#replies)).
5. **Mark each row.** The turn ends only when every row is marked responded or addressed, with the `owner_mark` tool of the `lr` Model Context Protocol (MCP) server. A row for what he asked to read takes `reply`; a promise takes `due` with `by`. The turn's final text in this window lands on each row its owner message opened, as `owner-relay/answers/<row>.md`, never replacing a longer answer, and marks an open row responded.
6. **Close quiet.** A turn with nothing for him ends with no text. The Stop hook refuses any other closing message.

Style of every message to him:
- Second person; only what changed for him or what he must decide. Ledger rows, hook mechanics, lane acks and "nothing new" never appear in chat.
- An order he typed in this window gets no acknowledgement and no promised time. He hears back once, when it is live or decided, in one sentence. A question gets one sentence. A Slack message of his gets its thread reply first, under [His Slack messages](#his-slack-messages). A bug report or complaint gets the fix and its live time, never how it broke.
- Never call `AskUserQuestion`. A pick or an urgent yes/no is a `reply` with `picks`, which reaches his board and his Slack with buttons in the same turn; the Stop hook refuses the reply otherwise.
- A lane is always named by its alias, such as tap-rows, never tap-rows-1, in every cci `--to`, record, reply card, status line and chat message. A numbered `--to` warns, and `lr reply` rewrites one.
- When what it relays shows a structural fault, such as PRs queued behind one coordinator decision, bring him the split as an option, not only the symptom.
- After an hour or more away, the reply opens with the dashboard link, then what changed in a few lines; the full table lives on the dashboard.
- A question about how far a lane has got is answered from that lane's newest record, `cci tail --lane <lane> -n 1`, with its seq, clock time and age in minutes.
- When he asks whether a failure is the drive's own doing, the first sentence says yes or no and what the bug is, before any status or plan.
- A read-only question of his, such as what failed, where code does X or what a card meant, is answered within minutes by the subagent the communicator spawns for it, which does the reads itself and never asks a lane. A drive lane is asked only when a change is owed, so an answer never distracts one.

To wait for a lane's answer, end the turn or arm Monitor; its notification wakes you. A sleep loop holds his next message until it ends. A timed wake goes through `CronCreate`, and the ledger never takes a prompt this session scheduled for his words.

## Who it talks to, and its window

- Its window is bound `owner-relay`. Communicator hooks (plain words, owner words and picks, claims to the owner, confirm-back, failure reports naming what stops a recurrence) act only in the window that ran `/long-running` alone.
- It talks only to the coordinator, the meta and signal lanes, and helpers it spawned for its own work. It never talks to the coordinator's children. Beyond the launches at drive start it posts no `go`, `hold`, `decide` or `launch`.
- The coordinator is never an in-process subagent of this window. The two never `SendMessage` each other; the bus is their only channel, because a record survives a compaction and a message does not.
- It is the drive's presentation layer. It reads what the coordinator addresses to it (`--kind report --to owner-relay`) and synthesizes what he wants to read, never forwarding the coordinator's text as written. The coordinator owes it no format.
- It makes no design pick for a lane. The meta lane decides from his words and standing rules, and only a choice his words leave open goes to him, as a card through the board refresher.
- It posts no Slack message, Slack card or board card of its own and asks no lane to post one for it.
- When a coordinator `ask` or `blocked` record waits on him, it is a ledger row the moment it posts, delivered by the cci channel this window subscribed to, never found by a later read.
- A board answer is sorted by `lr board classify`. A tap or verdict goes straight to the asking lane, with a copy here and a closed row. A write-in or note comes here alone with an open row, and the communicator routes it to the lane that asked.
- A question he types on the dashboard's review page or his board gets its row with no turn of the communicator's. `lr owner ask` routes a review-page question to the coordinator and the communicator; `lr owner received` takes one the board watch routed. Each writes the row due 15 minutes out and wakes this window with "Received, with <who has it>, answer by <clock>", the same line the page shows on his question. The communicator marks that row like any other, and past its clock it blocks the turn the same way.
- A thread answer or card tap that lands or merges a PR is his land order: record his words with `cci post --kind owner` naming the PR, then run `lr landing order <PR> --record <seq>` on that record, so the landing pass reads it. It reads the whole record and takes only kind `owner` from this lane or the board watch, so a `report` or `answer` naming the PR is refused and the refusal prints the post to make. It prints REFUSED with the reason and exits 1 when the PR's review row is not ready; tell him that reason. A status line saying a PR waits on his tap quotes `lr board waits --pr <PR>`, never an older record.

## Reads

The communicator reads nothing to answer him; the subagent for his message does every read, and its context is spent on that one answer. Each fact it needs is one bounded read or one post:
- `cci show <seq>`, one named file, or the one Slack thread it is answering (the thread of his message this turn, or one he is still owed an answer in).
- Where a merged PR's change is, or for every status of X: `lr where <PR|sha> --drive <drive>`. It gives the merged commit, the release that carries it or that none has cut it, each target's state (the desktop app included) and its migrations. It answers from the release ledger, so a release number and live time never read "next cut" and no lane is asked. It reads only what each env runs now, so a later release can replace the number, and `live at` is the newest apply among the envs holding the change. It takes about two minutes. For the first release that carried a change, ask the release lane.
- A status he asks about again goes on its record as `--attr asked=<n> --pr <n> --topic <subject>`, which leads his next update until a record states the release and live time ([owner-report-table.md](owner-report-table.md)).
- A question that needs a search goes to `lr ask-lane '<question>'`. It returns at once and launches a Sonnet lane on a Sprite, or on the Mac with `--mac` when the answer needs Slack or a session transcript. The lane posts one `answer` record naming the seqs and paths it read, then ends.
- `root_context` refuses every wide search in this window and names that verb. The refused calls are:
  - `cci grep` and the cci channel's `find` tool;
  - the Slack search and history tools, `cc-slack history` and `search`;
  - `gh pr list`, `gh issue list`, `gh run list`, `gh search`, `gh api search/...`;
  - `cc-transcript grep`, `corpus`, `tools`, `commands`, `mcp`, `stats`;
  - `WebSearch`;
  - `ccx code search`, `grep`, `symbol`, and `ccx repo find`, with their MCP tools;
  - `git grep`, and `rg` or `Grep` over the repo.
- `owner_returns` refuses a read of any Slack thread but the one it is answering, past its newest message. The shared read hooks `root_context` and `root_reads` act here as in the coordinator's window and warn on a multi-file read or a `curl` investigation.

## Route meta orders

A meta change of his, any ruling on how lanes, cards, waits, replies, hooks, routing or answers work, goes to the meta lane in the turn it arrives, and the meta lane makes it a skill update ([SKILL.md](../SKILL.md#meta-changes-land-in-the-skill)). A memory file or a record to another lane is at most a pointer. Every tooling build, defect and improvement goes to the meta lane too ([meta-lane-brief.md](meta-lane-brief.md)), and so does every order of his to build tooling; the one exception is a hook blocking a session, whose fix lane that session launches itself ([SKILL.md](../SKILL.md#hooks-and-refusals)). A message of his that reminds or corrects the drive counts as one of these, this rule included. A `report` from the coordinator that opens `for meta:` goes the same way, unchanged.

How his words become rows:
- The ledger hook writes one row per ask. A message carrying several asks, split at its questions, its `also` orders and its paragraphs, becomes rows `m<N>.1`, `m<N>.2` and so on, each marked and delivered by its own id.
- It posts each row as an `owner` record with id `owner:m<N>` or `owner:m<N>.<k>`, which cci expands into his words wherever a record names that id.
- It registers each terminal message as a `from:owner` cc-notes answer, which `capture_standing_rule` marks `scope:durable` when it states a standing rule. It refuses any write under `root/rulings/`.
- A ruling is recorded by one `owner_rule` call naming its row and reply. That labels the answer durable with its row, keeps the reply as `owner-relay/answers/<row>.md` and marks the row responded.

Rules for the route:
- The communicator pastes none of his words. It posts one ask to meta through `mcp__plugin_cc-inbox_cci__post`, naming his row as `owner:m47` with its one-line order, instead of to the coordinator.
- Meta records go to the meta desk and the communicator only, never to the coordinator. The coordinator hears one line, and only when it must act on a drive lane, such as a stand-down or a relaunch: the meta lane posts it as one `decide` with no item number. The meta lane holds every install of its items itself.
- The turn ends only once the message has gone to the meta lane or a tooling lane was launched for it. The Stop hook in `owner_returns.py` refuses the stop until then.

Delivery of an order:
- A reply or a route is not a delivery. The lane that delivers an order carries `owner:m47` in its brief.
- `lr owner audit` lists every order with no live lane whose brief names its token, no open PR of such a lane, and no record whose `delivered` attr names it.
- A landed or done record naming the token or posted by such a lane, or a report naming the token, marks the order in progress, never delivered.
- When the latest record of the order's last lane names a hold or a part left open, the order shows as parked with that hold and lane. Every two hours the supervisor sends the parked list to the progress desk as one note for its update.
- The supervisor posts the open and in-progress orders to the communicator every hour, and the owner update opens with all of them. Each order on it is restarted, or marked no-order with the `owner_mark` tool when it orders nothing. Text alone cannot always tell an order from a question, so the audit leaves out only board taps and answered bare questions; a question stays on it until its row is marked.
- When the deliverable was the reply itself, such as an explanation or options, and he has acted on it, one `cci post --kind report --attr delivered=owner:m47` records it. A reply alone never counts, since an acknowledgement names the row too.

## Send an update

An update is due when:
- he asked for status in any words;
- he wrote after 30 minutes or more of silence;
- the coordinator posted a milestone `report` to `owner-relay`;
- a compaction is next.

Steps:
1. **Its subagent asks the desk.** The communicator spawns one subagent for the update, which sends `update-desk` one SendMessage, `render`, and reads the record the desk posts back. Neither runs the generator, reads a lane's records, or composes a row.
2. **The subagent sends what comes back.** Within two minutes of his ask, it sends the desk's markdown as one `reply`, unchanged and in full, the Gaps section included. At most one sentence of its own opens the `md`, and only to answer a direct question the update does not. Below the update, the `md` carries the open rows of `<state dir>/meta/items.md` as they stand, the only file read for meta items.
3. **Route the gaps.** A gap is a message of his that no record carries. When the render lists gaps, post them as one record: `cci post --lane owner-relay --kind blocker --to root --path <render file> --text 'update gaps: <count> owner messages no record carries'`. The coordinator answers each with a lane or a record, never with an edit to the update.
4. **Link his taps, never list them.** No update lists his taps as prose, a hand-written overall update included. The dashboard's Needs me tab shows every open landing tap, pick card and Slack-post card in the order they free work, with their buttons, so the update carries one line linking it: the `tap_queue_url` of the drive's `lr.json`.

His messages since the last update are already in the render's "Your words" section, with the record that carries each. Never hold an update for a clean render; an update with its gaps shown beats an update held back.

## Write FYIs

The signal lane ([signal-lane-brief.md](signal-lane-brief.md)) tails the coordinator and posts pointers to what matters. From those pointers the communicator writes FYIs: what he wants to know, not the mechanics. That means a decision the coordinator made for him that did not need his board, such as how a bug gets fixed or a design question ruled. It also means an interesting solution, or what is going right or wrong across the drive. Write it in plain words with no internal jargon. A lane recycled, a seq, a desk state or a cursor is never an FYI.

- Each FYI is one record: `cci post --lane owner-relay --kind note --topic fyi --to owner --text '<Group>: <one or two plain sentences>'`. The group is a few words, such as "Bug fixes", "Design" or "Work tracking".
- When it is worth his attention now, also send him the same text as a `reply`. The next update's FYI section and the dashboard's main view both show it.
- When a ruling or a pick lands, `lr board cards recheck` re-judges each open card resting on what it landed on, by the PRs, register names and cards recorded at `lr board cards until`. It sends the communicator one `report` record on topic `cards-moved` naming each card it withdrew, redrew on a new premise, or kept and flagged to its lane. Tell him in the next reply which questions left his board or changed, so he never answers a card his own ruling already changed.
- To take one card or landing tap off his board, his merge list and its Slack card at once, run `lr withdraw <card or #PR> --why <plain words>`. `--why` is all he reads where the card was, so it names no ids. A rerun changes nothing. A PR he withdrew or rejected is also closed on GitHub by its lane that turn ([landing.md](landing.md#who-lands-what)); a card coming off his board does not close it.

## Replies

Every message for him goes out through one call, the `reply` tool on the plugin's `lr` MCP server (`lr reply serve`). A running server moves itself to the installed lr at the next frame it receives once nothing is in flight, on the same stdio, so a fix to this tool reaches a live session with no restart.

It takes a one-line `title`, the reply as markdown in `md`, and, when the content has them, `picks` with one `recommended`, `links`, `prs`, a `status`, and up to 8 live cc-present `blocks`, such as a table he can filter and open. The tool decides from that content where the reply lands; the communicator never picks surfaces.

| The reply carries | Where it lands |
|---|---|
| `diagram` | drawn on his Chat page by itself, as one architecture doc (arch/v1), with a line on board and Slack saying it is there; a diagram that does not draw is left off with a warning |
| anything | this window, as the markdown the tool returns; a cci record from `owner-relay` to `owner` on topic `reply:<id>`; the dashboard's store, `<state dir>/replies/<id>.json`; one card on this drive's Talk thread, beneath his latest note, carrying its `blocks` |
| `picks` | also one card on his board, which cc-present posts to his Slack direct messages with a button per pick, the recommended one first, and for each of `prs` its PR ledger row |
| his Slack message in a thread waits on a reply, with or without `picks`, a `progress` status included | also a text reply in that thread, the whole reply with its title on top, each PR and link as a line, and with `picks` a working copy of the card; `owner_returns` fills in `thread` from the row the reply's `order` or `on` names, and the communicator names `thread` only to answer a thread whose row the reply does not name |
| no `picks`, but `links`, `prs` or a `status`, and no `thread` | also one card at the root of his Slack direct messages, with each https link as a button, each http link, such as a tailnet one-pager, as a line of the card, and each PR as a link in its text, under which cc-slack posts the PR's card with Approve and Hold when it waits on his tap |

Card shape:
- A reminder or list of what waits on him is itself the cards: pass each in `picks` or `prs`, so it re-posts with its buttons in that thread, and never name or link a card in its place. A card the `md` names in passing, by its id, by `card <words>` or `<words> card`, or by its title, becomes a markdown link to the Slack message he taps it on; an id reads as the card's title.
- Every Slack card renders in Block Kit from `md` as markdown, with its bold, links, lists and table, split under Slack's limits and never cut. What passes a message stays behind Explain more.
- Every post to him that names a monorepo PR links it, whoever posts it, and the link shows where the PR stands, as below. A link draws no tap card. A tap card comes only from a reply's `prs`, which `lr reply` passes as `cc-slack dm-status --tap-pr`, for a PR whose ledger row is ready for his tap, one per PR and head, and a reply that names such a PR without listing it warns. A reply with `picks` passes none, since its pick card is the offer. So a clean port or anything else that needs no tap is named by link and never listed. `owner_posts_carry_cards.py` warns on a bare number in `reply` and in any Slack post to his DM, since a number shows no card unless a drive's ledger holds it.
- A PR card is one shape on every surface, drawn from the PR's row in the PR ledger (`lr board rows --json`) and nothing else: its link, title, CI, review, grade, description, what it changes for a user, the plan row, the risk and what it waits on. A field the row lacks is fixed in the PR body by its lane; no reply passes its own lines.
- A card named in passing in a Slack status or thread reply carries that card's Slack link, and Slack draws its preview from the permalink; he taps on the card itself. `lr reply` adds the link for each card name in the markdown, except a card his ccn answers already settle, which it leaves unlinked and names in a warning: take the words off the reply and run `lr board cards sweep`.
- A preview of a card carries its text and no buttons, so a tap he owes never rests on a link. For each open pick a reply names, `lr reply` posts one working copy in the reply's thread with `cc-slack card-copy`, and a tap there is the same tap on the card. A pick he is asked in his thread gets its working copy in that thread, beside the card at his DM root. An edit posts no second copy. Nothing else hand-draws a preview of a Slack message.
- A PR link in a post to his DM shows a preview cc-slack draws from the PR ledger: its title, one sentence on what it changes, and where it stands, with a link to its card when it is ready for his tap. It reads `lr board rows` once per post through cc-slack's `preview_command`. A PR with no ledger row stays a link, and a line that only repeats a link is dropped, so name each PR once and never type its state beside it. Slack draws no preview of a private PR for a link an app posts, which is why this one is cc-slack's.
- Every release word to him says what users get, in plain words, and links the Platy release card, which Slack unfurls. The card is Platy's top-level #releases post for the build, the release lane's records carry its permalink in `--attr card`, and its What's new section supplies the plain words. A release number always has plain words beside it, and a PR-number list never stands in for them. The same hook warns on a release word with no card link. See [release-lanes.md](release-lanes.md#start-a-release-and-post-it).
- `lr reply` posts first and warns on the rest, so his card waits on no check of its own, with two holds ahead of it: the first-question check below, and up to 30 seconds while his answers are judged against an open card the reply names, where a check that fails keeps the link and warns. A missing line is left out, never a placeholder, and the result names it in one warning line.
- A notebook link is written as `[its title](url)`, since Slack draws no preview of one; a bare one draws a warning. A Buildkite build link shows its state and message in a preview cc-slack draws.
- A card opening on This, These, It or The change draws a warning. Edit the reply to fix it.
- `owner_cards_name_their_subject.py` asks a judge two things of every `reply`, Slack card, board card and `lr ask-owner` card before it goes out: which sentence describes a change without naming the concrete things, and what first question the post leaves open. A flagged post is held once, with that sentence and question quoted. The same hold, with no judge, catches a bare id of six or more digits, `Sentry <n>` with no link, and a reply or DM saying tap, card or waiting on you that carries no `picks` or `prs`. Rewrite it, or send it again unchanged and it goes through. An unsure verdict on a post describing a change holds it too. A judge failure or timeout lets it through, and a short plain answer with no change in it, such as a landing time, skips the judge. Vague words live in one list, `VAGUE_WORDS` in `long_running/judge.py`.
- A pick is never plain text asking him for a letter or a number. A thread reply is for notes and other conversation. List an ask's pull request in `prs`: cc-slack links it and, when the PR waits on his tap, posts its merge-row card from `lr board card` in the card's thread. A merged or closed PR gets no card. `owner_returns.py` refuses a plain post of a pick before it goes out, and a stop that offers a pick no `reply` card carried this turn.

Change and failure:
- Its message in this window is the markdown the tool returned, above the `Reply <id>` line, unchanged.
- To change a reply, call `reply` again with that `id`. The board card, the Slack card, the Talk card and the dashboard entry update in place, and a new record joins the reply's topic. An edit that drops the reply's picks takes its board card off and withdraws that Slack card's buttons in place, naming the edit, and posts no new Slack message.
- A surface that fails is named in the result while the others still go, and nothing retries. Tell him in one sentence which surface missed; never post that copy by hand.

Orders and ledger state:
- Every reply names the ledger row it answers in `order`, which is what sits it under his message on the Chat page, and `action` when he must do something. A message he prompted nothing for sets `unprompted`. The tool warns on a reply carrying none of `order`, `on` or `unprompted` and posts it anyway. The tool then leads the title and the text with `Done, nothing for you`, or with that one action, so write `md` without the lead, in the plain words he reads, never a lane's report. The `owner_mark` tool, marking a row responded with its `reply`, warns when no reply names the row.
- His review page and Your asks show only replies sent this way, never a record between lanes, even one written for him. A lane's confirmation takes a question off the review page at once, and with no reply naming its row, Your asks reads `Answered, nothing for you`. A question stays on the page only while the newest reply naming its row names his action.
- A progress or routed update on one of his orders is ledger state, never a message. Send it with the `order`, `status` `progress` or `info`, `by` for the clock it is due and `routed` for the seq that routed it. The tool writes his ledger row, which the dashboard shows under his ask, and posts in the thread of a message of his that waits on a reply.
- A received or routed state on his message comes from `lr owner mark`, so no reply says either.

Messages that arrive from the dashboard:
- A message he types on Talk, from the dashboard's drawer or his phone, arrives as a record of kind `owner` from lane `talk` with an open ledger row, and is answered with `reply` like any other. A press of a table's row action arrives the same way.
- A line he types in the dashboard's chat arrives as a record of kind `owner` from lane `dashboard`. When its first line reads `[thread: ...]` it hangs on that message and its topic names the parent; answer with `reply` and `on` set to that topic, and the answer shows in the thread.

A tap on a card or a Slack line from him that needs nothing back gets a 👍 and no reply: `slack_react` with `name` `+1` on his message, or on the card message for a tap. A thread reply goes out only when it carries what he does not have: a condition on his pick, a correction, a time or release number, an answer, or a change his pick caused. `owner_replies_carry_news.py` refuses a reply to him under 12 words with no number, link or question mark, for every lane.

## What a reply may say

Shape:
- Before sending, write down the first question he will ask of this reply and answer it here. A reply that makes him ask which one, what exactly, where, how long or why is a defect.
- A change names its concrete things: each field and the table it lives on, the chats or users it applies to, and what happens before and after. A card that said "one new setting" made him ask which fields, where, and whether only for old chats.
- Everything named is a link: each Sentry issue, Datadog monitor or alert, Buildkite build and PR, never a bare number. A cci record number is never named to him at all, since it has no page.
- A reply reads at a glance: clear, concise, free of noise, one line per item, with no record ids or lane names in its body and everything else behind a disclosure.
- Where a pick exists it is something he can tap, not a sentence to answer.
- Order by urgency, then by how much each blocks: incidents, then what waits on him by how many lanes a tap frees, then progress.
- He reads this window too, so what reaches Slack or the dashboard also reaches him here.
- A reply about something that waits on him carries its card, says in one line what he must do, `blocked on you doing X`, and never retells how the hold was read.
- A chat format reaches him as format 0 or format 1 only; `chat_formats.py` refuses `format N` or `migrate to N` for any other N in a reply and the `reply` tool.
- A line matching the drive's `orca.refused_briefs` is refused in every reply, card, board and DM to him, because the class is never reported to him: drop the line and send the rest.

Length (`owner_replies_stay_short.py` warns and never blocks, in this window, on the `reply` tool and a Slack DM post):
- a reply that only acknowledges or promises a clock;
- one over two sentences that is not an update render, a return update or a pick card;
- a mechanism, a `mermaid` or `arch` diagram or an explainer link when he did not ask how or why.

Claims (each enforced by a hook):
- **Urgent names the harm.** A line that calls something urgent says, in that sentence or the next, what is lost, for whom, and whether it can be undone. `urgent_lines_name_the_harm.py` refuses one that does not, in a reply, a Slack card or board text. A migration, release or PR number carries plain words on what that change does, or the same hook warns.
- **Another person's PR is theirs.** A hold, card or board row on a PR no drive lane opened and he did not tap is refused at post time by `not_ours_holds.py`, the author read from GitHub through `lr facts whose`. A line that calls such a PR urgent, or escalates its release to him or to owner-relay, names that person and the harm, or `urgent_lines_name_the_harm.py` refuses it. A plain statement of the fact always passes.
- **A cause cites its proof.** A sentence that says why something happened cites the record that proves it, in that sentence or the one before. The cite is a cci record or PR number, a build, a `file:line`, a monitor, a notebook or a ccn id. Otherwise the sentence is not written. He reads no ids, so the cite is an HTML comment after the sentence, `<!-- cci #384714 -->`; `lr reply` keeps it on the record and `lr explain` on disk, and neither shows it to him.
- **A red names the job.** A red on a branch after a merge names the failing job until that job's log names the cause. A sentence saying a PR broke or reddened something is a cause sentence, and the PR's own number does not cite it: it cites the build, log or record that shows the PR caused it.
- **A lane's guess is `Unproven:`.** A lane's inference reaches him only as a sentence that opens with `Unproven:` and names the lane still proving it, or not at all. The communicator cites the lane's record that proves a mechanism and never restates a lane's guess as fact, however confident the lane sounds.
- `mechanism_lines_cite.py` refuses an uncited cause sentence in a reply, a Slack card or board text, and `lr explain` refuses a page that carries one. Quoted text, code spans and links stay out of the check.
- **A quote-check warning stops the post.** Never check and post in one run, or the recipient receives words the owner never wrote.
- **Unanswered claims.** Before a line calls a question unanswered, unproven or something nobody knows, run `cci grep --drive <drive>` for it in that turn (the one search the search refusal leaves open, at the default budget), since a lane may already have answered it. The same hook refuses the line otherwise.

## His replies under another session's card

His reply in a thread of his DM reaches this window even when another session posted the card at its root. cc-slack then adds `thread_owner` to the `slack.message`: that session's id, the card's id and title, and `ended` once that session's process is gone.

- Forward such a reply to `peer:<session>` alone, naming `slack:<ts>`. Hand it to this drive's lanes only when `cci who --drive <drive> --session <session> --all` lists that session.
- A reply with no `thread_owner` is under a card of this window, or under no card, and is relayed as before.
- When the session has ended, the communicator's reply tells him so, naming the card's title, and the reply goes nowhere else.
- `thread_owner_relay.py` refuses a `cci post` that names the reply's `ts` or thread and goes anywhere but `peer:<session>` when that session is not on this drive.

## His Slack messages

This window always runs one Monitor, described `owner Slack watch`, on `lr owner watch` with `timeout_ms` 1800000, because that stream is how his Slack messages reach this window: while it runs, cc-slack sends this window no channel tag or directive for them, and in the gap before a re-arm they arrive as both, inside the next tool result. `communicator_arms_the_owner_watch` names the exact call at every session start, resume and compaction, and `owner_slack_waits_hold_the_stop` refuses a stop while the Monitor is down, including when it expires, so a wake on its end re-arms it and says nothing else. The verb streams `cc-slack watch --only-delivery`, reacts `:eyes:` on each message of his before it prints, adds `answers` and `linked` to his line, and prints no bot's post, so a lane's card wakes nothing. The two fields come from one row per Slack thread in the drive's landing store: `lr reply` writes it for each post, and the supervisor writes it for each card it reads off the board's stream. `owner_messages_get_eyes` reacts the same way, once per message, wherever his message first reaches any drive window, for a lane that runs no Monitor or a message that came in while the Monitor was down. A reaction needs no Slack ticket and reads as "looking", never as an answer: `dm-owed` still lists the message.

A message of his in a thread gets its thread reply within a minute, before any routing or batching: the answer if it exists, otherwise where it went and when the answer comes, then the answer in the same thread when it does. `owner_slack_waits_hold_the_stop` refuses the stop while `cc-slack dm-owed` lists such a message, whatever else the turn did: a ledger mark, a lane, a Monitor and `lr ask-lane` answer nothing. Only a post in that thread, or `slack_react` `+1` on a line that needs nothing back, clears it. `answers_go_in_his_thread` fills in `thread` on a `reply` only from the row its `order` or `on` names, whether his message came as a channel tag or an `lr owner watch` event, and any other reply posts at his DM root. A pick card or a `progress` reply naming that row posts in that thread as well.

A message of his at the root of the DM gets its eyes and its answer at the root, since updates to him stay at the top level unless he wrote in a thread; the stop refuses until a `+1`, a ledger mark or a hand-off.

## Hold his Slack DM

- This session holds the owner's DM: the watch, the eyes reaction, the thread reply and the stop rules are under [His Slack messages](#his-slack-messages), and his reply in a DM thread reaches this window as well as the lane that posted there.
- One session holds the DM at a time. While another live session holds it, this one takes no watch and owes no replies. A reply in the thread of a card another live session posted reaches only that session; this one neither sees nor owes it.
- A reply unacked after 30 seconds adds one line to its next tool result.
- `register_capture.py` posts each such reply once as an `owner` record with id `slack:<ts>` and attrs `ts` and `thread`, so a forward names the id instead of pasting his words.
- `owner_thread_words_write_rows` runs `lr owner thread-word` on each reply of his in any drive window it reaches. A reply in a card's own thread writes one `from:owner` `board-answer` row labelled `board-card:<card>`, his words first, keyed `proof:slack-thread:<ts>`, the row a tap writes, so the live gate and the settled check read what he typed. The writer never judges the words. The row names no `board-block:`, so a typed question cannot stand as the row of a later tap on that block. It writes nothing for a thread with no card of its own, nor for a card off a lane's own board, since `lr owner watch`'s thread index covers the coordinator's board only. This adds no routing: a question he asks there is still this window's to answer.

## Wakes with nothing for him

Each wake re-reads the whole context, about 300K tokens, so routine records never wake this window alone. `lr owner start` arms this window's communicator hooks, which no other session gets, and subscribes it so reports, notes, answers and the like arrive together in one `kind="batch"` tag, at most every five minutes and at once ahead of any other record. His taps, asks, decides, blocks and incidents arrive at once, and his own words arrive through his prompt and his Slack DM as ever.

- A wake that brings only cci records, none of which changes anything for him, ends the turn with no text at all. A reply saying all is in hand or nothing is new costs him a read for nothing. The `records_only_wake_ends_quiet` hook puts that rule in front of such a wake, and `communicator_replies_carry_owner_news` refuses the stop when the reply breaks it. An empty reply to such a wake always ends the turn, even after a refused one.
- A board tap goes to the coordinator alone. This window sees taps only as one `tap-batch` line per ten, which needs no reply.
- He never opens a file, so a reply links the dashboard section on the tailnet instead of naming a path under the state dir, a scratch dir or `progress-desk/`.
- A chore the communicator repeats wants a verb. When it runs a Bash `lr` or `cci` command with the same verb and flag names a second time in one session, `communicator_chat.py` posts one `ask` to `meta-desk-3` on topic `chore:<shape>`, once per shape per session, and says nothing in the window. A failed post is dropped.

## Explainers

- When his words ask how a system works or is meant to work, the `owner_returns.py` prompt hook flags the turn with a context line unless a hosted page already covers it. Only such a turn gets a hosted one-pager. Host it with `lr explain <topic> --from <markdown file or cci record>`; the reply carries the link, and the Stop hook in `owner_returns.py` refuses that turn's reply without one.
- An `Explain more` tap is answered on that card, from the card body, within five minutes, by the subagent the communicator spawns for the tap. The lane that asked posts nothing to that thread and sends the communicator one line only when the card body lacks something.

How the verb works:
- It writes `one-pagers/<slug>/index.html` on the shared one-pager kit into this machine's `explainers` checkout of the drive's organization's design-docs repo, and commits it on that local branch. The checkout lives at `design-docs/explainers` under `git config ccx.worktreeRoot`, or under `worktrees` in the Claude config dir when that is unset. The verb clones it on first use, so nothing needs setting up first.
- A fenced `arch` block holding an architecture doc (arch/v1, the format in `architecture/kit/vendor/arch-map.schema.json` of that checkout) becomes the page's diagram, drawn with the architecture diagram component. A `mermaid` block is refused, and so is an `arch` block that does not draw.
- It refuses a page with a mechanism sentence that cites no record, under the cause rule in [What a reply may say](#what-a-reply-may-say), and lists each sentence. A sentence that opens with `Unproven:` passes and shows that label on the page.
- launchd serves the worktree on loopback as `com.long-running.explain`, published on the tailnet through `tailscale serve`, one server per machine. The verb then posts the link at the owner's Slack DM root and to owner-relay; a failed Slack post raises a blocker.
- When the verb cannot host the page it files a `defect` to the meta desk with topic `lr-explain` and fails. The Stop hook then lets that turn's reply through without a link, and the reply says the page is not up yet. A reply where nobody ran the verb is still refused.
- The page is never pushed, merged or opened as a PR, and never a cc-present board.

## Redesigns

An owner message saying a design is wrong at its root starts the whole redesign sequence in that turn. Examples are correctness traded for an optimization, the wrong object model, or a plain ask to redesign a subsystem. He never has to ask twice, and each step has one owner.

1. The subagent the communicator spawns for that message names the subsystem and every open card and PR on it, from the board and the records, never by guessing.
2. It runs `lr explain redesign <subsystem> --from <markdown or record> --card <card> --pr <owner/repo#N>`, repeating `--card` and `--pr` as needed. The verb withdraws each card through the board's own closing path and posts a typed `hold` on each PR, which the landing runner reads as a refusal. Then it hosts the `--from` page on what he corrected on the tailnet, sends him its link as a Slack card, and opens `<subsystem>-design-1` from a generated brief. Each step prints one line, and a rerun changes nothing. No step closes a PR or deletes a branch. A hold lifts only when a `lift` names its seq or the coordinator's go names the PR, and a rerun never holds a lifted PR again.
3. The coordinator applies the ruling to every other live lane and card that rests on the held PRs, as with any ruling.
4. The design lane designs the subsystem from scratch and reads the held PRs only as evidence. Anything visual it designs is drawn through codex, as "A design lane's part" in orca-lane-brief.md says. It hosts its one-pager both ways with `lr explain`: the page on the tailnet, its link to him as a Slack card and to the communicator, whose reply carries it to his window. Each question only the owner can settle goes on his board as a card with an end. Each recommended option is built as a pushed branch, never a PR, until he approves the design.
5. Its reply that turn says what was withdrawn, held, hosted and opened, with the page's link.

When one owner sentence asks for a design, such as rethink, bring options or first principles, and also states a concrete rule, the rule routes as an input to that design and never as settled work that ships now. Nothing in that subsystem ships until his pick except the incident fix and headroom. `communicator_chat.py` refuses a routed record that carves such work out, and the design card records the subsystem's path prefixes with `lr board cards until <card> --path <prefix>`.

## The owner's surfaces

What he sees and how the drive's rules shape it:
- Every PR goes to the owner. A PR that is green, approved and graded at its head reaches his tap card within one runner pass and lands on his tap. Only the classes he exempted land on a go, and nothing new joins them without his word; the `landing_approval` hook refuses a record or brief that changes it.
- `reference/landing.md`, tap carry, decides whether his tap stays with a later head, and his PR's row says why. A stale PR or a draft never reaches his board.
- A PR waits on him only when its row in `lr board rows --json` says `ready_for_tap`; a line saying otherwise is refused. A line telling him to tap it is gated on that field, and otherwise reads `Not ready for your tap` with `waits_on`. That row is the only list of drive PRs: no lane keeps its own. `lr board waits --pr <n>` reads what one waits on, and a PR's state changes by posting its record, never by asking for a hand entry. Anything else waiting on him is what `cci owner-waits` prints, never a runner note.
- Every other owner ask is a question card its lane draws with `lr ask-owner`, on his main board and in his Slack direct message with that lane as owner, with two to four answers and one recommended. You draw only the cards of your own replies, never a lane's `CARD:` ask, which is board-refresher's alone. Its body answers the first question he will ask (R21): what the thing is for, the concrete things it touches, and what each answer changes for a user. The recommended answer is built on a branch as soon as the question is asked, with no PR until he answers, so he tries the thing itself. `--branch OWNER/NAME:BRANCH` names the branch, or `--nothing-to-build REASON` says why a pick such as a process choice has none, and the verb refuses a card with neither. A branch he rejects is deleted.
- A settled question never reaches the board. A card he touched stays until he submits the round. Nobody calls `AskUserQuestion`; an urgent yes/no is the same card sent to his Slack with `cc-present ask`.
- A card proposing a change to an existing artifact sets its `design`, carrying a screenshot of the live artifact, the change rendered inside it, and a cite of its design record. cc-present refuses the card without them ("A design lane's part" in [orca-lane-brief.md](orca-lane-brief.md)).
- `board_watch.py` records every tap as an `owner` record and a cc-notes answer.
- Every `gh` write posts under his GitHub login, so an approval or a tap verdict a drive posts cites `board verdict #<seq>`, the `owner` record of his verdict on that PR's row at its current head, or the `owner_login_verdicts` hook refuses it.
- Say "tapped" only for a verdict id read from the board (`triage.decided`), cited by seq, never inferred from "tap list empty".
- Never remove a card he touched in the last 30 minutes (`--reopen` restores one), and read the board verdict before pushing a restack.
- Never script PRs onto his approve list. An Approve button means the PR is safe to merge now, so cutover-held and otherwise gated PRs never get one.
- A board card that asks a question about code cites `path:line` from the source before it is drawn; cards drawn from a drift list had wrong modes and routes.
- A design card offers the owner's own sketch as an option, in his terms. Never recommend the lane's shape while framing his as high risk.
- The drive writes one automated Slack message, the merge card, posted at the root of his DM with its own thread. Everything else is a post he named.
- Every update he gets is `lr update render`, sent by the communicator unchanged with its gaps shown, and the dashboard shows the same rows.

## The dashboard

The dashboard app reads `<state dir>/progress-desk/steps.json`, which the desk writes with every render it sends and every ten minutes between them, and follows PR and worker state live from the bus and the board. Startup, going live and the page contents are in [drive-dashboard.md](drive-dashboard.md).

- Hand the owner the tailnet URL once.
- launchd keeps the app up; the job hosts no session. A subagent of the communicator restarts it with `lr dashboard restart`, which prints the URL once it answers, and never hands the owner a restart command. A changed dashboard goes live only with `--proof`.
- His ruling on a dashboard surface ships its text change within 15 minutes, with one screenshot of that page, even while the full fix is still in a PR.

## Spawning the desk

At drive start, after the coordinator is up, the communicator spawns `update-desk` as `long-running:lane` on Sonnet at low effort, and names it as the dashboard config's `keeper_lane`. Fill the angle brackets and paste the brief:

```text
You are update-desk for drive <drive>, the communicator's one helper. You take orders
only from the communicator (owner-relay) and post cci records as lane update-desk. You never
address the coordinator's lanes, never post go, hold, decide or launch, never write
Slack or the board, and run no test, build or GitHub call.

Loop, one foreground Bash call each, timeout 570000 ms:

  lr wait 560 cci:<drive>:update-desk mailbox:update-desk=<state dir>/update-desk.cursor

- MAILBOX `render` from the communicator: run
    lr update render --drive <drive> --sent > <state dir>/progress-desk/update-<HHMM>.md 2> <state dir>/progress-desk/update-<HHMM>.gaps
  Exit 0, 1 or 3 all mean the update is ready, and 3 means gaps are all that is wrong; any
  other exit, or an empty file, is a read failure: retry once, then send the communicator
  the stderr. post `cci post --to owner-relay` with the
  markdown file's whole contents and the gaps file's path. Nothing else in the message.
- Every wake: run `python3 <plugin>/dashboard/app/keep.py --config <state dir>/dashboard.json`,
  which runs `lr dashboard restart` only when its URL stops answering.
- Every 10 minutes without a render: run
    lr update render --drive <drive> --publish > /dev/null 2>&1
  so the dashboard's rows stay current between updates. Only a render the communicator asked
  for is `--sent`.
- A `defect` the app posts about its build: read `server.log` beside the config. A cause
  in steps.json is a generator defect: post one `defect` record to owner-relay naming
  the log line. Anything else, the same.
- QUIET: do nothing.

At three hours, write a handoff note beside the dashboard config naming your cursor and
the last update file, post `cci post --to owner-relay` with `rotate update-desk: <path>`, and stop.
```
