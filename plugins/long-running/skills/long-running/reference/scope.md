# Scope, and asking him

Every PR the drive touches is one of four things, decided before any builder starts on it and
said in its brief's `Serves` line, its builder's `READY`, its `ready` record and every summary
line about it. His ruling, Nov 9 9:10 PM, under the tap card of a PR that was neither: "Is this a
port? If so you need to indicate it as such and doesn't need a tap, if not why are we doing
this? It's not in scope for us."

- **A port**, "port of #N": a change carried from the old tree into the new one, or back, with no
  behaviour of its own beyond what the target tree needs to run #N's. Its title opens `Port:`,
  and it is called "port of #N" wherever he sees it. It needs no tap and never draws a tap card:
  the coordinator reads it against #N's diff, records it with `drive pr set <url> --port-of <N>`,
  which sets the `pure port` exemption, a handoff builder lands it, and he reads one line in its
  order's thread, "port of #N landed". Every exempt class lands the same way, by
  [reference/landing.md](landing.md).
- **His order**: the message of his, or the card option he picked, that asks for this build,
  quoted wherever the PR is named to him. `drive pr set <url> --tap --serves <o>` records it and
  prints his words back. An order that asks for cards or a summary, such as one to summarize each
  issue in a channel with its own card and options for a fix, is delivered by those cards and
  licenses none of the fixes: a fix waits for his pick on its card. A PR is registered under an
  order only when that order's words ask for it, never because the jobs it unblocks serve it.
- **Drive tooling**: a fix to a tool the drive runs on, such as a launcher, the watch, cards or
  CI plumbing, by [Tooling](../SKILL.md#tooling). It is an order of its own, `drive order add
  "Drive tooling: <what it fixes>" --from cci:<seq>` with no `--thread`, never a job of his feature
  order and never `--serves` to it, and its PR's His words list says none. A `yasyf/*` PR lands
  with no tap. A `Forge-AI/*` one lands under an exempt class, or serves its own order with `drive
  pr set <url> --tap --serves <its Drive tooling order> --design none` and gets a PR card at his DM
  root like any other. The line above the card quotes the order's "Drive tooling: …" words, and his
  Approve lands it with nothing done by hand. A card refusal saying no order of his names it is
  answered that way, never by writing his words for another change into its body. His ruling, Nov
  10 3:21 AM, on a Sprite launcher fix carded in his journal thread as journal work: "What the
  fuck? Why are you talking about sprites and dev tooling for run journals? This makes no sense at
  all?"
- **Neither**: the drive does not build, polish or card it. Another lane's PR or bug report goes
  back to that lane in a `handoff` record. A sweep's finding, a teammate's report or a job's own
  finding he has not ordered becomes a question card whose summary says what was found, and nothing
  is built before his pick. His ruling, Nov 9 8:42 AM, on a card for another lane's bug report: "Not
  ours drop this".

Two kinds of work are the drive's without a new order of his, and each says so in its line. A
defect in something the drive shipped or is building belongs to the order that shipped it, and
its line names that order. A defect in a tool the drive runs on is drive tooling, fixed without
asking under its own order, and its lines go at his DM root, never in the thread of a feature it
unblocks. Nothing else is.

Ask only what is his to decide: architecture and abstractions, scope, and his own workflow.
Within his orders, do everything else and report the result with a count: ports, closes,
restarts, backfills, CI fixes, cleanups, tool fixes.

- A question he has answered is never asked again, in any wording. `drive job ask` and every `ask`
  record check it twice: the part he reads against every recorded answer of his, and the whole
  block, findings included, against the drive's own answers, nearest first, since his answer often
  rules past the question it was given under and names the subject only in his words. Each refuses
  with every answer that may settle it, by id and first line, the same list each time for the same
  question. Read each: one that settles it is his answer, and the work goes ahead on it. One that
  already names what you would recommend, such as a service he said to build, settles what that
  thing does: build it, and ask nothing. Only when none does, the ask goes out with one
  `--unsettled` string, or the record's `unsettled` attr, naming every id and why that answer does
  not settle this question, such as `--unsettled "1396ce1: <why>; 729fb9a: <why>"`. His ruling,
  Nov 10 9:46 PM, on a card asking what may delete a file from a team's shared folder, whose own
  findings quoted his earlier answer naming the service: "I alrady told you to build this service
  as `ssql-blobstore` and move all the blob logic from al over into there only".
- A trust line waits for his word: landing a change in a `Forge-AI/*` repo that is not an exempt
  class ([reference/landing.md](landing.md)), posting as him to anyone else,
  changing production, and stopping or closing sessions he did not name. His order to post, in his
  DM, in a terminal or by a tap on a card, is that word for the one post it names, and nothing asks
  him to approve it again. A post nobody ordered, such as a reply offered to someone who reported a
  bug or a line for a teammate, is a question card asking whether to tell them, never a card asking
  him to approve a post.
- A design question goes to him before any PR carries it, by [Design first](design.md): a card
  with the design fleshed out, its Show me in code above two to four one-line options,
  recommendation first, and a picture when the shape matters. A job may build the recommendation
  meanwhile on a pushed branch, built in the ledger, but puts up no PR and draws no Approve until
  he has answered every card it waits on.
- A question card is the job's `ASK` as `drive job ask` kept it. It holds the question; a summary
  of one to three lines, what was found and what is being decided, the recommendation, and what
  happens meanwhile, with each thing it names that he could open linked; a Show me, the worked
  example drawn whole as code, with real contents in every row and cell; and two to four options,
  one line each, saying what he gets and what it costs. Each option lands releasable alone, by
  [Land a change](landing.md#every-pr): no option lands a change first and leaves a fill, backfill,
  pass or command for after its release, and `drive job ask` refuses one that does. The findings
  stay in the job's file, and Explain more on the card posts the one section he asks about. No
  separate findings post, ever.
- His answer is the option he tapped, or his own words when he writes one: a written reply that
  names no single option settles nothing, is never mapped to the closest option, and goes back to
  him under the card. No option asks a reporter or anyone else to retry or confirm.
