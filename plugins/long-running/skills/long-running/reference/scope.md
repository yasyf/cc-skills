# Scope, and asking him

## The four kinds of PR

Every PR the drive touches is one of the four kinds below. Decide which before any builder starts,
and say it in the brief's `Serves:` line, the builder's `READY`, its `ready` record, and every
summary line about it, because work stopped before it starts wastes nothing.

- **A port**, "port of #N": a change carried between the old and new trees, with no behavior beyond
  what #N needs in the target tree. Its title opens `Port:`; every line he sees calls it
  "port of #N". The coordinator reads it against #N's diff and records it with
  `drive pr set <url> --port-of <N>`, which sets the `pure port` exemption, withdraws its open card,
  and refuses `--tap`. A handoff builder lands it; he reads one line in its order's thread,
  "port of #N landed", as every exempt class does in [landing.md](landing.md). A port needing his
  pick is not pure: its question card says it ports #N and what in the target tree forces the pick.
- **His order**: his message or picked card option asking for this build, quoted wherever the PR is
  named to him. Record it with `drive pr set <url> --serves <o> --design none`, or
  `--design asked:<his answer id>` for a settled design; it prints his words back. When an order
  asks for cards or a summary, those cards deliver it, and each fix waits for his pick on its card.
  A PR serves an order only if the order's words ask for it, not because jobs it unblocks serve it.
- **Drive tooling**: a fix to a tool the drive runs on, such as a launcher, the watch, cards, or CI
  plumbing, or a rewrite of this skill a ruling of his asks for, by [Tooling](../SKILL.md#tooling).
  It is the communicator's, under its own order with no thread, added with its reader and builder by
  [communicator.md](communicator.md#tooling), and never `--serves` to his feature order, so his
  feature threads carry only their feature. Its PR's His words list says none. A `yasyf/*` tooling
  PR lands with no tap. A `Forge-AI/*` one lands under an exempt class, or serves its own order with
  `drive pr set <url> --tap --serves <its Drive tooling order> --design none` and gets a PR card at
  his DM root. That card's top line quotes the order's "Drive tooling: …" words, and his Approve
  lands it. When a PR card is refused for naming no order of his, take a way the refusal lists.
- **Neither**: the drive does not build, polish, or card it. Another lane's PR or bug report goes
  back to that lane in a `handoff` record. A sweep's finding, a teammate's report, or a job's own
  finding he has not ordered becomes a question card saying what was found, and nothing is built
  before his pick, because he decides what the drive takes on. When a job's report, an incident's
  included, names a limit, a capacity, or an architecture question, take it to him that turn,
  unasked, as a diagnosis with option cards.

## What you do without asking

Two kinds of work are the drive's without a new order, and each line about them says which. A defect
in something the drive shipped or is building belongs to the order that shipped it, and its line
names that order. A defect in a tool the drive runs on is drive tooling, fixed without asking under
its own order, its lines at his DM root, never in a feature thread it unblocks. His feedback on a
post or card is one, in the library, `cc-slack check-post` or drive tool that let it through, every
time. Nothing else is.

Ask him only what is his to decide: architecture and abstractions, scope, and his own workflow.
Within his orders, do everything else and report the result with a count: ports, closes, restarts,
backfills, CI fixes, cleanups, and tool fixes. A repair he calls a one-off runs once, with no code
path, PR, or release step. When he picked something and the route to run it is missing, build the
route instead of carding it or waiting.

Four actions wait for his word: landing a `Forge-AI/*` change that is not an exempt class by
[landing.md](landing.md), posting as him to anyone else, changing production, and stopping or
closing a session he did not name. His order to post, a tap on a card included, is that word for the
one post it names, and nothing asks him to approve it again. A post nobody ordered, such as a reply
to a bug's reporter or a teammate, goes to him as a question card asking whether to send it, never
one asking him to approve a post. The fix-live note the drive owes a bug's reporter in that report's
thread once the fix is live is the one exception, by [messages.md](messages.md).

## A question for him

A question he has answered is never asked again, in any wording. Before you write an `ASK`, read his
words in the order's thread and run `ccn answer search "<words>"`, because his answer often rules
past the question it was given under and names the subject only in his words.

`drive job ask` and the hook on every `ask` record check the part he reads against his recorded
answers and his pick or words on each card the job asked. Each refusal lists, by id and first line,
every answer that may settle it; read each. One that settles it is his answer, and the work goes
ahead on it. One that names what you recommend, such as a service he said to build, settles what
that thing does, so build it and ask nothing. Only when none does, ask with one `--unsettled`
string, or the record's `unsettled` attr, naming every id and why it does not settle this question,
such as `--unsettled "1396ce1: <why>; 729fb9a: <why>"`. His pick or words on this job's own card
answer that card, by [His answer](#his-answer): `drive job ask` refuses an `--unsettled` naming them
while that card still asks him, until `drive answer <id>` settles it with his words, and only once
it is closed does a follow-up name them with why they leave it open. `drive job ask` also reads the
order's own thread and lists each message of his there that shares a rare word with the question,
refusing until `--unsettled` names each by its permalink and why it does not settle this. A tap
approving a PR settles only that PR's landing, so the check never counts an answer labelled
`kind:approval`.

A design question goes to him on a card before any PR carries it, by [Design first](design.md). A
job may build the recommendation meanwhile on a pushed branch, in state `built` in the drive, unless
his words on that order ask for options only; then no builder starts before his pick, because he
wants to choose the fix. Outside an incident's repair, `drive pr add` refuses a PR while its job
waits on an open card of his, and no Approve is drawn while a design card the PR waits on is open.

A question card is the job's `ASK` as `drive job ask` kept it. Its question names each thing by its
proper technical term, the function, flag, file, service, or table, by [messages.md](messages.md).
One to three summary lines say what was found, what is being decided, the recommendation, and what
happens meanwhile, with each thing he could open linked. A Show me, when a picture, flow, table, or
the real code shows what words cannot, is drawn whole with real contents in every row and cell. Two
to four one-line options close it, each saying what runs, on what trigger, what it reads and what
reaches whom, then what he gets and what it costs. `drive job ask` refuses a question or option that
leaves a mechanism such as polling, a push or an event a bare word, as "Polls only" does.

The fix the job's diagnosis points at is always an option, the recommended one unless the summary
names it and says why another is better. The findings' `Direct fix:` line names it, or reads
`Direct fix: none: <why>`, and `drive job ask` refuses a block without one. No option asks a
reporter or anyone else to retry or confirm. The findings stay in the job's file, and Explain more
on the card posts the one section he asks about, never a findings post of its own.

## His answer

His answer is the option he tapped, or his own words under or about the card, a direction included.
They settle the card, which closes showing them, and the work goes ahead on them, never mapped to
the closest option. Only a reply that leaves the question open, such as "neither of these" with no
other way named, gets a follow-up question, on a new card; the card he answered is never redrawn.
