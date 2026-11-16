# Scope, and asking him

One test decides what goes to him. A question is his when it is product scope, an architecture fork
with lasting cost, his own workflow, money, or one of the hard limits below. Everything else you
decide, do and report with a count. A wrong reading is fixed by his next word, which costs less
than a card he did not need.

## The four kinds of PR

Say which kind a PR is in the brief's `Serves:` line and the builder's `READY`, so its landing
route is known before it is built.

- **A port**, "port of #N": a change carried between the old and new trees, with #N's behavior and
  visuals and nothing more. Its data is modeled the right way for the target tree, such as a typed
  table where #N kept key-value rows, because copied storage is the part that costs most to undo.
  Its title opens `Port:`. `drive pr set <url> --port-of <N>` sets the `pure port` exemption,
  withdraws its open card and refuses `--tap`. It refuses a PR changing a migration, schema or
  `.proto` file, whose data model is a design part by [Design first](design.md). It refuses an #N
  the drive does not track unless GitHub reads it merged, and a port of a tracked #N that is
  unmerged and without his Approve lands after #N. The lander queues a pure port once green, after
  checking it changes no stored data, and he reads one line in its order's thread, "port of #N
  landed", as for every exempt class in [landing.md](landing.md). When the target tree forces a
  pick that is truly his, the port is not pure: its card says it ports #N and what forces the pick.
- **His order**: work his message or picked option asks for, or work clearly in its spirit. Record
  it with `drive pr set <url> --serves <o> --design none`, or `--design asked:<his answer id>` for a
  design he picked; it prints his words back. Inside it, keep what already exists (a printed form, a
  wire, file or API shape, a default, a command's output) unless the outcome he named needs the
  change. Leave an unneeded change out and name it in your return if it seems worth doing. The
  check before his PR card lists each change the diff makes to what exists, before and after, and
  refuses those his words do not need. An order he limits to options or a summary is delivered by
  its cards, and its fix waits for his pick because he said so.
- **Drive tooling**: a fix to a tool the drive runs on, such as a launcher, the watch, cards or CI
  plumbing, or a rewrite of this skill, by [Tooling](../SKILL.md#tooling). It is the meta session's,
  under its own order ([meta.md](meta.md)), and does not `--serves` a feature order, so a feature's
  thread keeps one subject. Its PR's His words list says none. A `yasyf/*` tooling PR lands with no
  tap. A `<guarded org>/*` one lands under an exempt class, or serves its own order with
  `drive pr set <url> --tap --serves <its Drive tooling order> --design none` and gets a PR card at
  his DM root whose top line quotes the order's "Drive tooling: …" words; his Approve lands it.
- **Neither**: a PR someone other than him opened is the drive's only when his words name it by #N
  or link, in the order or in a card he answered. Otherwise `drive pr add` and `drive pr set` refuse
  it, the watch withdraws its card and drops it with nothing done on GitHub, and the lander and
  `landing_guard` refuse to land it, because another person's work is theirs. Another lane's PR or
  bug report goes back to that lane in a `handoff` record. A teammate's report of a bug an open
  order already fixes adds no work: `drive order set <o> --reporter <its permalink>` keeps it on
  that order so its thread hears the fix too.

## What you do without asking

Everything that is not his by the test above. Do it and report the result with a count: ports,
closes, restarts, CI fixes, cleanups, tool fixes.

- **A finding nobody ordered.** A sweep's, a teammate's or a job's own finding that is clearly in
  the spirit of his order is fixed under that order and reported. One that is a new product
  direction is a question card saying what was found. A job report naming an architecture fork with
  lasting cost, or a limit that costs money to lift, goes to him that turn as a diagnosis with
  option cards.
- **A defect in what the drive shipped or is building** belongs to the order that shipped it, and
  its line names that order.
- **A defect in a tool the drive runs on** is drive tooling, fixed under its own order. Its first
  line goes at his DM root and later lines under it, not in the feature thread it unblocks; a fix
  that posted nothing shows only on the dashboard. His feedback on a post or card is such a defect,
  in the library, `cc-slack check-post` or the drive tool that let it through.
- **A flaky test** on any PR the drive tracks is fixed at its cause the day it flakes, whatever
  files the cause is in. Its PR is a `flake fix` by [landing.md](landing.md), serving his standing
  order to fix flakes, not the order whose PR it reddened.
- **A tooling choice.** Which file, function, store, hook, verb, flag, check or process does it is
  the job's. On a drive tooling order only a high-level preference is his: what reaches him or
  when, a card's shape, a tap he owes, a cost he pays in money or waiting.
- **Which branch, pin or commit holds a fix** is yours on any order. Put the fix wherever it is
  needed.
- **A fact a job can read**, such as an ID, key or URL, is read where the app or infra defines it.
  A card that asked him for one is withdrawn by `drive cards withdraw` with `--via` the done job
  whose report names the card and the value.
- **A repair he calls a one-off** runs once, with no code path, PR or release step.
- **A missing route** to something he picked is built, not carded.

Four limits hold whatever else is true. Name the one that stops you in your return.

1. A dangerous change merges or releases only while he is present, meaning his word or tap in the
   last 15 minutes, because it can break customer runs or lose data with nobody watching. Dangerous
   is what the repository's root AGENTS.md lists, and a production release carrying one. A rollback,
   and an incident's hotfix changing nothing dangerous, run without him.
2. Customer data is not read or touched on this Mac.
3. No session, Orca, terminal host or process of unknown ownership is stopped, signalled, suspended
   or closed, because it may hold someone's live work.
4. Before changing what forward-deployed engineers' or customers' code can observe, run the FDE
   impact scan and put what it finds in the PR body, because their callers break silently.

Two mechanisms also put work in front of him. A `<guarded org>/*` PR outside an exempt class lands
on his Approve tap, by [landing.md](landing.md). A post in his own name to another person waits for
his word; his order to post, a tap included, is that word for the post it names. For a post nobody
ordered, the card asks whether to send it. The fix-live note owed a bug's reporter and the answer to
a question he shared need no card, by [messages.md](messages.md).

## A question for him

He reads only Slack, so a question reaches him as a question card, which `drive job ask` draws as
it keeps the job's `ASK`. Every drive session starts with AskUserQuestion denied
([Start or resume](start.md)), and the `no_terminal_questions` hook refuses it in any session or
agent bound to a drive. The hook also refuses the computer-use access requests (`request_access`,
`request_full_control`, `request_teach_access`), whose dialog would wait in a terminal, and tells a
job to return what it was for as `UNPROVEN: <the check>: <the proof that settles it>`.

A question he has answered is not asked again. When you think he may have ruled on it,
`ccn answer search "<words>"` finds his answer, which often rules past the question it was given
under.

### What `drive job ask` does

- **Settles with no card** on his answer to that same question in the same words, given on or
  under its card or recorded under it. It prints his words and where he gave them. An answer to
  another question settles nothing here, so a question once skipped on one draws its card when asked
  again. An answer he gives later settles the older cards it
  decides through `drive answer`.
- **Decides a tooling detail.** On a drive tooling order, when Opus reads under 0.3 that the
  options differ in what he gets (a post, tap, cost, ruling of his or scope of work), the verb keeps
  the question decided on its recommended option and prints so, and the job builds it. A card
  already drawn still goes to him unless you read it the same way and end it with
  `drive job ask <j> --drop <file> --answer 43aa665`: his card shows "<option>: decided without you
  under your ruling on tooling details" with no buttons. That is refused on any other order and for
  a card kept unsettled. `drive cards withdraw` refuses that ruling's message as its `--via` and
  names this route.
- **Refuses a card that re-asks.** A card whose recommending sentence cites a recorded answer of
  his asks again what that answer chose, so the verb names the option to build with no card, unless
  `--unsettled` names that answer as words the card asks him to lift. A card already drawn for it
  ends with `drive job ask <j> --drop <file> --answer <id>`, which closes it showing his words and
  their link.
- **Reads the card against his words, as advice.** In one request Opus reads the card against his
  words on each order its job works, their threads, the recorded answers the card cites and the
  answers cc-notes ranks nearest. Each reading is a probability: 0.7 or more is a finding, under 0.3
  none, between is printed as unsure. It prints each option his words rule out, each amount or
  choice his words name that the card offers no tap for, and each part that breaks the card rules
  below or what a linked page shows. The card is kept: no card is refused on merits. Weigh a
  finding, and when it holds, rewrite that part and ask again, which redraws the card.
- **Finds an answer that already decides it.** When Opus reads 0.7 or more that a nearby answer
  answers the card, the verb prints it with its id and where he gave it. When his words choose the
  recommended option, build it, name the id on your `DONE` return's `rulings:` line and end a drawn
  card with `--drop <file> --answer <id>`. Otherwise cite the id in the summary, outside the
  recommending sentence, and ask again: the card redraws with his words as its `Already answered`
  tap, so one tap settles it.
- **Draws the `Already answered` option** from the cited answers Opus reads 0.7 or more as deciding
  the question, from an answer the brief binds with `--ruling` or you cite under `Findings:`, and
  from each answer the release gate reads as ordering a release the recommended option names in
  backticks. His tap closes the card on those words. For a release the verb prints that
  `drive job add --release "<release> --order <id>"` starts it
  ([landing.md](landing.md#after-merge)). Such a block keeps at most three options of its own,
  since a card draws four.
- **Keeps a card unsettled** when Opus reads 0.7 or more that the recommended option, or another
  option offered, breaks a recorded answer of his. The card asks him to lift those words, as
  `<id>: Opus reads the recommended option as breaking it` or
  `<id>: Opus reads option "<that option>" as breaking it`, and holds its PR, on an incident too.
  His answer marks the lifted answer superseded in cc-notes, so no brief binds it, and his Undo
  takes the mark back. Nothing is marked when a newer cited answer of his decides the card, when the
  answer only waited for something the card shows has come, or when the card merely offers an
  option beside the one he chose.
- **Draws no card on an order that says to ask nothing**, as "Do this autonomously without asking
  me anything" does. It tells you to build the recommended option and name his words on `RULINGS:`.
  A card asking him to land a PR, release to production or post as him still draws.
- **Keeps a refused block** under `asks/` as the job's pending question with its refusal.
  `drive status` lists it refused, and the watch says the job waits on a card never posted until the
  job asks again, in the same words or others sharing three rare words, or
  `drive job ask <j> --drop <file>` drops it.

Each tap of his on a question card is recorded in cc-notes as `drive answer` records one. A tap
approving a PR settles only that PR's landing.

### The card

`drive job ask` refuses a block out of this shape, and each refusal names its fix.

- **Question and summary.** The question names each thing by its proper term: the function, flag,
  file, service or table ([messages.md](messages.md)). One to three summary lines say what was
  found, what is being decided and the recommendation. Each thing he could open is linked at the
  exact view described (its route, tab, anchor or query string), since the page's root leaves him
  hunting; a view not built yet is called unbuilt.
- **Options.** Two to four, one line each, saying what he gets and what it costs. Under each, one
  plain-text description: what runs and when, where its data lives, what it touches or builds by
  name, and any cost every user pays. The verb refuses an option with no description, field labels
  such as `Does:` in place of one, and a mechanism such as polling left a bare word. No option asks
  a reporter or anyone else to retry or confirm, since that hands them the work.
- **Show me**, when a picture, flow, table or the real code shows what words cannot, drawn whole
  with real contents in every row and cell.
- **`Root cause:`** names the structure that lets the defect happen, with the number that measures
  it, in at most 60 words, since his card draws it under the summary's first line. It is the cause
  his order asks about, such as why a step is slow. Other forms:
  `fixed: <that structure>, removed by <the merged PR's link>` on a card asking only whether to
  release that PR, `not found: <what measures it>, by <the job measuring it>`, and
  `none: <why no defect is behind it>`, as on a pick between his designs.
- **`First principles:`** names the option that removes that structure, so the defect cannot occur,
  through one abstraction every caller goes through. It is the recommended option unless his own
  pick or order chose another. A bound, cap, retry or time budget goes on `Interim fix:` instead.
  **`Simplest:`** names the simplest option, which every card offers beside it.
- **`Interim fix:`**, on a bug hurting someone now, links the immediate fix shipping while he picks
  (its PR, release, rollback or repair), or reads `Interim fix: <that fix>, by <the job building it>`
  or `Interim fix: none: <why nobody is hurt now>`. A reader's `DESIGN` is refused without it too.
- **`Holds:`** names what the card holds from landing: each PR its options alter, by link or `#N`,
  each branch building an option, in backticks by its whole name, and on a reader's or design's
  card each change of the order its options alter, in that change's own words. Or it reads
  `Holds: none: <why>`.

What is built meanwhile is the card's line above its buttons, which the drive reads from its jobs at
each draw. The findings stay in the job's file, and Explain more on the card posts the one section
he asks about.

### What a card says

A card says in plain words what his pick changes for a user. One about a lock, a queue or a wait names which user actions wait and for how long, checked against the code, never a code path.

### What a card holds

An open card holds only what its `Holds:` line names: each PR and each PR of a named branch, and
among the PRs of jobs working a named change, those its design's builder, a job briefed on its
research or a job it names by id carries. A staging tester's card holds the PR of the part it waits
on the same way, and a card a `DESIGN:` line cites holds that PR. A question kept with no `Holds:`
line holds every change of its job. No card holds a flake fix or a main fix, an immediate fix, or,
from a `Drive tooling:` order, a PR of his product.

While a card holds a PR, the lander, the `landing_guard` hook and a `yasyf/*` PR's merge hold it,
and outside an incident's repair the `bash_guard` hook refuses the `ccx vcs ship` that would open
it, on this Mac and on a Sprite. The job builds the recommendation meanwhile on a pushed branch, in
state `built`, by [Design first](design.md). The immediate fix of a bug hurting someone now is not
held: its own `immediate fix` job starts at once and the proper fix replaces it.

## His answer

His answer is the option he tapped, or his own words under or about the card that pick an option
or say what to build. They settle the card, which closes showing them, and the work goes ahead on
his words as written, not on the closest option. When his words order something the options did not
offer, build what removes the root cause within them.

A question of his, such as "could we X?" or "why not just X?", is not a pick, before or after his
tap. Answer it in the card's thread, work X out, offer it on the card redrawn, and he chooses. A
reply that leaves the question open, such as "neither of these" with no other way named, gets a
follow-up on a new card.
