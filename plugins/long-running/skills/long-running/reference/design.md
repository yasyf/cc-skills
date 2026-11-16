# Design first

A design job is a read-only pass that works a part's shape out on paper before a builder writes
it. Its designer is the one job that runs Fable, besides the coordinator.

## Is it a tricky design?

A design job is worth it when a part has two reasonable shapes that cost a lot to switch between
once built. The usual cases: a data layout or file format, a schema or migration shape (the data
behind a port's visuals included, by [scope](scope.md#the-four-kinds-of-pr)), a lock, queue,
scheduler or other coordination, a public or customer-facing API shape, and a new background
process. You judge it. A shape one later PR can change needs no design job; the builder picks it and
says so in its return.

A visual design, how a page, card or post he sees lays out its information, is its builder's own
work. Brief the builder with the surface on `--native` and his user stories on `--interactions`. It
asks `codex-ask --effort high` for the layout inside its own run and builds the recommended one, so
no builder waits on a job holding its design.

## The design job

- **Add it** with `drive job add design --order <o> --part "<part>" --goal "<one line>"`. On a
  build order outside an incident its agent starts only on a brief carrying the `Researched:` line
  of a reader that kept its block, by [Jobs](jobs.md). While the reader is at work, add it with
  `--waits <the reader's job>` and it starts on its `FREE` line. The order's `immediate fix` job
  does not wait on it.
- **A follow-up** he orders to a PR, as in "just land this then do a follow up", is its own
  `follow-up` part. Its design job starts in the turn he orders it, beside the builder landing that
  PR, with no reader and no `--waits`, which `drive job add` refuses on it. The designer does its
  own research and keeps it as its report's `Researched:` block.
- **Brief it** with
  `drive job brief <j> --serves "<his order>" --done-when "<the DESIGN return>" --where "<repo>" --plan "<what exists>"`,
  plus `--ruling <answer id>` for each answer of his on the area. The `--plan` names the files, PRs
  and findings it starts from. The design follows what his words need; the code that exists is an
  input, not a shape to keep.
- **The work queue starts it.** Once its brief is kept and nothing holds it, it is a design item, and
  the watch starts a design worker for it as its own `claude --bg` session on
  `long-running:designer`, one design at a time. `drive work show` names the worker. Start no
  agent for it yourself.

## What it returns

Its return opens `DESIGN <part>`. `drive job report <j>` prints the rest:

- **The design**, with a worked example of each piece, such as an API's calls, imperative beside
  declarative, or a file format's bytes. A good design makes a bad state impossible by its shape,
  under one durable owner. One that leans on a sweeper, time rule or reclaim loop is worth sending
  back.
- **Its `Researched:` block**, which every builder of its changes starts on. `rulings:` names each
  answer of his on the order's words and area. `exists:` names each table, column, store, file,
  process, mechanism, check or verb that already does or holds what the part touches, by
  `path:line` or link, each ending `extended by change N`, `reused by change N` or
  `deleted by change N`, or `none:` with the searches that found nothing, so one thing owns each
  fact. `open:` lists each question it leaves him. `drive job report` refuses an entry naming no
  change of its list, and a `none` while another reader or design of its part found something.
- **A `Change:` line for each PR** its builders open, in at most 80 characters with no backtick,
  double quote, `$` or backslash. Each becomes a part of the order under those words, with a
  builder the drive adds as the design returns. `drive job report` refuses a later filing that
  renames a change once an agent or lane took its builder, and drops a renamed one nobody took.
- **Its forks for him**, the `ASK` blocks it kept with `drive job ask`, each an `asks him:` line
  under it in `drive status <j>`.

When his words tell the system to choose by a rule, such as by usage or size, the design builds the
abstraction the strategies share first and walks each strategy through it on paper. A fork on it
asks him about the strategies and the rule that picks one, not about one outcome. No check reads
a design for this: a design that builds less or later than his words name, with no fork asking
him, is a defect the designer and the coordinator catch.

## What the designer decides

The designer decides every fork an engineer can decide and writes the choice and its reason into
the design. A fork is his only by the [scope](scope.md) test: product scope, an architecture fork
whose cost lasts, his workflow or money. On a drive tooling order only a high-level preference is
his.

- **Each fork of his is its own question card**, by the `DESIGN <part>` row in
  [The coordinator](coordinator.md), in the card shape [scope](scope.md#a-question-for-him) gives.
  The card holds the design fleshed out above its options. A wall of findings or a vague stand-in
  leaves him asking what it is.
- **The Show me works each option out** to what it looks like once built, such as its real calls in
  code, the rows it writes or its flow, not only its files and cost. It counts toward no word limit; the visual rules are in
  [Messages](messages.md).
- **A design from scratch.** His words ordering one mark its order fresh, as Jev reads them on
  `drive order add` or the first `drive answer` on the order, or with `drive order set <o> --fresh`.
  Every card asked on its work before them is withdrawn in that call, except one holding an open PR
  from landing. The new design asks only its crucial directional decisions, with `--redraws` for an
  old card that still matters. `drive cards list --order <o>` lists the cards still open on a
  feature. A ruling of his that closes a class of questions closes every listed card of that class.

## While he decides

- **The recommended option is built meanwhile.** Its builder starts beside the cards and builds on a
  pushed branch. When his words on the order ask for options only, no builder starts on the proper
  fix until he picks, in an incident too.
- **An immediate fix ships at once.** When a bug hurts someone now, its immediate fix, or a
  rollback when prudent, ships beside the cards on its own `immediate fix` part, on options-only
  orders too. It waits on no card, and the proper fix replaces it when it lands.
- **Built is a state, not a PR.** The builder returns `BUILT <branch>, waits on <cards>` and opens
  no PR. The line sets its job built on that branch, waiting on each card it names by waits id or
  Slack link and on each open card holding its work as the lander reads it, by
  [What a card holds](scope.md#what-a-card-holds). `drive status`, `drive view`, the dashboard and
  his Slack status page show it built, with its branch and each card. An incident's builder is held
  only by a card kept unsettled.
- **He is told once.** The minute a card holds a built job, the watch replies once in that card's
  thread that its recommended answer is built and paused on his pick, and `drive view` puts that
  card first in his queue.
- **His tap frees it.** A built job waits only on his cards. The watch drops each card as he answers
  it and says `FREE <j>` once the last is answered and no question the job asked him is open. Then a
  handoff builder opens the PR, rebuilding first where his pick differs from what was built.
- **PR cards wait too.** While a card holds a PR, `drive pr add`, `drive card` and the watch draw
  no PR card with an Approve for it outside an incident, and a card already up is redrawn without
  its Approve until he answers the last. The watch then says `FREE <pr>`. `drive pr set --waits`
  refuses a card, since a card holds work only by its `Holds:` line or a `DESIGN:` cite.
- **In an incident** the fix's PR opens and lands on the recommended option while the card stays
  open, and his Approve on the repair also picks the recommended option of each card holding it. A
  card kept unsettled, asking him to lift a ruling of his, holds the PR until he answers. Another
  pick of his starts a follow-up job on the same order, by
  [Incidents](incidents.md#his-card-on-an-incident).
- **His order to land a change with a design card still open on it.** When his words settle the
  card, record them with `drive answer` and land. When they do not, name the card in your `report`
  so the communicator redraws it with its buttons in his order's thread.

## The builder carries the design

The drive gives each builder it adds for a change the design on its brief's `Plan:` line. Pass his
answer to each fork as `--ruling <answer id>`, so `Decisions that bind` carries his words.
`drive job brief` also adds every ruling the `rulings:` line of each carried order's reader names,
his answer to each question that reader asked, and each carried order's words. A lane that takes
over or restarts the job starts on the brief rebuilt this way.

A builder that meets a new fork mid-build decides it, builds on and names the choice in its return.
It returns `ASK` only for a fork that is his by the scope test.

`drive pr set --serves` takes `--design none` for a PR whose design decisions were an engineer's,
or `--design asked:<his answer id or card permalink>` for one he picked, and refuses without either.
Its check refuses a permalink to a card he has not answered or a message he did not write.
