# Messages to the owner

Only the communicator's side writes to him, from his own messages and from the `report`
records the coordinator sends, and on the question cards `drive job ask` draws. The communicator posts the ack alone in its first message, with `say`, and
nothing else. Its handlers write, check and post every other post, one handler at a time per message of his or
per thread: the coordinator's record carries the facts, the handler writes the words, and a handler
answers his read-only questions itself. Every post is short, says what changed for him, and carries
the card with its buttons whenever it says something waits on him.

## Contents

- [Limits](#limits)
- [The short form](#the-short-form)
- [The shapes](#the-shapes)
- [When it goes out](#when-it-goes-out)
- [What goes in](#what-goes-in)
- [What stays out](#what-stays-out)
- [Examples](#examples)
- [Before it goes out](#before-it-goes-out)

## Limits

The tooling holds every post to a length, counted as the post goes out with a link counted as the
words of its label, so a `report` record names no cap of its own. The limits are the coordinator's
reading of his words "your messages need to be way shorter rewrite this for a human".

| What he reads | At most |
|---|---|
| A line in his thread | 60 words |
| A summary of two or more features, each under its own bold line | 120 words |
| Each row of a markdown table, which `say` draws as Slack's own table | 60 words, its words outside the post's count |
| The count of his open cards and the cards asked since he was last heard, which `say` adds to his catch-up | none: drawn whole, outside the 120 |
| A question card: question, summary and options together | 150 words |
| A question card's Show me, the worked example between its summary and options | none: drawn whole as its visuals, outside the 150 |
| Each option on a card | one line of 25 words, saying what he gets and what it costs |
| Each option's plain-text description, drawn under it on the card | 60 words, drawn whole outside the 150 |
| A findings post | none, ever: findings stay on disk |
| An answer to a question or order of his in that thread, his words quoted as `asked` | no limit: the whole answer goes out |
| An edit that rewords a line already posted | that line's own length and 10 words more, or the line's limit when that is longer |

- The message-limits hook refuses a post or edit in his DM over its limit, from the communicator or
  any handler, naming the count, the limit and what would pass. `drive job ask` refuses an `ASK`
  whose card runs over its limit, or that cc-slack refuses to draw, in cc-slack's own words.
- Text passed beside a question card is drawn atop its body and counts toward its 150 words, so post
  the card with no text beside it.
- A post that answers a question or order of his, a ruling he gave, or his tap on Explain more goes
  out whole at any length, because a cut answer loses what he asked. To lift the limit, pass as
  `asked` each message of his the post answers, word for word, one per line or list entry (a list
  sent as a JSON string counts as its entries), with `…` where you skip words, or the message's
  permalink in his DM as its entry. A post outside the thread his message is in, such as a share he
  ordered from his thread to another person's group DM, passes that permalink, since the thread it
  goes to holds no word of his.
- The hook looks for each quote in his messages in the thread the post goes to, in the answers of his
  its cards there show, in his Explain more tap, which a card there shows on cc-slack's "You asked for
  more" line so a reply passes `asked="Explain more"`, and in each message of his in his DM an `asked`
  permalink links, read from Slack, with straight and curly quote marks alike; one found quote is
  enough. The handler's brief
  and the record it was started with are other agents' words, never his, whatever they quote. A
  refusal says which limit applied and why: no `asked`, so not an answer; quotes it could not find,
  named; or an edit's own length.
- An edit that rewords a posted line may grow it by 10 words, so a correction can add the proper
  terms without the cap cutting what the line says.
- A question card keeps its 150 words whatever it quotes, and its Show me counts toward none of them,
  so never cut a Show me to fit.

## The short form

- Lead with what changed for him, in his words, never with how the machinery got there.
- Write a bold line and at most two sentences; use a table only for three or more parallel items.
- Keep every card and line in a feature's thread about that feature. Post drive tooling, such as a
  fix to the launcher its builders start from, in the thread his words ordering it came in, else at
  his DM root under its own words, never in a feature's thread it only unblocks, because a tooling
  line he did not ask for there makes no sense to him.
- The coordinator sends one `report` per thread per turn, opening `POST` or `FYI`, because four
  posts of 60 words are the same wall as one of 240; `record_lines` refuses a second, or a `POST`
  past 60 words, 120 for a summary. A turn runs from a wake, whether a cci record, a task
  notification or his message, to the end of that response. The handler writes the post from its
  facts.
- The report's first sentence says what changed for him, and no cut takes it. What does not fit
  waits for the next turn, each item that waits on him kept first with its card's permalink or
  `waits` id, which `record_lines` refuses a report without, so the handler never searches.
- When a line names a card or says something waits on him, name the card as `{card:<its waits id>}`
  in `say`'s text, which `say` links under words that name it. Slack then draws the card with its
  buttons under the line, so he answers it where it sits. In that card's own thread `say` writes it
  as the words after its `|` or as "this card", with no link, and cc-slack writes any link to a
  thread's own parent as its words, since he is already under the card. His words: "why are you
  showing me a card in the thread of the same post that this is a thread of".
- Never paste the bare URL, which shows him a raw address, use the URL as its own label,
  `<permalink|permalink>`, which Slack never draws, or copy the card's words. `cc-slack check-post`
  refuses a waiting line with no open card linked, and a card link shown as a raw URL.
- Slack draws a message's first five Slack-message and alert links, and none it drew in the same
  thread a moment before. Put each card on its own line: cc-slack sends a post to his DM that links
  more than five as pages, each cut at the last blank line before the line holding its sixth, so
  every card draws ("if you're telling me to tap something you need to include the unfurled card
  here"), and refuses one line linking six.
- A line that waits on him and links a PR draws that PR's tap card, as `tap_prs` does. When the PR
  draws no card, such as one merged or one the card command skips, `say` is refused and nothing goes
  out, so name a PR he has nothing to tap in a line that waits on nothing.
- Only cards have buttons, so anything that asks for his action or decision is a card: "Anything that
  says waiting on me you always need to embed the card with the buttons".
- When a post in an order's thread promises him his next line by a clock time, such as "Next line
  by 9:45", the post's record (`say` or `drive order posted`) makes that time the order's
  due from the words Slack holds for the post, a time with no AM or PM read as the one nearest the
  post, and any other time in it, such as "due by 2:37", sets nothing, so the
  watch holds the drive to it from Slack's own record, not a session's memory, and no `--due` follows
  the line. `drive order add --due` counts from when the order is added, and `drive order set --due`
  from the next line in the order's thread. The watch says `DUE` every 10
  minutes until a drive post is in that thread, and the order cannot close before one is. An open
  incident keeps no such time: the watch owes its next line five quiet minutes after its last once it has news, by
  [Clocks](incidents.md#clocks).
- The handler records each post on an order through the post itself,
  `say(record=<the report's seq>)`, on the order the report is about, in whatever thread it goes, which delivers each part
  the report's `part` attr names, or each part of an order its `close` attr names, whatever the
  post's words say, and makes a thread the order holds not yet one of its threads. No post delivers
  work he ordered, every part of a build order or an incident and a part of an order of no kind
  that a builder, codex or design job holds or held or a design lists, unless
  `drive order set <o> --kind answer --part "<part>"` sorted it as an answer, since only a
  builder or codex job returning `DONE`, or a merged PR, does, so `record_lines` refuses a report naming one
  on an order it does not close. A progress
  line's report names neither, whatever job it names, and carries `progress=true`, since
  `record_lines` refuses a `POST` naming none of the three: it keeps the time and delivers no part,
  on an order of one part as on one of several. An incident and every alert's order joined to it
  with `--joins` share their threads both ways, whichever job carries the alert.
  `drive order posted <o> <link> --record <the report's seq>` records a post already made. Only the communicator's side records a post, except that the session the order's `ORPHAN` lines reach marks a part a post
  of the drive already answered, or work a merged PR delivered, with `drive order posted <o> <that post's or PR's link> --part "<part>"`, with or without
  `--record`, posting nothing.

## The shapes

**An ack.**

- Post it in the thread of his message, alone in the communicator's first message, inside 5 to 10
  seconds; cc-slack put the eyes on his message when it landed.
- Write one sentence the communicator already knows: what he asked, in his words, and what happens
  next; a hook refuses a longer ack. Never read, draft or check for it, because any of those delays it.
- Restate only what his words carry, adding no reach, condition or outcome he did not say, because
  jobs downstream treat the ack as his words; mark any reading of your own as yours, beside his words.
- The ack names no time: the coordinator sets the order's due time, and the first summary says it.

**A summary.** A handler writes it from a `POST` report and posts it with
`say(text=…, thread_ts=<his thread>)`: what changed for him since the last summary, grouped by
feature. Each feature gets one bold line and at most two sentences under it, 120 words in all, and a
summary of one feature is a line of at most 60 words.

```
*<feature>: <what is true now, for him, in under 12 words>*
<one or two sentences: what it means and what happens next, by when>
```

**A question card.**

- `drive job ask` draws it in the call that keeps the job's `ASK`, from the file it kept, with
  nothing posted before or after it.
- Its title is its question, one sentence of plain words that names what it decides by its proper
  terms, the function, flag, file, service or table, never a stand-in such as "the copy change".
  `drive job ask` refuses a title naming its object only as "the change", "the fix" or "this" while
  its summary or findings name the proper terms.
- Slack draws a card's title and buttons as plain text, so a link or markdown there shows raw and
  `drive job ask` refuses it; put the link in the summary.
- Its body is its summary, one to three lines: what was found and what is being decided, the
  recommendation, and what happens meanwhile, naming the immediate fix or rollback already shipping
  when a bug hurts someone now. The first line says what the card is: the work it serves, as
  `{order:<o>}`, which his card draws as his order's own words linked to its thread, `port of #N` as
  the text of that PR's link, or Drive tooling, and what his pick changes for him, said as "your
  pick", such as "For {order:o518}: your pick decides whether restoring a SandSQL Env to an earlier
  point keeps its moved tables readable once the reader switch ships." `drive job ask` refuses a first
  line missing either, by his words on a card that opened on its findings: "what is this and what the
  fuck are you talking about". It also counts the words his card draws for each reference in the
  summary: an `{order:<o>}` draws its order's first 100 characters, so give it a few words of its own,
  `{order:<o>|<a few words>}`, when the card runs near 150. When the block has a Show me, a `*Show me*` line
  and the Show me follow as written.
- The card starts from the root cause, the structure that lets the defect happen, and one option is
  always the one that removes it so the defect cannot occur, recommended, every other option saying
  it keeps that structure and what that costs. The recommended option is the block's first, which `drive job ask` refuses otherwise. `drive job ask` refuses a block whose findings carry no `Simplest:` line
  naming by its words the option that is the simplest design doing his words, by his words "in geernal keeps implifying all this tuff", or no `Root cause:` line
  with a `First principles:` line naming that option by its words, `Root cause: fixed: <the structure>,
  removed by <the merged PR's link>` on a card asking only whether to release that PR, `Root cause: not
  found: <the job finding it>` or `Root cause: none: <why>`, as on his approval of an action his rules
  hold for him (his ruling "Update your skill to not just brush over the root cause ever"), and, on a bug hurting someone now, an incident's
  or one a reporter shared, one whose findings carry no `Interim fix:` line linking the immediate fix
  shipping while he picks, or `Interim fix: none: <why nobody is hurt now>`.
- The findings carry a `Holds:` line naming, in each change's own words, every change of the order
  its options alter, a part of the order or a design's `Change:` line, or `Holds: none: <why>`; his
  open card holds from landing, among the PRs of jobs working a change it names, only those its own job, its design's builder or a job briefed on its research carries and those it names by link, `#N` or their job's id, by his pick "Each
  question names the changes it holds, kept with it". `drive job ask` refuses a block without it.
- Give two to four options, each one line of at most 25 words saying what he gets and what it
  costs; `drive job ask` refuses any other count or length. cc-slack adds Other, which takes his own
  words, and Explain more to every card that waits on him, and refuses an option that repeats either.
- Explain each option on the card in one plain-text description under it, indented two spaces in
  the block, which `drive job card` passes as the card's `hints` and cc-slack draws under that
  option. In at most 60 words of prose, short but clear and whole, it says what the option is: what
  runs and on what trigger, where its data lives and how it is written and read, and what it
  requires touching or building, by name, with any cost every user pays, such as SandSQL's write
  path for every table. A table or Show me never stands in for it. His words, on a card whose
  options never said one changed SandSQL: "you need to always fucking explain each optopn properly as prose in additon tot he table or whatever and what it requires touching/building"; on the next card: "where is that data stored
  then and how"; and on the `Does:`, `Data:` and `Touches:` lines that first carried it:
  "this does/data/toches is not as helpful as each option just having a plain-text shot concise but clear and comprehensive description of what it is". `drive job ask` refuses an option with no description, more than one line under it, a
  field label such as `Does:` in it, or one over 60 words. The card draws each file name and path in
  its body and descriptions as code, so Slack links no `clone.rs` as a website; the title and
  options take no markup, so a file name goes in the description.
- Name each mechanism by what it does, such as "the Mac reruns the check function every 30 seconds
  and compares its output; a change sends one inbox message that wakes the agent". After its first
  line, the summary says this of what every option shares, and the title asks only what the options differ
  on. `drive job ask` refuses a title or option that leaves polling, a push, a subscription, a
  notification, a listener or an event a bare word, as "Polls only", "a Poll source" and "the event
  system" do, because he cannot tell what is polled and what wakes whom: "That wasn't clear from the
  card, polling is fine for seeing when the function output changed". A Sand name such as
  `event::on` is a proper term and passes.
- Write each option to stand alone: cc-slack draws the recommended option first and numbers the rest
  after it, so the numbers in an `ASK` block are not the ones he sees. An option that builds on
  another repeats what that one does, such as "Own process plus a memory cap per script".
  `cc-slack check-post` and `drive job ask` refuse "as 1", "same as 2" or "the first option".
- The findings stay in the job's file. Explain more on the card posts the one section its question
  rests on, in plain words, and that is the only way findings reach him. Each option's description
  is on the card, never only in the findings.

Its Show me:

- A card that offers a design to build, a fix included, works each option out in its Show me to
  what it will look like once built: its real calls, the rows or bytes it writes, its flow, or the
  reply or screen a caller gets, as a ```compare panel named with the option's words, a markdown
  table, or a diagram or picture captioned with its number, never only the files it touches and its
  cost. `drive job ask` asks Jev of each option whether the card works it out and prints each one left
  unworked, naming it, refusing no card, by his words on a card whose options each named files and a cost:
  "you need to flesh out what that will look like not just wave your hands, likewise for 2 and 3"; and his ruling 77b577b,
  "flash this out wayu more and od that for everyhtrfing in the future". A post in the card's
  thread explaining an option, an Explain more reply included, shows it the same way, which the say
  judge holds it to.
- Any other card, such as one asking whether or when to release, land or run something, includes a
  Show me only when it shows him what the summary and options cannot. That is the flow
  between the parts the options change, the numbers they turn on, the options side by side, or the
  real calls of an API choice. A constant, a name, a value or a call the words already say is no Show me, and a
  card with nothing that adds clarity has none.
- Fill every row and cell with real contents, measured or worked out from the code and marked as
  such, never the names of what will be measured later; `drive job ask` refuses a table with one
  value in every cell, and a table of one column, which compares nothing: give each field a column
  of its own.
- Each visual is one fence whose language names its kind, and one render layer,
  `lib/drive/site/visuals.js`, draws that one source for each surface. Slack gets a picture of it,
  since a Slack message takes no code or diagram blocks, except a chart, which Slack draws as its own
  [chart block](https://docs.slack.dev/reference/block-kit/blocks/data-visualization-block). Code gets
  a picture only when Jev reads it as illustrating a shape the words cannot, such as a function, a type
  or a query, by his pick 29865ea, "drop the ones where the code is not illustrative or is just one
  line or something": a one-line fence, a value flipped such as `write = 0` to `write = 1`, or an
  argument or list item dropped shows as Slack's code block under its caption. The
  dashboard and other web pages draw each kind's source natively, as highlighted code, a live diagram or chart, or a compare, never as a picture. A
  markdown table is native on both: a Slack data table and an HTML table.
- Pick the kind that shows what the words cannot:

| The question turns on | Kind | Fence holds |
|---|---|---|
| the shape of an API, call, query or schema the options change | code | the real calls, in a fence naming its language: ```sand, ```sql, ```go |
| components, data layout or flow between them | arch | `{"arch": 1, "name", "title", "nodes": [{"id", "kind", "name", "role"}], "edges": [{"from", "to", "label"}]}`: each id lowercase letters, digits and dashes; kind actor, app, service, worker, store, log, external, artifact or doc; a store or log node adds "medium", one of memory, disk, bucket, keys or pages; an edge may add "kind", one of sync, async, copy or control. `drive pr add` draws it and refuses one that does not draw |
| routing, ownership, steps over time, a state machine, a small graph | mermaid | Mermaid source |
| a graph whose layout carries the point, such as clusters of what runs where | dot | Graphviz source |
| numbers: sizes, counts, timings, before and after | chart | a Vega-Lite JSON spec with its data inline: a bar, line or area mark, or an arc for a pie, which Slack draws as its own chart |
| a tradeoff between options | compare | `{"options": [{"name", "recommended", "language", "code", "points": ["+ a gain", "- a cost"]}]}`, drawn side by side |
| words compared across rows | table | a markdown table, which cc-slack draws as a native Slack table |
| a draft message or a quote | quote | its sentences, each line opening with `> `, which Slack shows as its own quote, links and all |
| a UI or anything only a picture shows | image | `![what it shows](/absolute/path.png)` on a line of its own, a screenshot or a gen-image or codex imagegen picture; options that differ in how a screen looks carry one real screenshot each, captioned `N · …` |

- Put a caption line of at most 25 words just above each fence. `drive job card` makes it the
  picture's title, as `say` does with a post's `visuals` caption: it replaces the fence with
  `![caption](/abs.png)`, which cc-slack posts as a Block Kit image block, so the words stay text and
  the picture holds only the visual. A fence with no caption line is titled by its kind, such as
  "go code" or "Diagram", never a bare "Show me".
- When the Show me draws two or more pictures, Slack shows them side by side as one gallery titled by
  their captions, so open each caption with its option's number as the card numbers them, the
  recommended option 1 and the rest in their order, and a middle dot, as `1 · Feed first`.
- Leave a ```chart as its fence on the card, which cc-slack draws as Slack's chart. Its title is at
  most 50 characters and each label at most 20; it draws at most 12 series or slices and 20
  categories, and a card holds at most 2 charts. Each row's value is a number, above 0 in a pie, each
  label is text or a number, and each series holds exactly one row for each category.
- Draw a Mermaid flow whose longest chain runs past four nodes top to bottom, `flowchart TD`, because
  drawn left to right it shrinks its words to about 5 px on his phone.
- Name the code's language on its fence, because the picture highlights the code in that language,
  Sand by its own grammar.
- Split a mixed visual into its parts: the diagram in its own fence, and each table as a markdown
  table, never one picture made from both.
- Never draw text as a picture. A draft message or a quote shows as Slack's quote of its sentences,
  and `drive job card` draws a fence holding one the same way, links and all.
- Keep other words and raw data out of fences. `drive job ask` refuses:
  - a fence naming no language or `text` that holds no message or quote, and one holding JSON, YAML, logs, a terminal session,
    an HTTP exchange, command output or a stack trace, whatever language it names, as a hook does `say`'s `code` visuals;
  - a chart, compare or arch whose JSON does not parse;
  - a chart past Slack's limits above, or more than 2, naming the limit and the value;
  - a markdown table of one column;
  - two or more pictures whose captions do not each open with an option's number;
  - a `flowchart LR` or `graph LR` whose longest chain runs past four nodes;
  - a fence of one or two lines declaring a constant;
  - as many code fences as options, which go in one `compare` fence labeled per option;
  - a picture kept in the drive's asks or pictures folder, drawn by hand instead of from a fence, which a before and an
    after screenshot kept there is not;
  - two or more PRs listed by number, which their stack's PR card shows;
  - a `Show me:` with no visual under it, and a picture inside a sentence or a table cell;
  - options Jev reads as differing in how a screen looks, such as where rows draw in a sidebar, with
    no `![N · …](path)` screenshot for each option, by his ruling 0d2b7df, "this should have
    included images; you include images when they are usless and dont add anyhtng, and then dont
    when its things like this, its totlly backwards".
- List every member of a set the card counts. `drive job ask`, `drive job card` and the post hook
  refuse a question, summary or option that counts two to twelve things, as "four strategies" or
  "5 oversized tables" does, unless the card lists that many. It lists them as options, Show me
  table rows or columns, list lines, numbered captions, pictures or fences, or by name right after
  the count, as "four strategies: spill values, insertion order, hash by key, and equal cut" does.
  A measure such as "30 days", "3 requests" or "2 threads" and a bound such as "at most 3" pass.
- Give him a tap for each amount he named. Jev reads the card against his order and his messages in
  its thread, without the lines he quoted from others, and `drive job ask` prints to the agent each
  amount or choice Jev reads them as naming that the card offers no tap for, as "30 day bonding"
  names 30 days, and keeps the card, by his pick 320b7a7. His message in a thread two orders share is about the order it started, else the one that
  came into the thread last before he sent it, so another order's amount there is not counted. Offer it
  as its own option, in his words.
- A visual that still fails to draw leaves the Slack card with its caption and nothing in its place,
  and the dashboard with its source as text.

Posting it:

- `drive job ask` posts the card itself, in the same call that keeps the question, as `drive pr add`
  posts a PR card, and the watch posts on its next read each kept question whose card did not draw
  then. No session posts a question card, and none is put together by hand.
- A kept ASK file changes only through `drive job ask`, which checks the new words against every
  rule. `drive job card` and `drive cards redraw` refuse a kept file whose words no longer hash to its
  name, naming the `drive job ask <j> <file>` that keeps them. A question already asked under a waits
  id redraws under the rules it was asked under; once its file's words changed, it redraws from his
  card as cc-slack stores it, and once cc-slack forgot that card, from the file only while its
  question, summary, recommended option and options are the ones his card's message in Slack shows.
  A block no job kept meets every rule a new block meets before it draws.
- cc-slack posts a card that names its drive and waits on him at his DM root, and posts the card's
  permalink under its question in the thread of the job's order, or of its open incident, when that
  thread is in his DM. An incident's thread outside his DM, such as an alert's, gets the card's line through a
  report record `drive job ask` sends the communicator, whose handler posts it there, since only a
  `say` call carries the ticket cc-slack spends on a write outside his DM. That record goes once for
  each change to the line: its cci id names the card's `waits` id and the line's hash, so a redraw
  that leaves the line as it was returns the record cci already holds and starts no handler. His
  tap comes back as an `answer` record, so "card posted" and "he answered" stay two states.
- Every question card carries its `waits` id, `<drive>:<job>-<n>`, which `drive job ask` mints when
  it first keeps the question. One question keeps one `waits` id, which every copy of it in Slack and
  the dashboard draws from: `drive job ask` keeps a block asking an open question again as that
  question and redraws its card where it sits under that `waits` id; every other open card of
  the job asking the same shows asked again with a link to the live one and no buttons, and each
  wait on it moves to the live one; his answer closes every copy at once. A question that ended draws under no
  `waits` id: `drive job card` refuses it, naming the words it ended on. `drive job ask` refuses a block another
  job's open card asks. When a job builds the recommended option meanwhile, the summary says which
  option is being built, and names the immediate fix or rollback already shipping.
- Set `drive` to the drive's name on every card, so cc-slack reads a `waits` card as the drive's
  question, whose home is Slack, and posts it with no cc-present board warning.

When it is answered, replaced or redone:

- His words written under a card answer it, a question proposing a way such as "why not just X?"
  included, which X answers: the card closes showing them, and the work goes ahead on them. Only a
  question asking for facts or options is answered in its thread while the card waits. `drive job card` refuses to redraw that card with another question, so put a question his
  words leave open on a new card.
- When an answer or ruling he gave elsewhere decides a card, `drive answer` lists it, Jev reading
  his answer against every open card in one call, and `--settled` shows his words and a link to them
  in place where its buttons were, so a late tap records nothing. A card it decides only in part is
  redrawn under its `waits` id to the ASK its job keeps asking only what is left, linking his answer,
  by `drive job ask <j> <file> --redraws <key>`, then marked with `--settled <key>=<file>`. His words, on a card a later decision of his had settled: "didnt we
  alrady say to move all those to lazy? we need to update tehse old cards based on future desicions".
- To change the words of a card he has not answered, draw it again under its `waits` id. That one
  call edits the card where it sits to the new words, buttons kept, and posts nothing, and every
  older copy loses its buttons and reads one line linking the card, so one card stays answerable.
  `say` points every job's or PR's wait on an older copy at the card, the copies read from his open
  cards before the post; when his cards cannot be read, nothing is posted. `drive cards redraw`
  draws every open question of the drive again the same way, over the card where it sits even once cc-slack
  forgot it. Never edit a card with
  `say(edit=…)` or `slack_edit`: an edit with text alone replaces its layout and buttons with plain text, and a hook refuses
  it, naming the redraw.
- Words of his that supersede a card he has not answered, wherever he writes them, withdraw it the
  moment they are recorded: words that answer its subject, change its question or options, such as
  more options or a new constraint, or send its work back to be redone. `drive cards withdraw <its
  waits id> --words "<his words>" --via <his permalink>` settles it in place, showing his words and a
  link to them with no buttons, so a late tap records nothing, and its question, asked again on any
  job once the work is back, keeps that card's waits id and redraws it. An event that leaves a
  card's tap nothing to pick, such as a teammate lifting a freeze so the deploy the card would hold
  applies, withdraws it the moment the drive reads it: `drive cards withdraw <its waits id> --words
  "<what happened>" --via <the PR, build, job or message link>` settles it showing what happened,
  never recorded as his. A card still worth a tap while its work is reworked keeps its buttons. One question has one card. `drive job ask` refuses a question another job's
  open card asks in the same words, naming the `drive job set --waits` that frees its job at his
  answer. `drive answer` lists every open card
  his answer decides, rules options out of or makes moot, whatever job, order or words it shares with
  the one he answered. His words, on three cards about one search
  worker's cache disk: "We're already addressing this with the lazy + restate object + increase disk size right? So why is this card still here?"
- No option asks a reporter or anyone else to retry, confirm or check, because the drive proves a fix
  itself, and `drive job ask` refuses an option asking someone to retry or confirm.

**A PR card.**

- Nobody writes it: `drive pr add` draws it at his DM root the moment it registers a PR waiting on
  his tap, and the watch draws it on its next read for a PR still `uncarded`, whatever its checks,
  review or check against his words say, since each is a line on it. Its draws and `drive card` group PRs
  by one rule, `state.card_unit`: one card per stack his Approve lands, a native GitHub stack or PRs
  each on the one below's head branch, and for PRs in none, one per repository and order they serve,
  so sibling PRs of one order ready together draw one card whose Approve covers each, however many it
  holds, titled as that many PRs of one request and never called a stack, by his pick "Keep one card
  per request, as you picked; its title and wording stop calling siblings a stack. One Approve still
  lands both.", the PR sections past the 50 blocks one Slack message holds under its Explain more. It is only
  for a PR that
  carries out an order of his whose words ask for it, or a `Forge-AI/*` drive tooling fix no exempt
  class covers, which serves its own order by [Scope](scope.md).
- The drive's own draw posts the card at his DM root, in no order's thread, under one line written
  from its PRs' titles with no link, so Slack draws no other card: `Waits on your Approve: <what
  each PR does>`. While its PRs wait on him and fix a bug hurting someone now, the watch asks again
  every 30 minutes, once per card, under `Asking again, since it fixes a reported bug: <what each PR
  does>`, or `an open incident`. A flake fix's line adds that it fixes a flaking test at its cause,
  quoting his ruling. Before it posts, `cc-slack check-post` reads the line, and Jev judges anything
  it finds by the plain-words rule in one call. The order's thread hears one line when the PR merges. A card drawn through `say` carries a
  line above it quoting that order: his words, or the tooling order's "Drive tooling: …" words. The say hook finds a PR card whose PR serves a drive tooling order while
  its text leaves out that order's words, naming them, for Jev to judge. Those words pass
  `cc-slack check-post`, which `drive order add` and `drive order set <o> --words` run on them. A
  port never gets one: he reads "port of #N landed" in its order's thread once it lands, and so for
  every class that lands with no tap.
- `say(tap_prs=[<each url or owner/repo#number, bottom first>], text=<line>)` makes cc-slack draw
  one card at his DM root for the stack those PRs form (`drive card`), with Approve and Hold while it
  waits on him. The drive posts the line in the thread of each open order those PRs carry out, the
  one each serves, its own and each its job carries, in his DM, and cc-slack puts the card's
  permalink under it there, so whichever thread he reads carries its buttons; with none, the line
  goes at his DM root. An incident whose thread is outside his DM, such as an alert's, gets the line with
  the card's link through `cc-slack reply`, so the call names that thread with `channel_id` and
  `thread_ts`, the one place cc-slack's Slack permission hook judges it for, and is refused without
  them, naming both. His words: "There's no approval button". The call's `card_permalink` names
  the card and its `permalink` only the first line. When the card command gives a PR no card, the call fails with its reason
  and posts nothing.
- The same PRs show under the line on the dashboard's history, each with where it stands now. A post
  naming a PR the drive does not hold is refused: `drive pr add` records it first.
- His Approve lands exactly those PRs still asking his tap, bottom first. His Hold holds them all. A
  card posts once a PR on it asks his tap, and shows what holds it, such as a card of his it waits
  on, but never while one is being reworked to his words. A PR added later gets its own card when posted
  alone. Posted with the rest of its stack, it joins one new card for them all, and cc-slack marks
  each older card of those PRs replaced, with a link to the new one.
- The card opens with each PR's lead, the four lines its builder gave `drive pr add`, before any
  code: its headline as the card's title, then why the change is made, what it does and what it
  changes downstream, on the dashboard's card above the stack's rows too, since his words on a card
  of facts were "I have no idea why were doing this change, what it does, or what the downstream
  impacts of it are". A stack card is titled by its `## The stack` line, else its bottom PR's
  headline, and lists each PR's headline and lead above the stack's summary.
- Each PR gets its own section: its title, link and Open button, then its Summary of what it
  changes, its Refresher, its biggest surprise and what he will hate most. Where it stands and what it waits on follow only when that is
  not green and his tap, and how it differs from the plan only when it does. A section its body lacks
  is left off and holds nothing, since a body is fixed beside the landing, never before it. A card
  already posted redraws in place on every change with what holds it now. A stack card whose other
  PRs still ask his tap keeps Approve and Hold for them, and a held PR's section says why. Every section opens with its PR's stage as `stage.of` names it, such as "Its checks fail at
  1a2b3c4: lint". A head that moved under his card keeps Approve showing, and a head the check against
  his words judges against them keeps Approve and any Approve he gave, the objection drawn whole under
  Where it stands, since his Approve lands it whatever that check says, by his words "why the fuck would you keep eveyhting broken to make it perfect". While a PR
  fixes a bug hurting someone now, a card that waits on him is posted again every 30 minutes until it lands. Its Show me comes last,
  its pictures inline. A stack card's summary comes from `## The stack` in its bottom PR, under its PRs' leads.
- Once he answers, the card folds to its title, his pick and one line per PR. When an answer or ruling
  of his replaces the work it carries, `drive answer` settles it as it does a question card.
- His words that rework a PR on it take the card down in the same call once the coordinator, having
  read the stack's `## His words, step by step` against them, names the PR: `--rework <PR>` on
  `drive order add`, `drive order set` or `drive job set --carries`, or `drive answer --reopened`, each
  withdrawing it with his words as the reason. His order filed on its thread, or a job carrying one,
  with no `--rework` names the card and leaves it up, since a stack can meet his words already. The
  PRs below the lowest reworked one go up on a card of their own, so a PR his answer leaves standing
  asks his tap at once, and his Approve on it never reaches a PR above it. No
  redraw or new head brings the reworked PR back; a fresh card goes up on its own, with a row saying
  what changed, only once the builder's `READY` says in its `CHANGE:` line how his words are met, and that
  `READY` drops any tap he gave before the rework, so the fresh card asks his Approve again. A
  question or any other words of his under it leave it up. His words: "So why isn't this card withdrawn then? And why do I have to keep asking that?"
- Its words come from each PR's title and body, with record ids and the HTML tags GitHub draws
  dropped, so text such as `agent::of("<id>")` reaches the card whole; fix a bad card in the PR
  body, never in the message.

**A confirmation of a skill update** goes out twice for each message of his that leads to one. The
ack says the skill is being updated and what changes, in plain words; a summary then says it is
merged and installed, listing each rule now in the skill.

**A fix-live note in a bug report's thread.**

- The drive owes it to whoever reported a bug the drive fixes, in the thread of the report, the moment
  the fix is live. His words, the day a hand repair went live and the reporter never heard: "Did you
  let him know it's fixed? … I told him since you never did, fix that". The order keeps that thread
  from the share or link in his words, `drive order add --reporter`, or by itself when he gives the
  order in that thread, whose lines then go to his DM. A second report of the same bug, such as
  another teammate's, joins it with `drive order set --reporter`, beside the first, so each thread
  hears its own note, and `drive status` says on the order's line whether each thread heard.
- A question someone asked that a `--kind answer` order answers, kept the same way, gets the answer
  there once every part of it is posted to him, with no proof asked, since an answer has no fix to
  go live. His share of the question orders it: the handler writes the answer again for that person
  in the same step as his own line, and no card asks him and no line says it waits on him to
  forward, by his words "we were supposed to send that whas left for cloud update to … and never did".
- Post it once per thread, once the coordinator keeps the proof the fix is live with `--proof`, never
  before: one or two plain sentences saying what is fixed and since when, with the link. The handler
  of the report on that order posts it with `slack_reply` in the same step as his own line, then
  records it with `drive order told`. When the bot cannot post in that thread, as in a person's 1:1
  DM with him, the handler posts the same note as usual in the group DM of that person, him and the
  bot, with one `say` whose `channel_id` is the person's user id, which cc-slack's refusal names,
  and no `thread_ts`, and records it with `drive order told --group-dm <that user id>`. His words: "Dm him in group
  with bot then as usual".
- The drive holds it to once and to after the fix: `drive order set --state done` refuses an order
  with a reporter until each note is recorded, the watch says `TELL` once when the proof sat 10 minutes
  with no note, and the post hook refuses a second note in that thread and one before the proof.
  The dashboard's top strip counts each bug report until every note is recorded, and its list says
  the stage each reached: reported, being fixed, merged, live and not told, or told. His words: "we
  should have an equivalent bade gfor bug reports that tracks all teh way trhough to letting the
  user know its live and fixed".
- It needs no card and no approval, because he granted it standing: "we have authorization to message
  once in bug fix threads after we ship the fix … it needs to be in plain simple English no jargon, no
  crazy shit, and no asks".
- Leave out drive jargon such as job or record ids, and asks such as "please retry", "please confirm"
  or "let us know". Post nothing else in that thread: no progress, no test results, no follow-up.

```
Fixed: Iris picks up again right after you answer its question, on today's build.
```

**The status page.** You never write it: the drive's watch redraws the drive's canvas, a tab in his
DM with the app, within a minute of any change, and `drive view` prints the same page. It is native
Slack tables under a line linking the dashboard. A strip counts what waits on him and each state.
Tables follow for his questions, his PR taps, promised times that passed, every project's count by
state, each open state's features, and finished work. Each table shows its first eight rows and says
how many more the dashboard holds. When a line says where things stand, link the page instead of
restating it.

## When it goes out

- His message gets its ack inside 5 to 10 seconds, whatever else is running; no handler brief,
  record, report, card, due line, draft or `check-post` holds the ack. The handler the communicator
  starts answers in that thread when it is done.
- While the communicator compacts, cc-slack posts "Got it, back in a minute." under each of his
  messages itself, and the message arrives with `acked`, so it gets no second ack.
- Each later step of an order with a thread is the watch's to say there, by his pick "A handler
  posts each step, started by the watch on news": a job returning its finding, a PR going up with his
  card, getting a new head, its checks settling, Trunk ejecting it from the merge queue or taking his
  tap, and a card of his opening or closing. A PR's stage relabelled with none of those changed, such
  as red to behind once a PR below it in its stack goes red, is no step, nor is a release or answer ending one wait of a job that still
  waits on another. Once five minutes have passed since the thread's last line or the
  watch's last report on it, the watch sends one `report` carrying `quiet=true` and the thread,
  naming every step that moved in that time, and starts one handler, which posts what changed in that
  thread, so no session writes a progress line of its own and a thread lined minutes before gets none. None goes out while he is away, since his catch-up
  carries it, nor once a report closing the order is kept or posted, whose post is its line.
- An incident shares that rule with a clock: until its fix is live he hears each step the moment it
  happens, and until its users are unblocked a line five quiet minutes after the last while a job
  works on it or something it names moved, while he is away too, which the watch owes and has a
  handler post. A report on it goes out even when its thread already had one that turn, by
  [incidents.md](incidents.md).
- He is away when he says he is going to sleep or stepping out (`drive heard --away`), or when he has
  neither written nor tapped a card for an hour, since each tap on a card records him heard as a
  message does. While he is away only cards, the watch's one reply under a card holding built work,
  an incident line [incidents.md](incidents.md) calls for and an answer passing `asked` post; a hook
  refuses any other handler line. Summaries, due-time lines and job results wait.
  A report whose line the hook holds is marked held on the drive, `drive status` lists it under
  `Held for his catch-up`, and a `refused` record carrying `held=<seq>` tells the coordinator
  session that wrote it, so nobody takes it for a line he got. A correction record `--re` a held
  report travels with it, since cci never changes the report: `drive status` names it on the
  report's line, the `refused` record names one already written, and a `say` of the correction
  posts nothing while its report is held. A report found wrong while it is held, such as a close
  of an order not done, its writer takes back with the cci `post` tool, `kind: withdraw`, `re`
  the report's own seq, `to: communicator@<drive>`: `drive status` names the withdrawal on the
  report's line, the catch-up leaves the report out, so no post carries it and no order closes
  on it, and once the catch-up posts, an order it claimed to close is free for another report's
  close.
- His next message, right after its ack, or his next card tap gets one catch-up at his DM root,
  posted by the drive with no session in between. `drive heard`, or the tap, adds every order that
  changed while he was away or has a report held to the catch-up the drive owes him, which it keeps
  until the catch-up posts. Within one pass of the watch, 5 seconds, the watch writes one `report`
  of it, naming each held report its writer did not withdraw, with the corrections cci holds re it,
  and each order with news, and
  starts the handler that posts it, grouped by feature and each held report's facts as its
  corrections give them. A handler that ends without posting gets a fresh one the next time he is
  heard. Posting the catch-up lets go of every held report, marks a line given on each order it
  covers, and `say` names each correction of a held report the post must carry and each held
  report withdrawn, which it must leave out. A correction that
  comes after rewords the catch-up post: `say` of it posts nothing and names that post, and a
  second catch-up while none is owed posts nothing.
- That report carries `catch_up=true`, so `say` opens the post with the bold title
  `*Catch-up since <the time he was last heard>*` whatever the text says, and closes it with a
  count of every open card waiting on him, the list his status page queues, linking that queue on
  the dashboard, then each card asked since he was last heard, on its own line and five a message,
  since Slack draws five a message. A card asked before he went away stays behind the count, by his
  words on a catch-up that drew every card: "including all tehse cards each time ^^ is not helpful
  and is very obtrusive". No writer lists a card in the catch-up: a card the text names is not
  drawn twice, and the 120 words count the text alone, never the cards `say` adds, so none is cut
  to fit. The catch-up never goes in
  the thread of the message that brought him back, because a thread gets only lines about its own
  order.
- The minute a job built on a card's recommended option starts waiting on that card, the watch
  replies once under that card, in plain words: its recommended answer is built and paused on his
  pick, his tap ships it, and another pick rebuilds it first. The reply names no card, since it sits
  under its own, and so passes the post check when he was asked the same question twice. No other
  post repeats it. His words, Nov 11 1:16 AM, on a post `say` had opened with a raw line naming the job
  and its branch: "What the hell is this message?"
- His read-only question gets its answer in seconds from a handler, by his words "things liek this
  should be egtting almost immediate reponses but they are not": the handler reads the source that
  owns the fact through whatever access opens it, a staff route included, never answers that it
  could not read it, and posts with one `say(close=true)`. The communicator reads nothing
  itself.

## What goes in

- What changed for him first. "You can now tap Approve on any open PR card and it lands" beats "the
  rule that pulled your card when checks went red is merged and gone".
- Plain words for drive jargon he cannot open: a pipeline step, a job, order, record, card or row id,
  a `waits` id, a record seq, a hash or a plan step. Replace each with what it is, never
  quoted as it stands in a record or a log. "Waiting on a manual roll press" becomes "waiting for the button a
  person must press before a customer cluster's changes go out".
- Proper terms for everything technical. A function, syscall, flag, file, path, service, table or
  code path goes in by its proper term. A thing in the product or its data goes in by the word his
  messages or the code use, glossed in one line the first time. Never a vague stand-in such as "the
  copy change", nor a coined one such as "backup", "bucket history" or "chat format", since it names
  nothing he can open: "use the proper fucking terms for things". A product or tool he uses keeps its
  name, in backticks. When he corrects a word, the handler for his message runs
  `drive term <the word> "<the proper term>"`, and from then on the say hook finds a post using that
  word, naming the term, for Jev to judge by that rule and his words.
- Name only what the message needs, and a mechanism only when he asked about it.
- Every line stands alone, because he reads it with no other context: "you always have to assume I
  don't have any other context". A number, fraction, size or ratio says what it measures, why it has
  that value, and one concrete example. A mechanism gets one plain sentence before it is used, and
  nothing points at a list, finding, thread or PR he cannot see in that same message, such as "the
  third finding", "the remaining two" or "as above". Not "reads each journal file's page index sized
  to the file (a sixteenth of it, at most 64 KiB)", but:

  ```
  To open a run's page the browser first fetches a small index from the end of every journal file.
  It used to fetch the last 64 KiB of every file; now it fetches the last one-sixteenth of each file
  (about 6 KiB of a 97 KiB file), never more than 64 KiB.
  ```
- The coordinator writes its `report` records the same way, since a handler writes
  from their words.
- Link everything he could open where it is named: a PR, a teammate's fix, a card, a release, a
  build, an alert, a thread or an earlier decision, so he never has to look it up himself. A link
  opens the exact view its words describe: a screen a route, tab, anchor or query string reaches is
  linked at that address, never the page's root, and a view not built yet is called unbuilt, by his
  words "I dont see tthe slack tab on that link what are you talking abit?". A message
  that mentions a change names it with its PR's link, or it is not sent: never "a fix", "the change"
  or "a cc-slack fix" alone. When a record mentions a change without its PR, the handler finds the PR
  first, with `drive status` or `gh pr list --search`.
- Name a PR as `{pr:<number>}` and say in the line what it does; `say` links it under the PR's title,
  never by its number alone, or under words of the post's own as `{pr:<number>|<what it does>}`. A
  bare number names only a PR the drive holds: name any other as `{pr:<owner/repo#number>|<what it does>}`. Say what each PR serves: the order of his it carries out, in his words,
  "port of #N" with the old PR linked, or "Drive tooling:" and what it unblocks. The say hook finds
  a summary naming a drive PR that says none of these. Link each other PR the message names the same
  way.
- Name an order as `{order:<id>}`, which `say` links to the thread its lines go in under the first
  line of his words.
- Name a ruling of his as `{answer:<id>}`, which `say` draws as when he said it and its first words,
  linked to the ack of them in his DM, since the id cc-notes keeps it under means nothing to him.
- State a cause, a number or a state only through the read that measured it: a record, as `{record:<seq>}`
  on the same line, or, in a handler's post, a read of its own its `claims` list beside that line. `say` links what the record measured, its `--url`, under the first clause of the record's own
  words, at most ten, or under the words after a |, as `{record:<seq>|the memory graph}`, so the
  measuring lane's evidence is one tap away and its whole text never joins the post or its length:
  on Nov 10 a line at 8:46 PM blamed "the change that started the failures" without naming it, and a
  card he tapped at 9:49 PM carried a false "disk full". `say` posts nothing while a line outside a
  fence or quote names a cause, such as "because" or "caused", with no `{record:}` on it, unless a
  handler's post lists it in `claims`, where Jev checks it against the read beside it.
- A line telling him a state of work, that it is fixed, covers his case, merged, landed, queued,
  deployed or live, stands on a read of that state made in the same turn: the squash commit for
  merged, the deployed build holding it for live, the queue's own readback for queued, and for
  covers it, a run of the case he described, never a PR's title or a job's summary of its own work.
  A handler lists each such line in `claims` beside that read. Any other session's post carries the
  `{record:}` of the read on the line, or says unverified: `say` posts nothing while a line from a
  session posting on its own tells a state with neither. A line found wrong after it went out is
  reworded in place with `say(edit=<its permalink>, text=…)` the moment it is found, so no wrong
  state stands in his DM.
- A line about code, where it lives, what it does or why, stands on the posting session's own read
  of the code that does it, or the `{record:}` of the read that did, by his words "stop saying wrong
  shit bc you jsut guess and its probably misleading all your work" and "dont just take things for
  granted critically evakuate them". A code comment, a panic, error or log message, a doc, a PR body,
  the brief, a record's words or an earlier answer is a claim to test against that code, never a
  line to repeat, and the post says whether each claim it relies on holds. A line about every case
  stands on a read of what decides it, a line of an absence read from one metric, span or log
  series, such as no calls, zero rows or nothing since, stands also on a second source with no
  shared emitter showing the same absence, such as the caller's client spans beside the server's
  trace metric, since one series can be sampled or dropped before it is stored, and a mechanism it
  proposes on a read or his words showing
  whether the system controls that piece where it would run, by his words "we cant do that since we dont control the
  poloices on customer bucket". A handler passes `say` each such line in `claims`, beside the read
  behind it: `record:<seq>`, or words copied from a call of its own, such as a path, command or query,
  a claim standing on several reads listed once beside each. A bare `{pr:<number>}` or `{card:<waits id>}`
  is no claim, since `say` draws it under the PR's title or the card's question, and Jev reads the post's
  words as written beside it, so it needs no read, and so is one whose words after its `|` quote that title or question word for
  word, with … where they skip words, and a claim copying those words, such as a claim that the PR merged, claims only that
  PR's state; any other words after its `|` are the handler's own claim. The say hook hands Jev every call those
  words name with its whole result, and each record the post cites or carries, whole, and Jev holds
  the post for a claim its reads do not back, a read matching no call, or such a line left out, by his
  pick "The handler names the read behind each claim; Jev
  checks each claim against that read, whole, and holds only unbacked claims". The fix is that read,
  or the line cut to what the reads show, never a line posted marked unread.
- A card's title and options draw no links, so link whatever they name in its summary or a Show me
  caption. `drive job ask` and the post hook refuse an `ASK` or a post that names such a thing with no
  link there, naming the words and the clause they sit in, and that a thing not open yet is said so
  in that clause. In a post, "the change" names its PR too and is linked the same way; a question
  card's "the change" may be the design it asks about, which no PR holds yet.
- A link whose words name its kind, such as one reading "the update-button PR", covers the message's
  other mentions of that kind, such as "that PR". A mention inside a code fence, a generic one such as
  "every engineer's PR", and one in a clause saying it does not exist yet, such as "its PR goes up
  first" or "no PR is open yet", name nothing to link. One that exists keeps its link whatever its
  state, "has not merged" or "waits on your tap" included. Slack draws no `[label](url)` link, so the
  check refuses one and shows the `<url|label>` to write. A link's words hold no other link, mention or
  `{pr:}`, `{card:}` or `{record:}` reference, which Slack draws as a literal `<`, a link reading its own
  URL and a literal `>`: `say` refuses one, naming the nesting, so close the outer link's words and put
  the inner one after it.
- Numbers over adjectives: "129 of 476 passed, up from 100" beats "much better".
- Answer his next question in the same message: why, did he already decide this, is it a port, what
  comes after, why it is held.
- A line that says something waits on X says how long and whether X is running, or `record_lines`
  refuses the report. Past two hours with nothing running, it reports a failure with its owner and
  how it is prevented, because a wait that long is a failure, never a state.
- Done means live and checked on the surface he uses: a recording of the click for a page, the live
  post for a card or message, the installed command's output for a tool. Data fetched from an API
  proves no screen. Say how you checked in a few words, with the link.
- Say what was observed, and never list what is not proven, under any label: "unproven", "not proven
  yet", "not seen working yet", "unverified", "untested". Each unknown gets a job, and he gets its
  result or nothing. `cc-slack check-post` refuses a line listing what is not proven, and a job's
  `UNPROVEN:` list is the coordinator's input, never a fact its records state and never copy for
  him.
- An incident line follows [incidents.md](incidents.md) and carries no side facts about a release.
  Its alert's Slack message and Sentry or Datadog issue stay bare so Slack unfurls them: the say hook
  lets them through on a post whose order, or the record it posts, is an incident's.
- State comes from live reads, `drive status`, GitHub or a transcript, never from memory. The say
  hook finds a post calling a job stuck or silent with no read of its transcript by you in the last
  10 minutes, `drive job replies <j>` or `cc-transcript`. A job's last line is history: what is live is read from the default branch and the release, since a later
  PR may have reverted the change or put it back. A 12:57 AM status on Nov 11 told him the 512
  memory indexes were backed out, from a job's line written before the 8:41 PM PR that restored
  them. What a
  release carries, such as its PRs, its steps or which build fixes what, is read from that release's
  commit and build.
- Ask for a tap only when one is owed. Before any card or line asks him to tap, approve or hold a
  PR, read `drive status <pr>`; when he tapped it already, or it merged or closed, say what happened
  to it instead. The post hook refuses a card or post asking for a tap he already gave, naming the PR.

## What stays out

- The machinery: pins, packs, installs-as-process, who handed what to whom. A failure of the drive's
  that reaches him anyway, such as a card refused, a check nobody ran, a message that never arrived or
  a hook refusing a post he ordered, says in that post how it is prevented: the tool change, the
  `{order:}` or `{pr:}` carrying it, and when it is live, so that fix starts before the line goes out.
  His words of Nov 12, 12:44 AM, under "Its card was refused because nobody checked that PR against
  your words": "thats not acceptable to dsay without telling me how you will prevent it". A disconnect
  is never the reason, since a session reaches another through SendMessage while cci is down, by
  [records.md](records.md). When he asks why, give the mechanism in one or two sentences and quote
  back the instruction he gave, then the fix and when it is live. The post hook and `record_lines`
  find a line naming such a failure, or citing the defect record the drive filed for one, in a
  post naming no order or PR and drawing no PR card, and only warn the session sending it, which posts as written: the same words
  tell the product's own refusals too, such as a pod its check refused, by his pick "1 and then if
  we kepe getting false rejects move to 3", whose third option reads "Cause and plain-words findings
  only warn the session". On that warning, a line that does tell a failure of the drive's is
  reworded in place naming the fix and its order, and Jev reads such a line as within his words,
  never as work outside them.
- A second ask for a post he ordered. His order to post, in his DM, in a terminal or by a tap on a
  card, is his permission for one post in the place he named. No card asks him to approve it, no line
  says a hook needs his Yes, and a card already up asking that is closed the moment it is seen.
- A post nobody ordered, such as a reply offered to someone who reported a bug or a line for a
  teammate, needs his word once, on a question card asking whether to send it. The fix-live note is
  not such a post: his standing grant covers it. Nor is the answer to a question someone asked him
  that he shares or links: his share orders it.
- Asks of a bug reporter: a bug thread gets its one fix-live note, and no card option offers to ask
  the reporter anything.
- Messages with no new fact: react with `+1` to chatter instead of acking it, and post no "nothing
  new" line. A job's idle notice, a result repeating a state he already has, or one older than what
  he was told since reaches no session's post to him.
- Second copies: one post per thing, a correction rewords it in place with `say(edit=<its permalink>,
  text=…)`, and never text and a card for the same thing.
- A promise without a record. A promised time is one the coordinator set with `--due` in the same
  turn, by [The short form](#the-short-form), and its `report` names it. Any other promise, such as
  "from now on…" or "next you get…", goes out only with the order, due time or job that carries it
  out named in the same message, because a promise with nothing behind it is one he cannot believe.
- The current time, and any time you estimated: Slack already stamps every post. Compute a promised
  time from `date`, or write it as `+30m` to `--due` and read it back from `drive status`, never from
  your sense of how long the turn has run, which drifts.
- A clock time typed for work already done. Name the job or record and let the drive's stamp give the
  time: `{started:<job>}` for when a job started and `{at:<seq>}` for when a record went out, which `say`
  fills. Only words he reads are checked: `say`, a card `drive job ask` keeps, and a POST report or ready
  record to the communicator, through the cci `post` tool or a Bash `cci post`, refuse a time at or
  before the write, such as "started at 4:25 AM" in a report posted at 4:50 AM, naming the write's stamp
  and each named job's. A SendMessage, a defect, a handoff, any other record
  between sessions and `drive job report` quote times as they stand, by his words "i think that time
  hook its tooa ggessive too". A time after the write, such as "next line by 5:40 AM" or "the hold
  expires at 6:54 PM", read on the day written beside it, as "6:54 AM tomorrow" or "Nov 9 9:28 PM", else
  on the day nearest the write, a time quoted as a tool printed it, in backticks, a time a tool or query measured, such as an outage window, written in
  Pacific on the line linking the tool or read that measured it, and a linked time pass. His ruling of Nov 3:
  "the agent should never be passing in a time".
- A job's or record's stamp as another action's time. `{started:<job>}` draws as the job's start,
  labelled so, as "9:52 AM (job start)", and `{at:<seq>}` as when its record went out; neither is when
  a release, build or deploy started. Quote that start as its build or tool printed it, in backticks or
  its link's words, on the line linking it, as in
  ``<https://buildkite.com/forge/release/builds/15020|the release build> started `11:30 AM` ``.
  Every surface the clock-time check runs on refuses a line saying when a release, build or deploy
  started with either stamp in a clause naming no job or record, or a quoted time on a line linking no
  build or tool: on Nov 12 two posts said the SandSQL release started at its job's start, over an hour
  before its release build did.

## Examples

Two status lines that lead with what changed for him, with concrete counts:

```
*Your PR cards keep their buttons while CI is red*
Tapping Approve on any open PR now lands it once CI passes. Pictures on cards go live by 1:30 AM.
```

```
*1 of 5 tooling fixes is merged; none is on your Mac yet*
The other four are in review and land by 11:10 PM; each goes live within minutes of merging.
```

A design question goes out as its card and nothing else: about 120 words with its options, its Show
me drawn whole above them, and no findings post before it.

````
*How should a finished run's files be laid out so its page opens without guessing?*
A finished run is about 180 small files, each with its page map at the end, so the browser guesses where each map starts.
Recommended: one snapshot file per finished run, so any finished run opens in 3 requests.
Meanwhile the snapshot file is built on a branch, and its PR waits on your pick.
*Show me*
The requests a finished run's page makes today, then with one snapshot file:
```
GET journal/0001.ltx  last 64 KiB, guessing where the map starts
... 180 files, one guess each
```
```
GET snapshot.ltx      map at a fixed place
GET 2 page ranges
```
[One snapshot file per finished run (Recommended): written when the run ends, old runs backfilled; any finished run opens in 3 requests]
[A map file beside each journal file: no guessing, but still one request per file and twice the objects]
[Map at the start of the file: changes the shared file format and still needs a length read first]
````

## Before it goes out

- The hook passes the exact words of every post and edit, the ack's included, through
  `cc-slack check-post` and counts them as they go out. A `say` post counts the words of each
  `{card:}`, `{pr:}`, `{order:}` and `{record:}` title it draws, and its length refusal says how many
  of them those titles are.
- A finding refuses nothing on its own. Once `say` has posted the post, so `say` stays fast, the hook
  hands it and every finding, check-post's and the drive's own, to Jev with the rule each finding
  stands for, in this skill's and cc-slack's words, and his words: his messages in the thread it went
  to and each answer or Explain more tap of his a card there shows, and on a post on an order his
  messages and card answers in each of that order's other threads in his DM, read from Slack, beside
  the session's recent turns. A handler's brief, the record it reads in and every prompt of a handler's
  own `claude --bg` session are the communicator's or a writer's words, never his, whatever they
  quote. Jev judges only the line each finding names, by its rule: the post stands when no finding's
  rule holds against its line, or his words lift it, and is held with Jev's reason when one does,
  never for saying more than his message asked, and never as unpermitted, since cc-slack's Slack
  permission hook judges every write. A held post stays in his DM as written, and the
  session that posted it hears Jev's reason, rewords it in place with `say(edit=<its permalink>,
  text=…)`, which Jev checks again, and ends its turn only once Jev passes the new words. His pick:
  "Say posts at once and Jev checks afterwards: say stays fast, but a held line sits in your DM until
  its handler edits it". A handler's session gives Jev up to Claude Code's own 600-second hook
  timeout, and every other session the deadline capt-hook's request leaves.
- A post with a finding a count or a mark decides, the post's word count, his reading as away or a
  line out of turn in the thread a bug report or question came from, and a dry run, Jev judges before
  `say` runs, since an edit takes back neither the buzz on his phone nor a line a reporter already
  read: it goes out only when Jev lifts that finding, and when Jev cannot finish, because it errs,
  times out, or reads the transcript past capt-hook's deadline, it still refuses, since no judgment
  lifts it. On any other post a Jev that cannot finish leaves each finding as a warning: the post
  stays as written, and the session that posted it hears every finding beside it and rewords the post
  in place when one holds. His pick: "Refusals only warn when the judge cannot finish: no post ever
  waits, but it goes out unchecked, as twice this morning". A session's account of its own act or
  mistake names no cause the cause rule asks a record for. An order or job id, such as o643 or j1700,
  comes back unposted with no judging unless a message of his the post passes as asked used it. His
  words: "stop hard coding it and just use jev evaluating my message and rules to see if it's ok to
  send" and, on a refusal Jev got wrong, "you need to fix the hookw henver it wrongly refuses yu".
- What the session passed `say` is what he reads: `say` adds no words of its own and checks nothing
  again. Check-post reads the words under each link, so a card, PR, order or record title it flags is
  named in the post's own words, `{pr:<number>|<words>}` or `{record:<seq>|<words>}`.
- A post that answers him passes each message of his in `asked`, word for word or by its permalink
  in his DM, and check-post gets each message's words as `--asked`, so a drive word he used there,
  such as "the drive" in his question about the drive, passes in the answer, and so does an internal
  id such as a run id when his message asks for ids, as "the session IDs" does. When one stands word
  for word in his messages in that thread, an answer or Explain more tap of his a card there shows or
  a message of his an `asked` permalink links, the length limit lifts with no judge; otherwise Jev reads the same words.
- A post that draws a card, or a PR card through `tap_prs` or a PR its waiting line links, carries
  the card its words say waits on him, so check-post's `waits-without-card` finding is dropped for
  it. A check that fails on a post refuses it rather than let it out unchecked, and so does a
  check-post that exits 1 naming no finding.
- A handler's new line in his thread comes back until that handler has read the thread in the last
  10 minutes, so the line cannot miss what he said there since. Past that read every other check
  runs. A post over its limit that Jev upholds the finding on comes back once, unposted, with every
  finding, Jev's reason and what to fix; a post Jev holds on any other finding, such as one
  check-post flags, one naming something he could open with no link there, or one asking a tap that
  is not owed, is reworded in place once, from every finding, Jev's reason and what to fix.
- Then read it as he will, on a phone, with no other context. Does he know what changed, whether he
  must act, and when he hears next? Does every number, term and reference make sense with nothing
  else open, and does each thing he could open carry its link right there?
- A post he faults anyway, by its words, a picture, or a card or link it lacks, shows a class of
  post these checks pass. The post is fixed in place, and every time, a repeat too, its own Tooling
  order changes `cc-slack check-post`, a drive hook or the library that draws posts and cards, so
  the next post of that class is refused or drawn right before he reads it, by
  [meta.md](meta.md#records-and-inputs). His words: "When I give feedback on posts
  keep iterating on the library, tooling etc don't get complacent".
