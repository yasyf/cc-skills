# Scope, and asking him

Every PR the drive touches is one of three things, decided before any builder starts on it and
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
  licenses none of the fixes: a fix waits for his pick on its card.
- **Neither**: the drive does not build, polish or card it. Another lane's PR or bug report goes
  back to that lane in a `handoff` record. A sweep's finding, a teammate's report or a job's own
  finding he has not ordered becomes a question card whose summary says what was found, and nothing
  is built before his pick. His ruling, Nov 9 8:42 AM, on a card for another lane's bug report: "Not
  ours drop this".

Two kinds of work are the drive's without a new order, and each says so in its line. A defect in
something the drive shipped or is building belongs to the order that shipped it, and its line
names that order. A defect in a tool the drive runs on is fixed without asking, by
[Tooling](../SKILL.md#tooling). Nothing else is.

Ask only what is his to decide: architecture and abstractions, scope, and his own workflow.
Within his orders, do everything else and report the result with a count: ports, closes,
restarts, backfills, CI fixes, cleanups, tool fixes.

- Before any question, `answer_search` his recorded answers. A question he has answered is
  never asked again, in any wording.
- A trust line waits for him: landing a change in a `Forge-AI/*` repo that is not an exempt
  class ([reference/landing.md](landing.md)), posting as him to anyone else,
  changing production, and stopping or closing sessions he did not name.
- A design question goes to him before anything is built on it, by [Design first](design.md):
  a short card with two to four one-line options, recommendation first, and a picture when the
  shape matters. Nothing is built on the recommendation before his pick.
- A question card is the job's `ASK` as `drive job ask` kept it: the question; a summary of one to
  three lines, what was found and what is being decided, the recommendation, and what happens
  meanwhile; and two to four options, one line each, saying what he gets and what it costs. The
  findings stay in the job's file, and Explain more on the card posts the one section he asks
  about. No separate findings post, ever.
- His answer is the option he tapped, or his own words when he writes one: a written reply that
  names no single option settles nothing, is never mapped to the closest option, and goes back to
  him under the card. No option asks a reporter or anyone else to retry or confirm.
