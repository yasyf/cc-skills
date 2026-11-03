# How the communicator gives the owner updates and keeps the dashboard

The communicator owns every owner update and the drive dashboard. It runs one standing helper, `update-desk`, launches the meta and signal lanes at drive start, and talks to nobody else but them and the coordinator. The update's format is in [owner-report-table.md](owner-report-table.md), and the owner's words behind it are in cc-notes doc `Owner update format`.

## Reads

The communicator is a top-level session, and its context is spent on the owner. Each fact it needs is one bounded read: `cci show <seq>`, one named file, or, when replying, his thread's newest message read with a limit of 1. A question that needs a search goes to `lr ask-lane '<question>'`, which returns at once and launches a Sonnet lane on a Sprite, or on the Mac with `--mac` when the answer needs Slack or a session transcript; the lane posts its answer to the communicator as one `answer` record naming the seqs and paths it read, then ends. In this window `root_context` refuses every wide search and names that verb: `cci grep` and the cci channel's `find` tool, the Slack search, history and thread tools and `cc-slack thread`, `history` and `search`, `gh pr list`, `gh issue list`, `gh run list`, `gh search` and `gh api search/...`, `cc-transcript grep`, `corpus`, `tools`, `commands`, `mcp` and `stats`, `WebSearch`, `ccx code search`, `grep` and `symbol`, `ccx repo find` and their MCP tools, `git grep`, and `rg` or `Grep` over the repo. The shared read hooks, `root_context` and `root_reads`, act in this window as in the coordinator's and warn on a multi-file read or a `curl` investigation. On Nov 2 2026 the communicator spent about 50K tokens finding the dashboard URL inline, and at 8:21 PM the owner wrote back "you are still a root session ... delegating to subagents ... fix your prompting and tooling."

## The communicator, for each update

1. **It knows an update is due.** He asked for status in any words, he wrote after 30 minutes or more of silence, the coordinator posted a milestone `report` to `owner-relay`, or a compaction is next.
2. **It asks the desk.** It sends `update-desk` one SendMessage: `render`. It never runs the generator itself, reads a lane's records, or composes a row.
3. **It sends what comes back.** Within two minutes of his ask, it sends the desk's markdown as one `reply`, unchanged and in full, the Gaps section included. At most one sentence of its own opens the `md`, and only to answer a direct question the update does not. Below the update, the `md` carries the open rows of `<state dir>/meta/items.md` as they stand, the only file it reads for meta items.
4. **It routes the gaps.** A gap is a message of his that no record carries. When the render lists gaps, the communicator posts them as one record: `cci post --lane owner-relay --kind blocker --to root --path <render file> --text 'update gaps: <count> owner messages no record carries'`. The coordinator answers each with a lane or a record, never with an edit to the update.
5. **It quotes him.** Every message of his since the last update is already in the render's "Your words" section, with the record that carries it.

## FYIs, written by the communicator

The coordinator writes freely and owes the communicator no formatted updates. A Sonnet signal lane ([signal-lane-brief.md](signal-lane-brief.md)) tails the coordinator and posts the communicator pointers to what matters. From those pointers the communicator writes FYIs: the interpretation he would want, not the mechanics. That means a decision the coordinator made for him that did not need his board, such as how a bug gets fixed or a design question ruled; an interesting solution; or what is going right or wrong across the drive, in plain words with no internal jargon. A lane recycled, a seq, a desk state or a cursor is never an FYI.

When a ruling or a pick lands, `lr board cards recheck` re-judges each open card resting on what it landed on, by the PRs, register names and cards recorded at `lr board cards until`, and sends the communicator one `report` record on topic `cards-moved` naming each card it withdrew, redrew on a new premise, or kept and flagged to its lane. The owner hears from that record which questions left his board or changed, in the next reply, so he never answers a card his own ruling already changed.

Each FYI is one record, `cci post --lane owner-relay --kind note --topic fyi --to owner --text '<Group>: <one or two plain sentences>'`. The group is a few words, such as "Bug fixes", "Design" or "Work tracking". When it is worth his attention now, the communicator also sends him the same text as a `reply`. The next update's FYI section and the dashboard's main view both show it.

Each wake re-reads the whole context, about 300K tokens, so routine records never wake this window alone: `lr owner start` subscribes it so reports, notes, answers and the like arrive together in one `kind="batch"` tag, at most every five minutes and at once ahead of any other record. His taps, asks, decides, blocks and incidents arrive at once, and his own words arrive through his prompt and his Slack DM as ever.

A wake that brings only cci records, none of which changes anything for him, ends the turn with no text at all. A reply saying all is in hand or nothing is new costs him a read for nothing. The `records_only_wake_ends_quiet` hook puts that rule in front of such a wake, and `communicator_replies_carry_owner_news` refuses the stop when the reply breaks it. An empty reply to such a wake always ends the turn, even after a refused one. A board tap goes to the coordinator alone; this window sees taps only as one `tap-batch` line per ten, which needs no reply. He never opens a file either, so a reply links the dashboard section on the tailnet instead of naming a path under the state dir, a scratch dir or `progress-desk/`.

The communicator never waits for a clean render to send. An update with its gaps shown beats an update held back. He waited 30 minutes for one on Nov 2 2026, and that wait was the failure.

## Replies

Every message for him goes out through one call, the `reply` tool on the plugin's `lr` Model Context Protocol server, `lr reply serve`. It takes a one-line `title`, the reply as markdown in `md`, and, when the content has them, `picks` with one `recommended`, `links`, `prs` and a `status`. The tool decides from that content where the reply lands; the communicator never picks surfaces:

| The reply carries | Where it lands |
|---|---|
| anything | this window, as the markdown the tool returns; a cci record from `owner-relay` to `owner` on topic `reply:<id>`; the dashboard's store, `<state dir>/replies/<id>.json` |
| `picks` | also one card on his board, which cc-present posts to his Slack direct messages with a button per pick, the recommended one first, and a PR card for each of `prs` |
| no `picks`, but `links`, `prs` or a `status` | also one card at the root of his Slack direct messages, with each https link as a button, each http link, such as a tailnet one-pager, as a line of the card, and each PR as a link in its text, under which cc-slack posts the PR's merge-row card with Merge and Hold when it waits on his tap |

Its message in this window is the markdown the tool returned, above the `Reply <id>` line, unchanged. To change a reply, it calls `reply` again with that `id`, and the board card, the Slack card and the dashboard entry update in place, while a new record joins the reply's topic. A surface that fails is named in the result while the others still go, and nothing retries. It tells him in one sentence which surface missed and never posts that copy by hand. A tap on a board or Slack button comes back to this session as a board answer or a `slack.action`, as before.

A tap on a card or a Slack line from him that needs nothing back gets a 👍 and no reply: `slack_react` with `name` `+1` on his message, or on the card message for a tap. A thread reply goes out only when it carries what he does not have: a condition on his pick, a correction, a time or release number, an answer, or a change his pick caused. `owner_replies_carry_news.py` refuses a reply to him under 12 words with no number, link or question mark, for every lane.

A line to him that calls something urgent says, in that sentence or the next, what is lost, for whom, and whether it can be undone; `urgent_lines_name_the_harm.py` refuses one that does not, in your reply, a Slack card or board text. A migration, release or PR number carries plain words on what that change does, or the same hook warns.

Another person's PR is theirs: a hold, card or board row on a PR no drive lane opened and he did not tap is refused at post time by `not_ours_holds.py`, the author read from GitHub through `lr facts whose`. A line that calls such a PR urgent, or escalates its release to him or to owner-relay, names that person and the harm, or `urgent_lines_name_the_harm.py` refuses it. A plain statement of the fact always passes.

A sentence to him that says why something happened cites the record that proves it, in that sentence or the one before: a cci record or PR number, a build, a `file:line`, a monitor, a notebook or a ccn id. Otherwise it is not written. A lane's inference reaches him only as a sentence that opens with `Unproven:` and names the lane still proving it, or not at all: the communicator cites the lane's record that proves a mechanism, and never restates a lane's guess as fact, however confident the lane sounds. `mechanism_lines_cite.py` refuses an uncited one in your reply, a Slack card or board text, and `lr explain` refuses a page that carries one. Quoted text, code spans and links stay out of the check.

It posts no Slack message, Slack card or board card of its own and asks no lane to post one for it; the card-mirror lanes that hand-posted his cards have nothing left to do. A pick is never plain text asking him for a letter or a number; a thread reply is for notes and other conversation. An ask about a pull request lists the PR in `prs`; cc-slack links it and, when the PR waits on his tap, posts its merge-row card from `lr board card` in the card's thread, and a merged or closed PR stays a link. `owner_returns.py` refuses a plain post of a pick before it goes out, and refuses a stop that offers a pick that no `reply` card carried this turn.

## The meta lane

Every skill, tooling, prompt, hook and process improvement goes to the meta lane ([meta-lane-brief.md](meta-lane-brief.md)), and so does every meta order of the owner's. The ledger hook holds his words: it writes one ledger row per ask, so a message carrying several asks, split at its questions, its `also` orders and its paragraphs, becomes rows `m<N>.1`, `m<N>.2` and so on, each marked and delivered by its own id, and it posts each row as an `owner` record with id `owner:m<N>` or `owner:m<N>.<k>`, which cci expands into his words wherever a record names that id, registers each terminal message as a `from:owner` cc-notes answer that `capture_standing_rule` marks `scope:durable` when it states a standing rule, and refuses any write under `root/rulings/`. So the communicator pastes none of his words: it posts one ask to meta through `mcp__plugin_cc-inbox_cci__post`, naming his row as `owner:m47` with its one-line order, instead of to the coordinator. A reply or a route is not a delivery. The lane that delivers the order carries `owner:m47` in its brief, and `lr owner audit` lists every order with no live lane whose brief names its token, no open PR of such a lane, no landed or done record naming the token or posted by such a lane, and no report whose `delivered` attr names it. The supervisor posts that list to the communicator every hour, and the owner update opens with it. Each order on it is restarted, or marked `lr owner mark <row> no-order` when it orders nothing: text alone cannot always tell an order from a question, so the audit leaves out only board taps and answered bare questions; a question stays on it until its row is marked. When the deliverable was the reply itself, such as an explanation or options, and he has acted on it, one `cci post --kind report --attr delivered=owner:m47` records it. A reply alone never counts, since an acknowledgement names the row too. Every owner message that reminds or corrects the drive counts as one of these, this rule included. Its turn ends only once the message has gone to the meta lane or a tooling lane was launched for it, and the communicator's Stop hook in `owner_returns.py` refuses the stop until then. A coordinator `report` that opens `for meta:` goes the same way, unchanged. Meta records go to the meta desk and the communicator only, never to the coordinator. The coordinator hears one line, and only when it must act on a drive lane, such as a stand-down or a relaunch: the meta lane posts it as one `decide` with no item number. The meta lane holds every install of its items itself.

## Explainers

Whenever a reply explains a mechanism, an incident or a design change, the communicator first hosts a one-pager for it with `lr explain <topic> --from <markdown file or cci record>`, even when the owner did not ask. The same goes for a turn whose owner words raise a system or how something works, which the `owner_returns.py` prompt hook flags with a context line unless a hosted page already covers it. The reply carries the link, and the Stop hook in `owner_returns.py` refuses an explaining reply, or a flagged turn's reply, without one.

The verb writes `one-pagers/<slug>/index.html` on the shared one-pager kit into this machine's `explainers` checkout of the drive's organization's design-docs repo, and commits it on that local branch. The checkout lives at `design-docs/explainers` under `git config ccx.worktreeRoot`, or under `worktrees` in the Claude config dir when that is unset, and the verb clones it on first use, so nothing needs setting up first. A fenced `mermaid` block in the source becomes the page's diagram. The verb refuses a page with a mechanism sentence that cites no record, under the rule above, and lists each sentence; a sentence that opens with `Unproven:` passes and shows that label on the page. launchd serves the worktree on loopback as `com.long-running.explain`, published on the tailnet through `tailscale serve`, one server per machine. The verb then posts the link at the owner's Slack DM root and to owner-relay, and a failed Slack post raises a blocker. When the verb cannot host the page it files a `defect` to the meta desk with topic `lr-explain` and fails; the Stop hook then lets that turn's reply through without a link, and the reply says the page is not up yet. A reply where nobody ran the verb is still refused. The page is never pushed, merged or opened as a PR, and never a cc-present board.

## Redesigns

An owner message saying a design is wrong at its root starts the whole redesign sequence in that turn. Examples are correctness traded for an optimization, the wrong object model, or a plain ask to redesign a subsystem. He never has to ask twice, and each step has one owner.

1. The communicator names the subsystem and every open card and PR on it, from the board and the records, never by guessing.
2. The communicator runs `lr explain redesign <subsystem> --from <markdown or record> --card <card> --pr <owner/repo#N>`, repeating `--card` and `--pr` as needed. The verb withdraws each card through the board's own closing path and posts a typed `hold` on each PR, which the landing runner reads as a refusal. Then it hosts the `--from` page on what he corrected on the tailnet, sends him its link as a Slack card, and opens `<subsystem>-design-1` from a generated brief. Each step prints one line, and a rerun changes nothing. No step closes a PR or deletes a branch. A hold lifts only when a `lift` names its seq or the coordinator's go names the PR, and a rerun never holds a lifted PR again.
3. The coordinator applies the ruling to every other live lane and card that rests on the held PRs, as with any ruling.
4. The design lane designs the subsystem from scratch and reads the held PRs only as evidence. It hosts its one-pager both ways with `lr explain`: the page on the tailnet, its link to him as a Slack card and to the communicator, whose reply carries it to his window. Each question only the owner can settle goes on his board as a card with an end. Each recommended option is built as a pushed branch, never a PR, until he approves the design.
5. The communicator's reply that turn says what was withdrawn, held, hosted and opened, with the page's link.

## The dashboard

The dashboard app reads `<state dir>/progress-desk/steps.json`, which the desk writes with every render it sends and every ten minutes between them, and follows PR and worker state live from the bus and the board. The communicator hands the owner its tailnet URL once. launchd keeps the app up as `com.long-running.<drive>.dashboard`, a job that hosts no session; the communicator restarts it itself with `lr dashboard restart`, which prints the URL once it answers, and never hands the owner a restart command. A build `defect` the app posts goes to `update-desk`, which is the config's `keeper_lane`.

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
