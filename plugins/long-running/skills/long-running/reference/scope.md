# Scope, and asking him

## The four kinds of PR

Every PR the drive touches is one of the four kinds below. Decide which before any builder starts,
and say it in the brief's `Serves:` line, the builder's `READY`, and every
summary line about it, because work stopped before it starts wastes nothing.

- **A port**, "port of #N": a change carried between the old and new trees, with no behavior beyond
  what #N needs in the target tree. It carries #N's behavior and visuals, and its data is modeled the
  fundamentally right way for the target tree, never copied from #N's storage, such as key-value rows
  where the target tree keeps a typed table, by his words "port his visuals but always do it the
  fundamentally right way when it comes to modelng the data". That data model is a design part of the
  order, settled on his card by [Design first](design.md). Its title opens `Port:`; every line he sees
  calls it "port of #N". The coordinator reads it against #N's diff and records it with
  `drive pr set <url> --port-of <N>`, which sets the `pure port` exemption, withdraws its open card,
  and refuses `--tap`, and refuses a PR changing a migration, schema or `.proto` file, whose data model
  is that design part. The drive's lander queues it once green, after checking it changes no stored
  data; he reads one line in its order's thread,
  "port of #N landed", as every exempt class does in [landing.md](landing.md). A port needing his
  pick is not pure: its question card says it ports #N and what in the target tree forces the pick.
- **His order**: his message or picked card option asking for this build, quoted wherever the PR is
  named to him. Record it with `drive pr set <url> --serves <o> --design none`, or
  `--design asked:<his answer id>` for a settled design; it prints his words back. When an order
  asks for cards or a summary, those cards deliver it, and each fix waits for his pick on its card.
  A PR serves an order only if the order's words ask for it, not because jobs it unblocks serve it,
  so the fix of a test that flaked on its PR is his order by his ruling 532cd3d, never by the order
  whose PR it reddened.
  Inside it, what already exists, a value's printed form, a wire, file or API shape, a default, a
  command's output or a behavior, stays as it was unless his words name the new one or the outcome
  they name, the problem they describe included, cannot happen without it; any other change to it
  is his pick on a card, by his words "stop overrecahing and changing things people didnt ask you
  when you are asked to make a change, put that int eh skill". The check before his card lists each
  change the diff makes to what exists, before and after, and refuses each his words do not need.
- **Drive tooling**: a fix to a tool the drive runs on, such as a launcher, the watch, cards, or CI
  plumbing, or a rewrite of this skill a ruling of his asks for, by [Tooling](../SKILL.md#tooling).
  It is the meta session's, under its own order in the thread his words ordering it came in, else
  none, added with its reader and builder by
  [meta.md](meta.md), and never `--serves` to his feature order, so his
  feature threads carry only their feature. Its PR's His words list says none. A `yasyf/*` tooling
  PR lands with no tap. A `Forge-AI/*` one lands under an exempt class, or serves its own order with
  `drive pr set <url> --tap --serves <its Drive tooling order> --design none` and gets a PR card at
  his DM root. That card's top line quotes the order's "Drive tooling: …" words, and his Approve
  lands it.
- **Neither**: the drive does not build, polish, or card it. A PR someone other than him opened on
  GitHub is the drive's only when his words name it by #N or link: the order's, or a question card a
  job of that order drew that links it and that he answered, by his ruling 5815316 on a teammate's PRs,
  "Don't touch old tree or his PRs." Otherwise `drive pr add` and `drive pr set`
  refuse it, the watch withdraws its card and drops it with nothing done on GitHub, and the lander
  and `landing_guard` refuse landing it. Another lane's PR or bug report goes back to that lane in a
  `handoff` record. A sweep's finding, a teammate's report, or a job's own
  finding he has not ordered becomes a question card saying what was found, and nothing is built
  before his pick, because he decides what the drive takes on. A teammate's report a reader reads as
  the bug an open order already fixes adds no work: `drive order set <o> --reporter <its permalink>`
  keeps it on that order with no card, so that thread hears the fix too. A test that flaked is never that
  finding: his ruling 532cd3d already ordered its fix. When a job's report, an incident's
  included, names a limit, a capacity, or an architecture question, take it to him that turn,
  unasked, as a diagnosis with option cards.

## What you do without asking

Three kinds of work are the drive's without a new order, and each line about them says which. A defect
in something the drive shipped or is building belongs to the order that shipped it, and its line
names that order. A defect in a tool the drive runs on is drive tooling, fixed without asking under
its own order, its first line at his DM root and its later lines under it, never in a feature
thread it unblocks. His feedback on a post or card is one, in the library, `cc-slack check-post` or
drive tool that let it through, every time. A test that flakes on any PR the drive tracks is fixed at its cause the day it flakes, whatever
files the cause is in, by his ruling 532cd3d, "and ffs fix these flaky tgests how many times do I have
to tell yoiu": its PR is a `flake fix` by [landing.md](landing.md), serving that ruling, judged against
it, and its lines name it. Nothing else is.

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
thread once the fix is live, and the answer to a question someone asked him that he shares or links,
which his share orders, are the two exceptions, by [messages.md](messages.md).

## A question for him

A question for him reaches him only as a question card, which `drive job ask` draws as it keeps
the job's `ASK`, because he reads no terminal. Every drive session and handler starts with
the AskUserQuestion tool denied ([Start or resume](start.md)), the `no_terminal_questions` hook
refuses the tool in any session bound to a drive, the agents it starts and one started before that
deny included, and a job asks through its `ASK` return, so no question waits in a terminal. The same
hook refuses there each computer-use request for screen-control access (`request_access`,
`request_full_control`, `request_teach_access`), whose approval dialog waits in that terminal too,
and tells a job to return what it was for as `UNPROVEN: <the check>: <the proof that settles it>`.

A question he has answered is never asked again, in any wording. Before you write an `ASK`, read his
words in the order's thread and run `ccn answer search "<words>"`, because his answer often rules
past the question it was given under and names the subject only in his words.

`drive job ask` keeps a question settled with no card only on his answer to that same question,
asked in the same words, case and spacing aside. That answer is his pick or words on or under the
card it was asked on, or his words recorded under it. The tool prints his words and where he gave
them. On a pick, build that option. On an order, build the option his words leave that removes the
root cause, the recommended one.

A card whose summary sentence recommending an option cites a recorded answer of his asks again what
that answer chose, so `drive job ask` refuses it and names that option to build with no card, unless
`--unsettled` names that answer as words the card asks him to lift. A card's Already answered option
carries only the recorded answers its summary cites, his words above the `Options:` line its recorder
lists the choices under, so his one tap gives its job one answer's words.

It reads no answer he gave another question, by his pick "Settle only on your answer to that same
question, copies only in the same words: no wrong skips; each skip below becomes a card." A question
once kept settled with no card on another answer of his draws its card when its job asks it again.
An answer he gives later still settles the older cards it decides, through `drive answer`.

Every other question Jev reads, in one call, against his words on the order and his messages about
it in its thread, without the lines he quoted from an alert or another's message. It prints to the
agent each option they rule out and each amount or choice they name that the card offers no tap
for, by his words "You didn't give me a tap for 30 day bonding how many times do I have to say this",
naming each part and the words it breaks, and keeps the card, by his pick 320b7a7 "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits".
When a finding holds, the agent rewrites that part and asks the same question again, which redraws
its card. When Jev reads the card's recommended option as breaking a recorded answer or ruling of his,
at least 0.8 sure, the cited answer the card says it goes against first, the verb keeps the card unsettled on it, as
`<id>: Jev reads the recommended option as breaking it`, asking him to lift those words, so it holds
its PR on an incident too until he answers. A cited answer the release gate reads as ordering a release
the recommended option names in backticks is his order carried out, never words to lift: the verb
prints that `drive job add --release` starts that release with no card, by one reading of his pick
that both share ([landing.md](landing.md#after-merge)). A tap approving a PR settles only that PR's landing. A block a refusal stops
stays kept under `asks/` as the job's pending question with its refusal. No card draws from it,
and `drive status` lists it refused. The watch says its job waits on a
card never posted until the job asks it again, in its words or others sharing three rare words, or
`drive job ask <j> --drop <file>` drops it.

Cite by its id under `Findings:` each recorded answer of his you find that bears on the question.
When the job's brief binds one with `--ruling`, or the block cites one, his card adds an `Already
answered` option described by his words, linked to where he gave them. His tap on it closes the
card on those words, which reach the job to build as any answer of his does.
Such a block keeps at most three options of its own, since a card draws four.

A design question goes to him on a card before any PR carries it, by [Design first](design.md). A
job builds the recommendation meanwhile on a pushed branch, in state `built` in the drive, unless
his words on that order ask for options only; then no builder starts on the proper fix before his
pick, because he wants to choose the fix. Work does not land with a question of his on it
unanswered: outside an incident's repair, the `bash_guard` hook refuses the `ccx vcs ship` that would
open a proper-fix PR, on this Mac and on a Sprite, and the lander,
the `landing_guard` hook and a `yasyf/*` PR's merge each hold one, while any open card of his bears
on its work: one whose question's `Holds:` line names a change its job builds, the design it builds
included. The immediate fix of a bug hurting someone now is never held: its own
`immediate fix` job starts at once, options-only orders too, its PR waits on none of his cards, and
the proper fix replaces it.

A question card is the job's `ASK` as `drive job ask` kept it. Its question names each thing by its
proper technical term, the function, flag, file, service, or table, by [messages.md](messages.md).
One to three summary lines say what was found, what is being decided, the recommendation, and what
happens meanwhile, with each thing he could open linked at the exact view they describe: a screen a
route, tab, anchor or query string reaches is linked at that address, never the page's root, and a
view not built yet is called unbuilt, by his words "I dont see tthe slack tab on that link what are you talking abit?". A Show me, when a picture, flow, table, or
the real code shows what words cannot, is drawn whole with real contents in every row and cell. Two
to four one-line options close it, each saying what he gets and what it costs, with one plain-text
description drawn under it saying what the option is: what runs and when, where its data lives, and
what it requires touching or building, by name, with any cost every user pays, by his words
"you need to always fucking explain each optopn properly as prose in additon tot he table or whatever and what it requires touching/building" and "this does/data/toches is not as helpful as each option just having a plain-text shot concise but clear and comprehensive description of what it is".
`drive job ask` refuses an option with no description, and a question or option that leaves a
mechanism such as polling, a push or an event a bare word, as "Polls only" does.

A question card starts from the root cause, the structure that lets the defect happen, and
recommends the option that removes it so the defect cannot occur, through one abstraction every
caller goes through, unless his own pick or order chooses another, which beats this preference; an
option keeping it behind a cap, timer, retry, exit, flag, pin or sweeper is the recommended one only
when his pick chose it. The findings' `Root cause:` line names that structure and `First
principles:` that option, or `Root cause:` reads `fixed: <that structure>, removed by <the merged
PR's link>` on a card asking only whether to release that PR, `not found: <the job finding it>`, or
`none: <why no defect is behind it>`, as on a pick between his designs or his approval of an action
his rules hold for him, such as a benchmark run; `drive job ask` refuses a block without them. In the
call that reads his words, Jev weighs each option by his words, and the card, its recommended option
and its `Root cause:`, `First principles:` and `Simplest:` lines, by the part of this rule its `Root cause:`
form names and by the words each page it links shows once Chrome opens it against what the card says
that page shows. This rule speaks only to the recommended option, never one offered beside it such as
the `Simplest:` one every card offers. Each option or part Jev is at least 0.8 sure breaks his words,
this rule or a page's is printed to the agent, naming the part and the words it breaks, and refuses no
card, by his pick 320b7a7 "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits". His ruling: "Update your skill to not just brush over the root cause ever". On a bug hurting
someone now, an incident's or one a reporter shared, the findings' `Interim fix:` line links the
immediate fix shipping while he picks (its PR, release, rollback, repair, or the job building it),
or reads `Interim fix: none: <why nobody is hurt now>`; `drive job ask` and a reader's `DESIGN` are
refused without it. The findings' `Holds:` line names, in each change's own words, every change of the
order its options alter, or reads `Holds: none: <why>`, and his open card holds from landing,
among the PRs of jobs working a change it names, only those its own job, its design's builder or a job briefed on its research carries and those it names by link, `#N` or their job's id, and a question kept with no `Holds:` line holds every change of those jobs; no card holds a flake fix. No option asks a
reporter or anyone else to retry or confirm. The findings stay in the job's file, and Explain more
on the card posts the one section he asks about, never a findings post of its own.

## His answer

His answer is the option he tapped, or his own words under or about the card, a direction included,
and so is a question proposing a way, such as "why not just X?", before or after his tap: X is
built in place of any option he tapped.
They settle the card, which closes showing them, and the work goes ahead on them, never mapped to
the closest option. Only a reply that leaves the question open, such as "neither of these" with no
other way named, gets a follow-up question, on a new card; the card he answered is never redrawn.
