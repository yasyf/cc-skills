# Design first

His words: "when you have a tricky design like this to make, you should 1) spawn a light fable
lane just for the initial design and 2) ask the user early and often". So the designer is the one
job that runs Fable, besides the coordinator.

## Is it a tricky design?

Before any builder starts on a part, decide whether it carries a tricky design: two reasonable
shapes that cost a lot to switch between once built. It counts when the part settles a data layout
or file format, such as where a journal keeps its index, or a schema or migration shape, the data
behind a port's visuals included: a port carries the old PR's visuals, and its data is modeled the
fundamentally right way for the target tree, never copied from the old PR's storage, by his words
"port his visuals but always do it the fundamentally right way when it comes to modelng the data".
So does a lock, queue, scheduler or other coordination, a public or Sand-facing API shape, or a new
background process. When you cannot tell, it counts, because a wrong shape costs PRs he never saw.
A visual design, how a page, card or post he sees lays out its information, is no designer's: it is
a codex job's, briefed with the surface on `--native` and his user stories on `--interactions`, by his
words "get codex to design and stop doing the bare minimum", and its builder builds that reply.

1. **One design job, first.** The part's first job is a read-only design job, worked out before
   he is asked; a builder writes the code later.
   - Add it with `drive job add design --order <o> --part "<part>" --goal "<one line>"`. On a build
     order outside an incident it refuses until a reader kept its `Researched:` block, by
     [Jobs](jobs.md), so while the reader is at work add it with `--waits <the reader's job>`,
     recorded waiting, and start it on its `FREE` line; the order's `immediate fix` job never waits
     on it. A follow-up he orders to a PR, as in "just land this then do a follow up", is its own
     `follow-up` part, and its design job starts in the turn he orders it, beside the builder landing
     that PR, with no reader and no `--waits`, which `drive job add` refuses on it: its designer does
     its own research. His words: "start deisgnng the follow p right awya motherfucker dont wait".
   - Brief it with `drive job brief <j> --serves "<his order>" --done-when "<the DESIGN return>" --where "<repo>" --plan "<what exists>"`,
     plus `--ruling <answer id>` for each answer of his on the area. The `--plan` names the files,
     PRs and findings it starts from, and says to design from what his words need, the code an
     input, not a shape to keep.
   - Start it with the Agent tool: `subagent_type: "long-running:designer"`, `model: "fable"`,
     `run_in_background: true`, a short `description`, no `name` (the `agent_brief` hook drops
     one), and the `Read your brief first: <path>` line as the whole `prompt`. The `agent_brief`
     hook starts a design job's brief given any other type as `long-running:designer`, since only
     that definition keeps it read only, settled questions off his cards and each fork in shape.
   - Its return opens `DESIGN <part>`. Read the rest with `drive job report <j>`: the design, a
     `Settled:` line quoting his words, an `Exists:` line naming each table, column, store, file,
     process, mechanism, check or verb that already does or holds what the part touches, by
     `path:line` or link, each ending `extended by change N`, `reused by change N` or
     `deleted by change N`, or `none:` with the searches that found nothing, so one thing owns each
     fact; `drive job report` refuses an entry naming no change of its list, and a `none` while the
     order's reader found something. Then a `Change:` line for each PR its builders open, in at most 80
     characters with no backtick, double quote, `$` or backslash, which becomes a part of the order
     under those words with a builder of its own the drive adds as the design returns. Its forks are the `ASK` blocks it kept with `drive job ask`, each an
     `asks him:` line under it in `drive status <j>`. Each piece has a worked example, such as an API's calls, imperative beside
     declarative, or a file format's bytes.
   - A design makes a bad state impossible by its shape, under one durable owner. When one leans
     on a sweeper, time rule or reclaim loop instead, send it back with his words: "make getting in
     a bad state unrepresentable; ... not add another sweeper".
   - When his words tell the system to choose by a rule, such as by usage or size, the fork asks
     him to approve the strategies and the rule that picks one, never one outcome. The design then
     builds the abstraction those strategies share first, walks each through it on paper, and
     leaves none he approved for later without his card. `drive job report` refuses a design Jev
     reads as building less or later than his words name with no fork asking him, or as building a
     strategy before the abstraction it shares.
   - Every option carries one plain-text description his card draws under it, saying in prose what
     the option is: what runs and when, where its data lives, and what it requires touching or
     building, by name, with any cost every user pays, such as SandSQL's write path for every
     table. His words: "you need to always fucking explain each optopn properly as prose in additon tot he table or whatever and what it requires touching/building" and "this does/data/toches is not as helpful as each option just having a plain-text shot concise but clear and comprehensive description of what it is".
     `drive job ask` refuses an option with no description or with fields such as `Does:` in place of one, a mechanism such as polling left a bare word, a block out of shape, a
     `Root cause:` line missing, or its `First principles:` option missing or not recommended, a `Simplest:` line missing or naming no option, and a question a recorded answer of his may
     settle; each refusal names its fix.
2. **He is asked before any PR.** Each fork goes to him as its own question card, by the
   `DESIGN <part>` row in [The coordinator](coordinator.md). The card holds the design fleshed out
   above its options, each option's plain-text description under it, and names each function, file, table,
   flag or service by its proper term. A wall
   of findings, a high-level line or a vague stand-in leaves him asking what it is.
   - The Show me works each option out to what it will look like once built, such as its real calls
     in code, the rows it writes or its flow, never only its files and cost: "you need to flesh out what that will look like not just wave your hands, likewise for 2 and 3".
     `drive job ask` prints each option Jev reads as left unworked, naming it. Beyond that it shows
     only what the words cannot: "show a sinippet when it makews sense". It counts toward no word
     limit, so it is never cut; the visual rules are in [Messages](messages.md).
   - Nothing his words settle is asked. `drive job ask` settles only on his answer to the same
     question, and Jev reads each card against his order, its thread and the recorded answers of
     his `ccn answer match` ranks nearest to the block, so also read his answers on the area.
   - Meanwhile the recommended option's builder starts beside the cards and builds it on a pushed
     branch, because he wants work to go on while he decides. When his words on the order ask for
     options only, start no builder on the proper fix until he picks, in an incident too.
   - When a bug hurts someone now, its immediate fix, or a rollback when prudent, ships at once
     beside the cards on its own `immediate fix` part, options-only orders too, and the proper fix
     replaces it when it lands. His words: "always do the immediate fix (or rollback if prudent) in
     parallel to giving me options for the proper fix (which should replace the hacky fix, and as
     always you can optimistically build the recommended option on a branch)".
3. **Built is a state, not a PR.** That builder returns `BUILT <branch>, waits on <cards>` and
   opens no PR. The line sets its job built on that branch, waiting on each card's waits id or
   Slack link it names and on each open card of his holding its work as the lander reads it, its
   reader's included whatever words the line names it in, and none whose `Holds:` line reads none.
   - `drive status`, `drive view`, the dashboard and his Slack status page show it built, with its
     branch and each card. Outside an incident, the `bash_guard` hook refuses the `ccx vcs ship` that
     would open its PR while one of its cards is open, and on an incident while a card kept
     unsettled, asking him to lift a ruling of his, is open. An immediate fix's job is never held: its PR goes up at once.
   - The minute a card holds a built job, the watch replies once in that card's own thread, in
     plain words, that its recommended answer is built and paused on his pick, and `drive view` puts
     that card first in his queue.
   - A built job waits only on his cards, never on another job, so his tap frees it. The watch drops each
     card as he answers it and says `FREE <j>` once, when the last is answered and no question the
     job asked him is open, which wakes the coordinator. Then a handoff builder opens the PR,
     rebuilding first wherever his pick differs from the option it built.
4. **Nothing lands around an open card.** Work does not land with a question of his on it
   unanswered: the lander, the `landing_guard` hook, `drive pr add` and a `yasyf/*` PR's merge each
   refuse while any card of his bears on its work: one whose question's `Holds:` line names a change
   its job builds, the design job's included, since a card holds work only by its `Holds:` line and
   a card a job waits on otherwise only wakes it. His open PR card names each card holding the PR, and
   says his Approve also picks that card's recommended option, which answers and closes the card, or,
   for a card recommending none, that it lands once he answers it, so `drive pr set --waits` refuses a card. While a
   wait holds, `drive pr set --serves` and `drive card` draw no fresh card for it, his Approve lands it
   only once the last wait ends, and the watch says `FREE <pr>` once the last is answered; nothing is
   closed for this.
   - Outside an incident, land, enqueue or decide nothing on a recommended option while his card on
     it is open, because the recommendation is yours and the decision is his. The immediate fix is no
     recommended option: it stops harm now, waits on none of his cards, and his pick replaces it.
   - In an incident, by his pick, the fix's PR opens and lands on the recommended option while the
     card stays open, unless the card asks him to lift a ruling of his, one an option it offers
     breaks, which `drive job ask` marks and which holds the PR from its `Holds:` line until he
     answers, by his pick "Hold the PR until you answer; the drive finds each card against your
     recorded answers". Another pick of his starts a
     follow-up job on the same order, by [Incidents](incidents.md#his-card-on-an-incident).
   - His order to land a change does not answer a design card still open on it. Name that card in
     your `report` at once, so the handler redraws it with its buttons in his order's thread, and
     land after he answers, because he wants each card that blocks work surfaced, not skipped.
5. **The builder carries the design.** Its brief carries the design on its `Plan:` line, which the
   drive gives each builder it adds for a change, and his answer to each fork as
   `--ruling <answer id>`, so `Decisions that bind` holds his words verbatim, those above the
   answer's `Notes:` and never the drive's reading of them. `drive job brief` adds every
   ruling the `rulings:` line of each carried order's reader names, his answer to each question
   that reader asked, and each carried order's words, so no brief reads `none named` over a ruling
   on its area; a lane that takes over or restarts the job starts on the brief rebuilt this way. A builder
   that meets a new fork mid-build stops and returns `ASK` with it, because only he picks a fork.
6. **No card for a design he never saw.** A `READY` whose PR makes a design decision he never
   answered goes back as a design question, not onto a PR card. `drive pr set --serves` refuses
   without `--design asked:<his answer id or card permalink>` or `--design none`, and its check
   refuses a permalink to a card he has not answered or a message he did not write.
