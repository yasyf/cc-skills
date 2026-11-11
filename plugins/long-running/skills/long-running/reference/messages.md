# Messages to the owner

Only the communicator's side writes to him, from his own messages and from the `report`, `ready` and
`ask` records the coordinator sends. The communicator posts the ack alone in its first message and
nothing else. Its handlers write, check and post every other post, one handler per message of his or
per record: the coordinator's record carries the facts, the handler writes the words, and a handler
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
| A question card: question, summary and options together | 150 words |
| A question card's Show me, the worked example between its summary and options | none: drawn whole as its visuals, outside the 150 |
| Each option on a card | one line of 25 words, saying what runs, on what trigger, what it reads and what reaches whom, then what he gets and what it costs |
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
  sent as a JSON string counts as its entries), with `…` where you skip words.
- The hook looks for each quote in his messages in the thread the post goes to, or in the handler's
  brief and the record its `Record:` line names, with straight and curly quote marks alike; one found
  quote is enough. A refusal says which
  limit applied and why: no `asked`, so not an answer; quotes it could not find, named; or an edit's
  own length.
- An edit that rewords a posted line may grow it by 10 words, so a correction can add the proper
  terms without the cap cutting what the line says.
- A question card keeps its 150 words whatever it quotes, and its Show me counts toward none of them,
  so never cut a Show me to fit.

## The short form

- Lead with what changed for him, in his words, never with how the machinery got there.
- Write a bold line and at most two sentences; use a table only for three or more parallel items.
- Keep every card and line in a feature's thread about that feature. Post drive tooling, such as a
  fix to the launcher its builders start from, at his DM root under its own words, because a tooling
  line in a feature's thread makes no sense to him there.
- The coordinator sends one `report` record per thread per turn, opening `POST` or `FYI`, because
  four posts of 60 words are the same wall as one of 240. It sizes the report's facts to the post
  they become, a line of 60 words or a summary of 120, and the handler writes the post from them.
- The report's first sentence says what changed for him, and no cut takes it. What does not fit is
  cut, each item that waits on him kept first, and the rest waits for the next turn, never a second
  record. Each item that waits on him carries its card's permalink or `waits` id, so the handler
  never searches for the card.
- When a line names a card or says something waits on him, name the card as `{card:<its waits id>}`
  in `say`'s text, which `say` links under words that name it. Slack then draws the card with its
  buttons under the line, so he answers it where it sits.
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
- When a message promises him a time, the coordinator sets it with `--due` in the same turn, so the
  watch holds the drive to it from Slack's own record, not a session's memory. `drive order add --due`
  counts from when the order is added, and `drive order set --due` from the next line in the order's
  thread. The watch says `DUE` every 10 minutes until a drive post is in that thread, and the order
  cannot close before one is.
- The handler records each post in an order's thread through the post itself,
  `say(order=<o>, record=<the report's seq>)`, which delivers the part the report's `part` attr
  names, or each part of an order its `close` attr names. A progress line's report names neither,
  whatever job it names: it keeps the time and delivers no part. A post in the thread of the
  incident an alert's order joined counts in that order's threads too, once the incident's owner
  job carries it. `drive order posted <o> <link> --part <name>` records a part delivered anywhere
  else. Only the communicator's side records a post; the coordinator is refused.

## The shapes

**An ack.**

- Post it in the thread of his message, alone in the communicator's first message, inside 5 to 10
  seconds; cc-slack put the eyes on his message when it landed.
- Write at most one sentence the communicator already knows: what he asked, in his words, and what
  happens next. Never read, draft or check for it, because any of those delays it.
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

- A handler draws it from an `ask` record: the job's `ASK` as `drive job ask` kept it, with nothing
  posted before or after it.
- Its title is its question, one sentence of plain words that names what it decides by its proper
  terms, the function, flag, file, service or table, never a stand-in such as "the copy change".
  `drive job ask` refuses a title naming its object only as "the change", "the fix" or "this" while
  its summary or findings name the proper terms.
- Slack draws a card's title and buttons as plain text, so a link or markdown there shows raw and
  `drive job ask` refuses it; put the link in the summary.
- Its body is its summary, one to three lines: what was found and what is being decided, the
  recommendation, and what happens meanwhile. The first line gives him the investigation's results
  before he picks. When the block has a Show me, a `*Show me*` line and the Show me follow as written.
- One option is always the fix the job's own diagnosis points at, recommended unless the summary says
  why not. The recommended option is the block's first, which `drive job ask` refuses otherwise. `drive job ask` refuses a block whose findings carry no `Direct fix:` line naming that
  option by its words, or `Direct fix: none: <why>`.
- Give two to four options, each one line of at most 25 words saying what runs, on what trigger,
  what it reads and what reaches whom, then what he gets and what it costs; `drive job ask` refuses
  any other count or length. cc-slack adds Other, which takes his own words,
  and Explain more to every card that waits on him, and refuses an option that repeats either.
- Name each mechanism by what it does, such as "the Mac reruns the check function every 30 seconds
  and compares its output; a change sends one inbox message that wakes the agent". The summary's
  first line says this of what every option shares, and the title asks only what the options differ
  on. `drive job ask` refuses a title or option that leaves polling, a push, a subscription, a
  notification, a listener or an event a bare word, as "Polls only", "a Poll source" and "the event
  system" do, because he cannot tell what is polled and what wakes whom: "That wasn't clear from the
  card, polling is fine for seeing when the function output changed". A Sand name such as
  `event::on` is a proper term and passes.
- Write each option to stand alone: cc-slack draws the recommended option first and numbers the rest
  after it, so the numbers in an `ask` record are not the ones he sees. An option that builds on
  another repeats what that one does, such as "Own process plus a memory cap per script".
  `cc-slack check-post` and `drive job ask` refuse "as 1", "same as 2" or "the first option", and the
  coordinator writes an `ask` record's options the same way.
- The findings stay in the job's file. Explain more on the card posts the one section its question
  rests on, in plain words, and that is the only way findings reach him.

Its Show me:

- Include a Show me only when it shows him what the summary and options cannot. That is the flow
  between the parts the options change, the numbers they turn on, the options side by side, or the
  real calls of an API choice. A constant, a name, a value or a call the words already say is no Show me, and a
  card with nothing that adds clarity has none.
- Fill every row and cell with real contents, measured or worked out from the code and marked as
  such, never the names of what will be measured later; `drive job ask` refuses a table with one
  value in every cell.
- Each visual is one fence whose language names its kind, and one render layer,
  `lib/drive/site/visuals.js`, draws that one source for each surface. Slack gets a picture of it,
  since a Slack message takes no code or diagram blocks, except a chart, which Slack draws as its own
  [chart block](https://docs.slack.dev/reference/block-kit/blocks/data-visualization-block). The
  dashboard and other web pages draw each kind's source natively, as highlighted code, a live diagram or chart, or a compare, never as a picture. A
  markdown table is native on both: a Slack data table and an HTML table.
- Pick the kind that shows what the words cannot:

| The question turns on | Kind | Fence holds |
|---|---|---|
| the shape of an API, call, query or schema the options change | code | the real calls, in a fence naming its language: ```sand, ```sql, ```go |
| components, data layout or flow between them | arch | an arch/v1 JSON document, the diagram the dashboard and cc-present draw |
| routing, ownership, steps over time, a state machine, a small graph | mermaid | Mermaid source |
| a graph whose layout carries the point, such as clusters of what runs where | dot | Graphviz source |
| numbers: sizes, counts, timings, before and after | chart | a Vega-Lite JSON spec with its data inline: a bar, line or area mark, or an arc for a pie, which Slack draws as its own chart |
| a tradeoff between options | compare | `{"options": [{"name", "recommended", "language", "code", "points": ["+ a gain", "- a cost"]}]}`, drawn side by side |
| words compared across rows | table | a markdown table, which cc-slack draws as a native Slack table |
| a draft message or a quote | quote | its sentences, each line opening with `> `, which Slack shows as its own quote, links and all |
| a UI or anything only a picture shows | image | `![what it shows](/absolute/path.png)` on a line of its own, a screenshot or a gen-image or codex imagegen picture |

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
  categories, and a card holds at most 2 charts.
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
    command output or a stack trace, whatever language it names;
  - a chart, compare or arch whose JSON does not parse;
  - a chart past Slack's limits above, or more than 2, naming the limit and the value;
  - two or more pictures whose captions do not each open with an option's number;
  - a `flowchart LR` or `graph LR` whose longest chain runs past four nodes;
  - a fence of one or two lines declaring a constant;
  - as many code fences as options, which go in one `compare` fence labeled per option;
  - a picture kept in the drive's asks or pictures folder, drawn by hand instead of from a fence;
  - two or more PRs listed by number, which their stack's PR card shows;
  - a `Show me:` with no visual under it, and a picture inside a sentence or a table cell.
- A visual that still fails to draw leaves the Slack card with its caption and nothing in its place,
  and the dashboard with its source as text.

Posting it:

- Post the card with one `say(card=<what drive job card prints>, thread_ts=<his thread>)` call, the
  card `drive job card <the record's ask file> --waits <its waits id>` prints, never one put together
  by hand.
- cc-slack posts a card that names its drive and waits on him at his DM root, whatever thread it is
  given, and posts the card's permalink under its question in that thread. Give a drive tooling card
  no thread, since its order has none.
- Then close the `ask` record with a `posted` record carrying the card's permalink and `waits` id. His
  tap comes back as an `answer` record, so "card posted" and "he answered" stay two states.
- Every question card carries its `waits` id, `<drive>:<job>-<the ask record's seq>`, which
  `drive job card --waits` requires. When a job builds the recommended option meanwhile, the summary
  says which option is being built.
- Set `drive` to the drive's name on every card, so cc-slack reads a `waits` card as the drive's
  question, whose home is Slack, and posts it with no cc-present board warning.

When it is answered, replaced or redone:

- His words written under a card answer it: the card closes showing them, and the work goes ahead on
  them. `drive job card` refuses to redraw that card with another question, so put a question his
  words leave open on a new card.
- When an answer or ruling he gave elsewhere replaces a card, the coordinator's `drive answer`
  settles it in place, showing his words and a link to them where its buttons were, so a late tap
  records nothing.
- To change the words of a card he has not answered, draw it again under its `waits` id. That one
  call edits the card where it sits, its layout, pictures and buttons included, posts nothing, and
  leaves every older copy without buttons, reading one line that links the card. Never `slack_edit` a
  card: an edit with text alone replaces its layout and buttons with plain text, and cc-slack refuses
  it.
- While a card he has not answered is being redone, it says so where it sits and keeps its buttons,
  because he may still want to tap one. The moment a redo starts, the coordinator sends an `ask`
  record with `--attr ask=<the card's kept file>`, `--attr waits=<its waits id>` and
  `--attr reworking=<what is being redone, and when it is back>`.
- The handler draws the card again from that file, its title, body and options unchanged, with one
  line atop its body saying what is being redone and why:
  `drive job card <its ask file> --waits <its waits id> --reworking "<those words>"`. A pick he taps
  meanwhile stands. The redone `ask` comes under the same `waits` id and draws the new words with no
  such line.
- No option asks a reporter or anyone else to retry, confirm or check, because the drive proves a fix
  itself, and `drive job ask` refuses an option asking someone to retry or confirm.

**A PR card.**

- Nobody writes it: a handler draws one card per stack from a `ready` record. It is only for a PR that
  carries out an order of his whose words ask for it, or a `Forge-AI/*` drive tooling fix no exempt
  class covers, which serves its own order by [Scope](scope.md).
- The line above it quotes that order at his DM root: his words, or the tooling order's
  "Drive tooling: …" words. `say` refuses a PR card whose PR serves a drive tooling order while its
  text leaves out that order's words, naming them, and posts nothing. A port never gets one: he reads
  "port of #N landed" in its order's thread once it lands, and so for every class that lands with no
  tap.
- `say(tap_prs=[<each url or owner/repo#number, bottom first>], text=<line>, thread_ts=<his thread>)`
  makes cc-slack draw one card at his DM root for the stack those PRs form (`drive card`), with
  Approve and Hold while it waits on him. It also posts the card's permalink under its question in
  his thread, so that thread carries its buttons. The call's `card_permalink` names the card and its
  `permalink` only the line. When the card command gives a PR no card, the call fails with its reason
  and posts nothing.
- The same PRs show under the line on the dashboard's history, each with where it stands now. A post
  naming a PR the drive does not hold is refused: `drive pr add` records it first.
- His Approve lands exactly those PRs, bottom first. A PR added later gets its own card when posted
  alone. Posted with the rest of its stack, it joins one new card for them all, and cc-slack marks
  each older card of those PRs replaced, with a link to the new one.
- Each PR gets its own section: its title, link and Open button, then its Refresher, its biggest
  surprise and what he will hate most. Where it stands and what it waits on follow only when that is
  not green and his tap, and how it differs from the plan only when it does. Its Show me comes last,
  its pictures inline. The card's title and opening summary come from `## The stack` in its bottom PR.
- Once he answers, the card folds to its title, his pick and one line per PR. When an answer or ruling
  of his replaces the work it carries, `drive answer` settles it as it does a question card.
- Its words come from each PR's title and body, with record ids dropped, so fix a bad card in the PR
  body, never in the message.

**A confirmation of a skill update** goes out twice for each message of his that leads to one. The
ack says the skill is being updated and what changes, in plain words; a summary then says it is
merged and installed, listing each rule now in the skill.

**A fix-live note in a bug report's thread.**

- Post it once per bug, after the fix ships and is checked, never before: one or two plain sentences
  saying what is fixed and since when, with the link.
- It needs no card and no approval, because he granted it standing: "we have authorization to message
  once in bug fix threads after we ship the fix … it needs to be in plain simple English no jargon, no
  crazy shit, and no asks".
- Leave out drive jargon such as job or record ids, and asks such as "please retry", "please confirm"
  or "let us know". Post nothing else in that thread: no progress, no test results, no follow-up.

```
Fixed: Iris picks up again right after you answer its question, on today's build.
```

**The status page.** You never write it: the drive's watch redraws the drive's canvas, a tab in his
DM with the app, within a minute of any change. It shows what waits on him first and in full, then
each open feature as one line by state, then finished work as counts with the newest five named, then
the dashboard link. When a line says where things stand, link the page instead of restating it.

## When it goes out

- His message gets its ack inside 5 to 10 seconds, whatever else is running; no handler brief,
  record, report, card, due line, draft or `check-post` holds the ack. The handler the communicator
  starts answers in that thread when it is done.
- While the communicator compacts, cc-slack posts "Got it, back in a minute." under each of his
  messages itself, and the message arrives with `acked`, so it gets no second ack.
- Everything else waits for a summary: a job result, a merge, a CI turn or a promised time feeds the
  next summary of its thread, never a line of its own. The watch's `TICK` comes every 30 minutes and
  names the orders with news; the coordinator sends at most one `report` per thread per tick, then
  `drive order set <o> --lined` for each order it covered.
- An incident is the exception: until users are unblocked he hears a line at least every 5 minutes,
  then each step of the root fix once, by [incidents.md](incidents.md).
- He is away when he says he is going to sleep or stepping out (`drive heard --away`), or when he has
  written nothing for an hour. While he is away only cards post: PR cards and question cards that
  wait on him, the watch's one reply under a card holding built work, and an incident line [incidents.md](incidents.md)
  calls for. Summaries, due-time lines and job results wait.
- His next message gets one catch-up at his DM root, right after its ack. `drive heard` tells the
  coordinator every order that changed while he was away; it sends one `report` for his DM root
  covering them, grouped by feature with what waits on him last, then `--lined` each.
- That report carries `catch_up=true`, so `say` opens the post with the bold title
  `*Catch-up since <the time he was last heard>*` whatever the text says. The catch-up never goes in
  the thread of the message that brought him back, because a thread gets only lines about its own
  order.
- The minute a job built on a card's recommended option starts waiting on that card, the watch
  replies once under that card, in plain words: its recommended answer is built and paused on his
  pick, his tap ships it, and another pick rebuilds it first. The reply names no card, since it sits
  under its own, and so passes the post check when he was asked the same question twice. No other
  post repeats it. His words, Nov 11 1:16 AM, on a post `say` had opened with a raw line naming the job
  and its branch: "What the hell is this message?"
- His read-only question gets its answer within minutes from a handler, which reads the source that
  owns the fact through whatever access opens it, a staff route included, and never answers that it
  could not read it. The communicator reads nothing itself.

## What goes in

- What changed for him first. "You can now tap Approve on any open PR card and it lands" beats "the
  rule that pulled your card when checks went red is merged and gone".
- Plain words for drive jargon he cannot open: a pipeline step, a job, order, record, card or row id,
  a `waits` id, a record seq, a hash, a lane or a plan step. Replace each with what it is, never
  quoted as it stands in a record or a log. "Waiting on a manual roll press" becomes "waiting for the button a
  person must press before a customer cluster's changes go out".
- Proper terms for everything technical. A function, syscall, flag, file, path, service, table or
  code path goes in by its proper term. A thing in the product or its data goes in by the word his
  messages or the code use, glossed in one line the first time. Never a vague stand-in such as "the
  copy change", nor a coined one such as "backup", "bucket history" or "chat format", since it names
  nothing he can open: "use the proper fucking terms for things". A product or tool he uses keeps its
  name, in backticks.
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
- The coordinator writes its `report`, `ready` and `ask` records the same way, since a handler writes
  from their words.
- Link everything he could open where it is named: a PR, a teammate's fix, a card, a release, a
  build, an alert, a thread or an earlier decision, so he never has to look it up himself. A message
  that mentions a change names it with its PR's link, or it is not sent: never "a fix", "the change"
  or "a cc-slack fix" alone. When a record mentions a change without its PR, the handler finds the PR
  first, with `drive status` or `gh pr list --search`.
- Name a PR as `{pr:<number>}` and say in the line what it does; `say` links it under the PR's title,
  never by its number alone, or under words of the post's own as `{pr:<number>|<what it does>}`,
  the one way to link a PR the drive does not hold. Say what each PR serves: the order of his it carries out, in his words,
  "port of #N" with the old PR linked, or "Drive tooling:" and what it unblocks. Link each other PR
  the message names the same way.
- Name an order as `{order:<id>}`, which `say` links to the thread its lines go in under the first
  line of his words.
- State a cause, a number or a state only through the record that measured it, as `{record:<seq>}`
  on the same line. `say` draws it in that record's own words, linked to what the record links, so
  he reads the measuring lane's words, never a paraphrase of them: on Nov 10 a line at 8:46 PM blamed
  "the change that started the failures" without naming it, and a card he tapped at 9:49 PM carried
  a false "disk full". `say` posts nothing while a line outside a fence or quote names a cause, such
  as "because" or "caused", with no `{record:}` on it.
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
  check refuses one and shows the `<url|label>` to write.
- Numbers over adjectives: "129 of 476 passed, up from 100" beats "much better".
- Answer his next question in the same message: why, did he already decide this, is it a port, what
  comes after, why it is held.
- A line that says something waits on X says how long it has waited and whether X is running. Past
  two hours with nothing running, it reports a failure with its owner, because a wait that long is a
  failure, never a state.
- Done means live and checked on the surface he uses: a recording of the click for a page, the live
  post for a card or message, the installed command's output for a tool. Data fetched from an API
  proves no screen. Say how you checked in a few words, with the link.
- Say what was observed, and never list what is not proven, under any label: "unproven", "not proven
  yet", "not seen working yet", "unverified", "untested". Each unknown gets a job, and he gets its
  result or nothing. `cc-slack check-post` refuses a line listing what is not proven, and a job's
  `UNPROVEN:` list is the coordinator's input, never copy for him.
- An incident line follows [incidents.md](incidents.md) and carries no side facts about a release.
- State comes from live reads, `drive status`, GitHub or a transcript, never from memory. A job's
  last line is history: what is live is read from the default branch and the release, since a later
  PR may have reverted the change or put it back. A 12:57 AM status on Nov 11 told him the 512
  memory indexes were backed out, from a job's line written before the 8:41 PM PR that restored
  them. What a
  release carries, such as its PRs, its steps or which build fixes what, is read from that release's
  commit and build.
- Ask for a tap only when one is owed. Before any card or line asks him to tap, approve or hold a
  PR, read `drive status <pr>`; when he tapped it already, or it merged or closed, say what happened
  to it instead. The post hook and the record hook refuse a card, record or post asking for a tap he
  already gave, naming the PR.

## What stays out

- The machinery: pins, packs, installs-as-process, who handed what to whom, which hook refused what.
  When he asks why, give the mechanism in one or two sentences and quote back the instruction he
  gave, then the fix and when it is live. When a hook refuses a post he ordered, tell him in one line
  that it goes out once the fix is installed.
- A second ask for a post he ordered. His order to post, in his DM, in a terminal or by a tap on a
  card, is his permission for one post in the place he named. No card asks him to approve it, no line
  says a hook needs his Yes, and a card already up asking that is closed the moment it is seen.
- A post nobody ordered, such as a reply offered to someone who reported a bug or a line for a
  teammate, needs his word once, on a question card asking whether to send it. The fix-live note is
  not such a post: his standing grant covers it.
- Asks of a bug reporter: a bug thread gets its one fix-live note, and no card option offers to ask
  the reporter anything.
- Messages with no new fact: react with `+1` to chatter instead of acking it, and post no "nothing
  new" line.
- Second copies: one post per thing, a correction edits it in place, and never text and a card for
  the same thing.
- A promise without a record. A promised time is one the coordinator set with `--due` in the same
  turn, by [The short form](#the-short-form), and its `report` names it. Any other promise, such as
  "from now on…" or "next you get…", goes out only with the order, due time or job that carries it
  out named in the same message, because a promise with nothing behind it is one he cannot believe.
- The current time, and any time you estimated: Slack already stamps every post. Compute a promised
  time from `date`, or write it as `+30m` to `--due` and read it back from `drive status`, never from
  your sense of how long the turn has run, which drifts.

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
Meanwhile nothing is built on either layout, and the run page works as it does today.
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
- What check-post passed is what he reads: `say` adds no words of its own, and runs check-post again
  on the words it sends, each reference drawn as its words, refusing the post on any finding.
  Check-post reads the words under each link, so a card, PR or order title it flags is named in the
  post's own words, `{pr:<number>|<words>}`; a `{record:}` always draws the record's own words, so a
  record whose words it flags is not the one to cite.
- A post that draws a card, or a PR card through `tap_prs` or a PR its waiting line links, carries
  the card its words say waits on him, so check-post's `waits-without-card` finding is dropped for
  it. A check that fails on a post refuses it rather than let it out unchecked.
- A handler's new line in his thread comes back until that handler has read the thread in the last
  10 minutes, so the line cannot miss what he said there since. Past that read every other check
  runs. A post check-post flags, one over its limit, one naming something he could open with no link
  there, or one asking a tap that is not owed comes back once, with every finding and what to fix.
- Then read it as he will, on a phone, with no other context. Does he know what changed, whether he
  must act, and when he hears next? Does every number, term and reference make sense with nothing
  else open, and does each thing he could open carry its link right there?
- A post he faults anyway, by its words, a picture, or a card or link it lacks, shows a class of
  post these checks pass. The post is fixed in place, and every time, a repeat too, its own Tooling
  order changes `cc-slack check-post`, a drive hook or the library that draws posts and cards, so
  the next post of that class is refused or drawn right before he reads it, by
  [communicator.md](communicator.md#what-each-input-gets). His words: "When I give feedback on posts
  keep iterating on the library, tooling etc don't get complacent".
