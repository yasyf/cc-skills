# Design first

His words: "when you have a tricky design like this to make, you should 1) spawn a light fable
lane just for the initial design and 2) ask the user early and often". So the designer is the one
job that runs Fable, besides the coordinator.

## Is it a tricky design?

Before any builder starts on a part, decide whether it carries a tricky design: two reasonable
shapes that cost a lot to switch between once built. It counts when the part settles a data layout
or file format, such as where a journal keeps its index, or a schema or migration shape. So does a
lock, queue, scheduler or other coordination, a public or Sand-facing API shape, or a new
background process. When you cannot tell, it counts, because a wrong shape costs PRs he never saw.

1. **One design job, first.** The part's first job is a read-only design job, worked out before
   he is asked; a builder writes the code later.
   - Add it with `drive job add design --order <o> --part "<part>" --goal "<one line>"`. On a build
     order outside an incident it refuses until a reader kept its `Researched:` block, by
     [Jobs](jobs.md).
   - Brief it with `drive job brief <j> --serves "<his order>" --done-when "<the DESIGN return>" --where "<repo>" --plan "<what exists>"`,
     plus `--ruling <answer id>` for each answer of his on the area. The `--plan` names the files,
     PRs and findings it starts from, and says to design from what his words need, the code an
     input, not a shape to keep.
   - Start it with the Agent tool: `subagent_type: "long-running:designer"`, `model: "fable"`,
     `run_in_background: true`, a short `description`, no `name` (the `agent_brief` hook refuses
     one), and the `Read your brief first: <path>` line as the whole `prompt`. The `agent_brief`
     hook refuses a design job's brief on any other type, since only that definition keeps it read
     only, settled questions off his cards and each fork in shape.
   - Its return opens `DESIGN <part>`. Read the rest with `drive job report <j>`: the design, a
     `Settled:` line quoting his words, and a `Fork:` line for each `ASK` block it kept with
     `drive job ask`. Each piece has a worked example, such as an API's calls, imperative beside
     declarative, or a file format's bytes.
   - A design makes a bad state impossible by its shape, under one durable owner. When one leans
     on a sweeper, time rule or reclaim loop instead, send it back with his words: "make getting in
     a bad state unrepresentable; ... not add another sweeper".
   - When his words tell the system to choose by a rule, such as by usage or size, the fork asks
     him to approve the strategies and the rule that picks one, never one outcome.
   - Every option says what runs, on what trigger, what it reads and what reaches the agent or him.
     `drive job ask` refuses a mechanism such as polling left a bare word, a block out of shape, a
     `Direct fix:` line missing or naming no option, and a question a recorded answer of his may
     settle; each refusal names its fix.
2. **He is asked before any PR.** Each fork goes to him as its own question card, by the
   `DESIGN <part>` row in [The coordinator](coordinator.md). The card holds the design fleshed out
   above its options and names each function, file, table, flag or service by its proper term. A wall
   of findings, a high-level line or a vague stand-in leaves him asking what it is.
   - A Show me goes on only when it shows what the words cannot, such as each option's real calls
     in code: "show a sinippet when it makews sense". It counts toward no word limit, so it is never
     cut; the visual rules are in [Messages](messages.md).
   - Nothing his words settle is asked. `drive job ask` catches only recorded answers, so also read
     his threads on the area.
   - Meanwhile a builder may build the recommended option of each fork on a pushed branch, because
     he wants work to go on while he decides. When his words on the order ask for options only,
     start no builder until he picks, in an incident too.
3. **Built is a state, not a PR.** That builder returns `BUILT <branch>, waits on <cards>` and
   opens no PR. The line sets its job built on that branch, waiting on each card's waits id or
   Slack link it names.
   - `drive status`, `drive view`, the dashboard and his Slack status page show it built, with its
     branch and each card. Outside an incident, `drive pr add` refuses a new PR for it while one of
     its cards is open.
   - The minute a card holds a built job, the watch replies once in that card's own thread, in
     plain words, that its recommended answer is built and paused on his pick, and `drive view` puts
     that card first in his queue.
   - A job waits only on his cards, never on another job, so his tap frees it. The watch drops each
     card as he answers it and says `FREE <j>` once, when the last is answered and no question the
     job asked him is open, which wakes the coordinator. Then a handoff builder opens the PR,
     rebuilding first wherever his pick differs from the option it built.
4. **Nothing lands around an open card.** When an open PR carries a design card he has not
   answered, run `drive pr set <url> --waits <card>` for each card, which redraws his open PR card for it in
   place, Approve and Hold kept, to say it lands once he approves and answers that card. While a
   wait holds, `drive pr set --tap` and `drive card` draw no fresh card for it, his Approve lands it
   only once the last wait ends, and the watch says `FREE <pr>` once the last is answered; nothing is
   closed for this.
   - Outside an incident, land, enqueue or decide nothing on a recommended option while his card on
     it is open, because the recommendation is yours and the decision is his.
   - In an incident, by his pick, the fix's PR opens and lands on the recommended option while the
     card stays open, and `drive pr set --waits <card>` refuses on it. Another pick of his starts a
     follow-up job on the same order, by [Incidents](incidents.md#his-card-on-an-incident).
   - His order to land a change does not answer a design card still open on it. Name that card in
     your `report` at once, so the handler redraws it with its buttons in his order's thread, and
     land after he answers, because he wants each card that blocks work surfaced, not skipped.
5. **The builder carries the design.** Its brief carries the design as `--plan` and his answer to
   each fork as `--ruling <answer id>`, so `Decisions that bind` holds his words verbatim. A builder
   that meets a new fork mid-build stops and returns `ASK` with it, because only he picks a fork.
6. **No card for a design he never saw.** A `READY` whose PR makes a design decision he never
   answered goes back as a design question, not onto a PR card. `drive pr set --tap` refuses
   without `--design asked:<his answer id or card permalink>` or `--design none`.
