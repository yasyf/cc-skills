# Messages to the owner

He reads only Slack, so a post is the whole of what he knows. The default: answer him
directly, once, as soon as the answer is known, in plain words with links. No placeholder goes out
first and no promise line stands in for the answer. While a read runs, the thread's status says
what is being read.

The communicator writes every post to him, from his message or from a `report` record the
coordinator, the meta session or the watch sends. The watch posts each milestone
itself, and the drive draws the cards. You are trusted to judge what a post should say. The rules below are his style rulings and the
facts about how `say` and the cards behave.

## Contents

- [Limits](#limits)
- [The short form](#the-short-form)
- [The shapes](#the-shapes)
- [When it goes out](#when-it-goes-out)
- [What goes in](#what-goes-in)
- [What stays out](#what-stays-out)
- [Examples](#examples)
- [The rules Jev judges a post by](#the-rules-jev-judges-a-post-by)
- [Before it goes out](#before-it-goes-out)

## Limits

He asked for short messages, so the tooling counts each post. It counts the words its writer
typed. A link or a `{card:…}`, `{pr:…}`, `{order:…}`, `{record:…}`, `{answer:…}`, `{started:…}` or
`{at:…}` reference counts as the words after its `|`, or as one word. What `say` adds itself, such
as a card it draws, counts for nothing.

| What he reads | At most |
|---|---|
| A line in his thread | 60 words |
| A summary of two or more features, each under its own bold line | 120 words |
| Each row of a markdown table, which `say` draws as Slack's own table | 60 words, its words outside the post's count |
| A question card: question, summary and options together | 150 words |
| A question card's Show me, the worked example between its summary and options | none: drawn whole as its visuals, outside the 150 |
| Each option on a card | one line of 25 words, saying what he gets and what it costs |
| Each option's plain-text description, drawn under it on the card | 60 words, drawn whole outside the 150 |
| A findings post | none goes out: findings stay on disk, and Explain more posts the part he asks for |
| An answer to a question or order of his in that thread, his words quoted as `asked` | no limit: the whole answer goes out |
| An edit that rewords a line already posted | that line's own length and 10 words more, or the line's limit when that is longer |

- An answer goes out whole because a cut answer loses what he asked. Pass `asked` to lift the
  limit: each message of his the post answers, one per line or list entry, with `…` where you skip
  words, or the message's permalink in his DM. A reply to his Explain more tap passes
  `asked="Explain more"`. A post outside the thread his message is in, such as a share he ordered to
  another person's group DM, passes the permalink.
- The hook looks for each quote in his messages in that thread, in his answers on its cards and in
  each message an `asked` permalink links. One found quote is enough, and straight and curly quote
  marks match alike.
- Text passed beside a question card is drawn atop its body and counts toward its 150 words, so
  post the card with no text beside it. A Show me counts toward none of them, so do not cut one to
  fit.

## The short form

- Lead with what changed for him, in his words, not with how the machinery got there.
- Write a bold line and at most two sentences. Use a table for three or more parallel items.
- Keep a thread about that thread's work: its open orders, their jobs and the cards and PRs those
  wait on. Reach past it when his words there name more, such as a project or the whole drive.
- Drive tooling news goes in the thread his words ordering it came in, else at his DM root under
  its own words. It does not go in a feature's thread it only unblocks, where it makes no sense to
  him. A tooling order a session opened on its own, with nothing posted yet, closes on its proof and
  posts no fix-live line: the dashboard shows it live.
- His DM gets four things: the answer to a message of his, a card he must answer, a milestone he
  acts on or wants to know (a fix he reported is live, a release started, finished or failed, a PR
  of his landed) and an incident's line. Everything else is progress. It goes to the dashboard and
  the records, not to Slack, and costs no model call.
- The coordinator sends a `POST` report only for a line he gets, one per thread per turn, because
  four posts of 60 words are the same wall as one of 240. A turn runs from a wake to the end of
  that response. The report's first sentence says what changed for him, with each item that waits
  on him first and its card named by permalink or `waits` id. The communicator writes the post from
  the report's facts. A report opening `FYI` is context for the communicator and posts nothing.
- Only cards have buttons, so anything that asks for his action or decision is a card. When a line
  names a card or says something waits on him, write `{card:<its waits id>}` in `say`'s text. `say`
  links it under words that name it, and Slack draws the card with its buttons under the line. In
  the card's own thread `say` writes it as the words after its `|` or as "this card", with no link.
- A line in his DM that is news on something an open card of his already asks about names that
  card too, so he reads it as that card's news. Outside his DM, readers cannot open his card, so
  leave it off.
- Do not paste a bare URL or write a link whose label is its own URL. Slack shows the first as a
  raw address and does not draw the second.
- Slack draws a message's first five Slack-message and alert links. Put each card on its own line.
  cc-slack splits a post to his DM that links more than five into pages so every card draws. One
  line cannot link six.
- A table cell draws no card under a link. Link each card on a line under the table instead. This
  matters most for a row saying a PR waits on his tap.
- A line that waits on him and links a PR draws that PR's tap card, as `tap_prs` does. A PR that is
  merged, or one the card command skips, draws no card, so name it in a line that waits on nothing.
- A post promises him no next line, and no time a post names becomes a line the order owes. A
  thread owes no line for being quiet.
- The one cadence is his own. When his words ask for a line every so many minutes, `drive order add`
  reads "every 15 mins", "every half hour" or "every hour" and sets that cadence on the first open
  order whose lines go in that thread, else on the order it adds. In that order's thread each line
  and each edit of one names its next line within those minutes, as "Next line by 9:36 PM.", and
  `say` refuses one that names it later or not at all, printing the latest time it may name. A card,
  a PR card, a line closing the order and an incident's line name none.
- The communicator records its post on the order through the post itself,
  `say(record=<the report's seq>)`.
  That delivers each part the report's `part` attr names, or every part of an order its `close`
  attr names. A progress report carries `progress=true` and delivers no part.
  Built work is delivered by its proof passing under `drive job prove`, not by a post. A part
  `drive order set <o> --kind answer --part "<part>"` sorted as an answer is delivered by its post.
  `drive order posted <o> <link> --record <the report's seq>` records a post already made, and
  `drive order posted <o> <that post's link> --part "<part>"` marks a part an earlier post answered.
  The communicator's side records posts, so `say` from another session records nothing on an order.
- An incident and every alert's order joined to it with `--joins` share their threads both ways.

## The shapes

**An answer.** The communicator posts it in the thread of his message, with `asked` naming that
message, as soon as its reads pin it. No ack goes out first: cc-slack already put the eyes on his
message, and the thread's status, set with `slack_dm_status`, carries the progress.

**A summary.** The communicator writes it from a `POST` report and posts it with
`say(text=…, thread_ts=<his thread>)`: what changed for him since the last summary, grouped by
feature. Each feature gets one bold line and at most two sentences under it, 120 words in all. A
summary of one feature is a line of at most 60 words.

```
*<feature>: <what is true now, for him, in under 12 words>*
<one or two sentences: what it means and what happens next>
```

**A question card.** A card is for what is his to decide: product scope, an architecture fork with
lasting cost, his own workflow, money, and the hard limits. Decide the rest yourself and say what
you chose.

- `drive job ask` keeps the job's `ASK` block and draws the card from it in the same call. Nothing
  is posted before or after it.
- The title is the question: one plain sentence naming what it decides by the terms he uses for it,
  such as the memory move or expiry. Slack draws a title and buttons as plain text, so links and
  markdown go in the summary. Code names go below the summary's first line, each with a clause
  saying what it is the first time, as "the claims table, which records the runner holding each
  run" does. Leave out words the drive coined for its own rules and checks.
- The body is the summary, one to three lines. Its first line says what work the card serves, as
  `{order:<o>}`, `port of #N` or Drive tooling, and what his pick changes for him, said as "your
  pick". Then what was found, what is being decided and the recommendation. An `{order:<o>}` draws
  its order's whole title, so write `{order:<o>|<a few words>}` when the card runs near 150 words.
- Say how each thing came about and what it does to the people it touches, not only a count or a
  system's name. Keep paragraphs to three sentences.
- The drive adds the line above the buttons that says what is built while he decides, from the
  card's jobs: built on a branch and paused on his pick, landing on the recommendation on an open
  incident, or nothing built until he picks. Leave that out of the summary.
- The findings in the block carry labelled lines the code reads:
  - `Root cause:` names the structure that lets the defect happen, with a number that measures it.
    Its forms are `Root cause: <the structure>`, `Root cause: fixed: <the structure>, removed by
    <the merged PR's link>`, `Root cause: not found: <what measures it>, by <the job measuring it>`
    and `Root cause: none: <why>`. The card draws it under the summary's first line as
    `*Root cause:*`, at most 60 words, outside the 150.
  - `First principles:` names, by its words, the option that removes the root cause. That option is
    recommended and comes first in the block. Each other option says it keeps the structure and
    what that costs.
  - `Simplest:` names, by its words, the option that is the simplest design doing his words.
  - `Interim fix:` is for a bug hurting someone now: `Interim fix: <that fix>, by <the job building
    it>` with its link, or `Interim fix: none: <why nobody is hurt now>`. The card draws it under
    the root cause as `*Interim fix:*`, at most 60 words. A bound, cap, retry or time budget goes
    here, not on the root cause line.
  - `Holds:` names each PR the options alter, by link or `#N`, and each branch building an option,
    in backticks, or reads `Holds: none: <why>`. A reader's or design's also names each change of
    the order its options alter. His open card holds those PRs from landing. A `Drive tooling:`
    order's card holds no PR of his product.
- Give two to four options. Each is one line of at most 25 words saying what he gets and what it
  costs, and each stands alone, because cc-slack draws the recommended option first and renumbers
  the rest. Do not write "as 1" or "same as 2". cc-slack adds Other, which takes his own words, and
  Explain more.
- Under each option, indented two spaces, goes one plain-text description of at most 60 words,
  which `drive job card` passes as the card's `hints`. It says what the option is: what runs and on
  what trigger, where its data lives and how it is written and read, and what it touches or
  builds, by name, with any cost every user pays. File names go here, since the card draws them as
  code and the title and options take no markup.
- Name a mechanism by what it does, such as "the Mac reruns the check function every 30 seconds
  and compares its output; a change sends one inbox message that wakes the agent". A bare
  "polling" or "the event system" does not tell him what is polled or what wakes whom.
- No option asks a reporter or anyone else to retry or confirm, because the drive proves a fix
  itself.
- List every member of a set the card counts, as "four strategies: spill values, insertion order,
  hash by key, and equal cut" does. Give him an option for each amount his words named, such as 30
  days.
- The findings stay in the job's file. Explain more on the card posts the one section its question
  rests on, in plain words.

Its Show me:

- A card that offers a design to build, a fix included, works each option out to what it will look
  like once built: its real calls, the rows or bytes it writes, its flow, or the reply or screen a
  caller gets. A post in the card's thread that explains an option shows it the same way.
- Any other card has a Show me when it shows what the summary and options cannot: the flow between
  the parts, the numbers the choice turns on, or the options side by side. A constant or a call the
  words already say needs none.
- In the block it sits under a `Show me:` line, between the summary and the options. The card draws
  it under `*Show me*`.
- Fill every cell with real contents, measured or worked out from the code and marked as such.
- Each visual is one fence whose language names its kind. `lib/drive/site/visuals.js` draws that
  source for each surface. Slack gets a picture of it, except a chart, which Slack draws as its own
  [chart block](https://docs.slack.dev/reference/block-kit/blocks/data-visualization-block), and
  short code that illustrates no shape, which shows as Slack's code block. The dashboard draws each
  kind natively. A markdown table is native on both.
- Pick the kind that shows what the words cannot:

| The question turns on | Kind | Fence holds |
|---|---|---|
| the shape of an API, call, query or schema the options change | code | the real calls, in a fence naming its language, such as `sql` or `go` |
| components, data layout or flow between them | arch | `{"arch": 1, "name", "title", "nodes": [{"id", "kind", "name", "role"}], "edges": [{"from", "to", "label"}]}`: each id lowercase letters, digits and dashes; kind actor, app, service, worker, store, log, external, artifact or doc; a store or log node adds "medium", one of memory, disk, bucket, keys or pages; an edge may add "kind", one of sync, async, copy or control. Options that choose between architectures carry one `arch` per option, captioned `N · …` |
| routing, ownership, steps over time, a state machine, a small graph | mermaid | Mermaid source |
| a graph whose layout carries the point, such as clusters of what runs where | dot | Graphviz source |
| numbers: sizes, counts, timings, before and after | chart | a Vega-Lite JSON spec with its data inline: a bar, line or area mark, or an arc for a pie |
| a tradeoff between options' code | compare | `{"options": [{"name", "recommended", "language", "code", "points": ["+ a gain", "- a cost"]}]}`, drawn side by side, each `code` real code under its language; words go in `points` |
| words compared across rows | table | a markdown table of two or more columns, which cc-slack draws as a native Slack table |
| a draft message or a quote | quote | its sentences, each line opening with `> `, which Slack shows as its own quote, links and all |
| a UI or anything only a picture shows | image | `![what it shows](/absolute/path.png)` on a line of its own, a screenshot or a generated picture; options that differ in how a screen looks carry one real screenshot each, captioned `N · …` |

- Put a caption line of at most 25 words just above each fence. `drive job card` makes it the
  picture's title, as `say` does with a post's `visuals` caption. A fence with no caption is titled
  by its kind, such as "go code" or "Diagram".
- Two or more pictures draw as one gallery. Open each caption with its option's number and a middle
  dot, as `1 · Feed first`, the recommended option being 1.
- A chart's title is at most 50 characters and each label at most 20. It draws at most 12 series or
  slices and 20 categories, and a card holds at most 2 charts. Each value is a number, above 0 in a
  pie, and each series holds one row for each category.
- Draw a Mermaid flow whose longest chain runs past four nodes as `flowchart TD`. Left to right, it
  shrinks its words to about 5 px on his phone.
- Keep one kind per fence: a diagram in its own fence and each table as a markdown table. As many
  code fences as options go in one `compare` fence instead.
- Raw data does not belong in a fence: JSON, YAML, logs, a terminal session, command output or a
  stack trace show him nothing. Draw pictures from fences, not by hand, except real before and
  after screenshots.
- A visual that fails to draw leaves the Slack card with its caption alone, and the dashboard with
  its source as text.

Posting it:

- `drive job ask` posts the card, as `drive pr add` posts a PR card. The watch posts on its next
  read any kept question whose card did not draw then.
- A kept ASK file changes through `drive job ask`. `drive job card` and `drive cards redraw` do not
  draw a kept file whose words no longer hash to its name. A question already asked redraws under
  the rules it was asked under.
- A card draws in the thread of his DM the lines of the job's order go in, an open incident's
  thread first, so the card and its lines stay together. With no such thread it draws at his DM
  root. A thread outside his DM, such as an alert's, gets one line about the card through a report
  `drive job ask` sends, and the card draws at his DM root.
- Every question card carries its `waits` id, `<drive>:<job>-<n>`, which `drive job ask` mints. One
  question keeps one `waits` id. Asking an open question again redraws its card where it sits.
  Older copies lose their buttons and link the live one, and his answer closes every copy.
- His tap comes back as an `answer` record, so "card posted" and "he answered" are two states.
- Set `drive` to the drive's name on every card, so cc-slack posts it as the drive's question with
  no cc-present board warning.

When it is answered, replaced or redone:

- Read his words under a card as a careful colleague would. Words that pick an option or say what
  to build answer it: the card closes showing them and the work goes ahead. A question of his,
  such as "could we X?" or "why not just X?", is answered in the thread while the card waits. Offer
  a way it proposes by redrawing the card with `--redraws`.
- To change the words of a card he has not answered, draw it again under its `waits` id with
  `drive job ask <j> <file> --redraws <key>`. That edits the card where it sits and keeps its
  buttons. `drive cards redraw` redraws every open question of the drive the same way. A text edit
  through `say(edit=…)` or `slack_edit` replaces a card's layout and buttons with plain text, so
  redraw instead.
- When an answer he gave elsewhere decides a card, mark it on that answer's `drive answer` with
  `--settled`. The card then shows his words and a link where its buttons were. For a card decided
  only in part, redraw it asking what is left, then mark it with `--settled <key>=<file>`.
- When his later words supersede a card, withdraw it with `drive cards withdraw <its waits id>
  --words "<his words>" --via <his permalink>`. The same verb withdraws a card an event left
  nothing to pick on, with what happened as the words and the PR, build, job or message link as
  `--via`. `drive cards withdraw <its waits id> --via <that card's waits id>` folds a card into a
  newer card that asks the same thing, and each wait on it moves there.
- One question has one card. His answer on any card answers every open question asked in the same
  words, case and punctuation aside, and frees each builder waiting on one. A job that would ask
  what another job's card already asks waits on that card with `drive job set --waits`.

**A PR card.**

- Nobody writes it. `drive pr add` draws it when it registers a PR waiting on his tap, and the watch
  draws it for a PR that enters `uncarded`. A `<guarded org>` PR outside an exempt class lands on
  his Approve tap, by [Scope](scope.md). A port and every other class that lands with no tap get no
  card: he reads "port of #N landed" in the order's thread.
- PRs group by `state.card_unit`: one card per stack his Approve lands, and for PRs in no stack,
  one per repository and order. Sibling PRs of one order draw one card titled as that many PRs of
  one request, and one Approve covers each.
- A PR has one card. The drive redraws it where it sits when a head or staging line changes. A PR
  added to the stack later gets one new card for the whole stack, and cc-slack marks the older
  cards replaced with a link.
- `say(tap_prs=[<each url or owner/repo#number, bottom first>], text=<line>)` posts your line and
  makes cc-slack draw one card for those PRs right under it, with Approve and Hold. The line goes in
  the thread the call names, else the first open order's, and then in the thread of each other open
  order those PRs carry out, with the card's link. An incident thread outside his DM needs
  `channel_id` and `thread_ts` on the call. The call's `card_permalink` names the card and its
  `permalink` the first line. The drive must hold the PR first, through `drive pr add`.
- When his open card already asks the same thing at the same heads, link that card instead of
  passing `tap_prs`, so nothing moves and he is not asked twice.
- Approve lands the PRs still asking his tap, bottom first. Hold holds them all and stops only
  their landing. One Approve on a stack card lifts each Hold and lands the card whole. Outside an
  incident, a PR that a question card of his holds asks no tap until he answers that card. On an
  incident, his Approve on the repair also picks the recommended option of each card holding it.
- The card opens with each PR's lead, the four lines its builder gave `drive pr add`: the headline
  as the title, then why the change is made, what it does and what it changes downstream. Each PR
  then gets a section: title, link and Open button, its Summary, its Refresher, its biggest
  surprise and what he will hate most, and how it differs from the plan when it does. A stack card
  is titled by its `## The stack` line, else its bottom PR's headline. Its Show me comes last.
- The card's words come from each PR's title and body, so fix a bad card in the PR body.
- Once he answers, the card folds to its title, his pick and one line per PR.
- When his words rework a PR on a card, name the PR and the permalink of those words with
  `--rework <PR>=<his permalink>` on `drive order add`, `drive order set` or `drive job set`, or
  use `drive answer --reopened`. That takes the card down with his words as the reason. The PRs
  below the reworked one go up on a card of their own. The reworked PR gets a fresh card once its
  builder's `READY` says in its `CHANGE:` line how his words are met.

**A confirmation of a skill update** goes out twice for each message of his that leads to one. The
answer to his message says what is changing in the skill, in plain words. A summary then says it
is merged and installed, listing each rule now in the skill.

**A fix-live note in a bug report's thread.**

- Whoever reported a bug the drive fixes hears once, in the thread of the report, when the fix is
  proven live. He granted this standing, so it needs no card and no approval.
- The order keeps the reporter's thread from the share or link in his words. A second report of
  the same bug joins it with `drive order set --reporter`, and `drive status` says whether each
  thread heard. Orders on the same fix share one note per thread.
- Proven live means the order's `--proof`, or a passing `drive job prove` on a PR a job of the order
  opened. The communicator posts the note with `slack_reply` in the same step as his own line,
  then records it with `drive order told`. The order closes once each note is recorded, and the
  watch says `TELL` when a proof sits 10 minutes with no note.
- When the bot cannot post in that thread, as in a person's 1:1 DM with him, the note goes in the group DM of that person, him and the bot: one `say` whose `channel_id` is the
  person's user id, no `thread_ts`, recorded with `drive order told --group-dm <that user id>`.
- A question someone asked him that a `--kind answer` order answers gets its answer in that
  person's thread once the answer is posted to him. His share of the question orders it.
- Write one or two plain sentences: what is fixed and since when, with the link. No drive jargon
  and no asks such as "please retry". Post nothing else in that thread.

```
Fixed: the agent picks up again right after you answer its question, on today's build.
```

**The channel canvases.** Each channel the drive keeps has its own canvas, the channel's tab, which
the watch redraws within a minute of a change to its words. The drive's own channel's canvas has
one line per project or priority channel, linking that channel and its dashboard queue with its
counts of open orders and waiting cards. A project or priority channel's canvas lists what waits on
him there, then its open orders, one line each, each linking its dashboard page. No canvas goes in
his DM. `drive view` prints the whole status page. When a line says where things stand, link the
channel's canvas or the dashboard instead of restating it.

**The Waiting on you post.** The watch keeps one pinned post at the root of his DM and of each
operator channel, edited in place when what waits there changes, so it pings him once. It counts
the open cards whose project posts there, links that queue on the dashboard, then links the first
five, those fixing a bug hurting someone now first.

**The daily digest.** Once a Pacific day the watch posts one table in his DM linking each question
card asked more than 24 hours before. It is the one place an old card is raised again, so a thread
line does not name a card the digest lists.

## When it goes out

- His question gets its answer as soon as the answer is known, in one post. The communicator reads
  the source that owns the fact through whatever access opens it and posts with one `say` naming
  his message in `asked`. Do not post "looking into it" or a time for the next line first.
- While the communicator compacts, cc-slack posts "Got it, back in a minute." under his message
  itself, and the message arrives with `acked`.
- A message of his that cc-slack has owed an answer for 10 minutes goes back to the communicator
  as an `ANSWER THIS:` note from the watch, as does each `POST` report and each new message in a
  thread his order watches.
- A later step of an order goes to him only when it is a milestone: a fix he reported is live, a
  release started, finished or failed, or a PR of his landed. The watch posts each one its rows
  hold through `say`, with no model call but Jev's read. A release starting, ending or failing
  that no follow tracks goes as a `POST` report with `--attr milestone=true`, which the communicator
  posts. A job returning a finding, a PR going up, checks settling, a card opening or closing and a
  job at work are progress: the dashboard shows them, the records keep them, and no line goes in
  his thread.
- When he asks to be kept updated on a `<ci>` build, the session holding the order runs
  `drive order set <o> --follow <build link> <his ask> [--part "<part>"]` once the build exists.
  The watch then keeps one status message in his thread current with the running step, and at the
  build's end posts the end line itself, citing the record of the build's end, which delivers the
  part. It follows an ask in his DM.
- An incident's line goes to him the moment its step happens, by [incidents.md](incidents.md): the
  session holding it sends the report and the communicator posts it. No clock owes an incident a
  line after quiet minutes.
- These four kinds of line go out whether he is here or away. He reads away when he says he is
  going to sleep or stepping out, which `drive heard --away` records, else an hour after his last
  word or tap. Each tap records him heard as a message does, and `drive status` prints the away.
  What his absence holds is a dangerous merge or release, not a post.
- Correct a post when it would mislead him on a fact. Reword it in place with
  `say(edit=<its permalink>, text=…)`. A report `re` the seq of a `POST` report your lane sent goes
  out as a `correction`, and the communicator rewords the post that report went out in.
- When a job built on a card's recommended option starts waiting on that card, the watch replies
  once under it: the recommended answer is built and paused on his pick. No other post repeats it.

## What goes in

The voice, as a short list:

- What changed for him comes first. "You can now tap Approve on any open PR card and it lands"
  beats "the rule that pulled your card when checks went red is merged and gone".
- Use the proper term for anything technical: a table, column, function, file, migration, flag,
  service or code path, with a clause saying what it is the first time. Use the word his messages
  or the code use, not a stand-in such as "the copy change". When he corrects a word, run
  `drive term <the word> "<the proper term>"` so later posts are checked for it.
- Keep it short. Name what the message needs, and a mechanism when he asked about it.
- Link everything he can open where it is named: a PR, a card, a release, a build, an alert, a
  thread. Link the exact view the words describe, and call a view that is not built yet unbuilt.
- Leave out drive ids he cannot open: a job, order, record or row id, a `waits` id, a hash, a plan
  step. Say what the thing is instead.
- Say unverified on a line you did not check. A fact told to him is checked that turn, from
  `drive status`, GitHub, the default branch or the release, since a job's last line may be stale.
- Every line stands alone, because he reads it with no other context. Give a number what it
  measures and one concrete example. Do not point at "the third finding" or "as above". Do not say
  sorry: say what was wrong and what is fixed.
- Numbers beat adjectives: "129 of 476 passed, up from 100".
- Answer his next question in the same message: why, is it a port, what comes after, why it is
  held.
- Done means live and checked on the surface he uses: a recording of the click for a page, the
  live post for a message, the installed command's output for a tool. Say how you checked in a few
  words, with the link.

The references `say` expands:

- `{card:<waits id>}` links a card under words that name it and makes Slack draw it.
- `{pr:<number>}` links a PR the drive holds under its title. `{pr:<number>|<what it does>}` links
  it under your words. Name any other PR as `{pr:<owner/repo#number>|<what it does>}`. Say what a PR
  serves: his order in his words, "port of #N" with the old PR linked, or "Drive tooling:" and what
  it unblocks.
- `{order:<id>}` links an order to the thread its lines go in, under the first line of his words.
- `{answer:<id>}` draws a ruling of his as when he said it and its first words, linked to the ack
  of them in his DM.
- `{record:<seq>|<what that read measured>}` links a record's `--url` under your words, so the
  evidence is one tap away. Write the words after the `|`. Use it when a record holds the read
  behind a cause or a number you state.
- `{started:<job>}` draws when a job started, labelled as the job's start, and `{at:<seq>}` when a
  record went out. `say` fills both from the drive's stamps. Use them for how long something has
  waited.
- A post may pass `claims`: each factual line beside the read behind it, as `record:<seq>` or
  words copied from a call of its writer's, such as a path, command or query. Jev checks each claim
  against that read, except on the communicator's own posts, which pass the mechanical checks
  alone. A bare `{pr:}`, `{card:}`, `{started:}` or `{at:}` is not a claim. A claim
  about every member of a set stands on a read of what decides it, not of one member.

Other facts:

- A line about code stands on your own read of that code. A comment, an error message, a doc, the
  brief or an earlier answer is a claim to test, not a line to repeat. A line saying nothing
  happened, read from one metric or log series, is stronger with a second source, since one series
  can be sampled or dropped.
- Slack draws `<url|label>`, not `[label](url)`. A link's words hold no other link or reference. A
  card's title and options draw no links, so link what they name in the summary or a Show me
  caption.
- A line that says something waits on X says since when, with `{started:<job>}` or `{at:<seq>}`,
  and whether X is running. Past two hours with nothing running it is a failure: say who owns it
  and how it is fixed.
- An incident line follows [incidents.md](incidents.md). Its alert's Slack message and Sentry or
  Datadog issue stay as bare links so Slack unfurls them.
- Ask for a tap when one is owed. `drive status <pr>` says whether he tapped already or the PR
  merged or closed. Read a quiet job's transcript with `drive job replies <j>` before calling it
  stuck.
- The coordinator writes its `report` records in this voice, since the communicator writes from
  them.

## What stays out

- The machinery: pins, packs, installs, who handed what to whom. When a failure of the drive's
  reaches him, the post says what fixes it, with the `{order:}` or `{pr:}` carrying the fix. When
  he asks why, give the mechanism in one or two sentences, then the fix.
- A second ask for a post he ordered. His order to post is the permission for that post in the
  place he named. A post in his own name to another person that nobody ordered waits for his word,
  on a question card. The fix-live note and the answer to a question he shared are already ordered.
- Messages with no new fact. React with `+1` to chatter. Post no "nothing new" line, and pass on no
  idle notice or result he already has.
- Second copies. One post per thing, and not text and a card for the same thing.
- A line saying a card or its buttons are coming. Post the card, or name the open one as
  `{card:<waits id>}`.
- The current time, and a typed time for work already done. Slack stamps every post, and
  `{started:<job>}` and `{at:<seq>}` give a job's or record's time. A job's stamp is not when a
  release, build or deploy started: quote that as its build or tool printed it, in backticks, on
  the line linking it, unless it is a log's UTC stamp such as `09:15:42Z`, which `cc-slack check-post`
  refuses even in backticks and which goes in Pacific instead. A time a tool or query measured, such as an outage window, is written in
  Pacific on the line linking that read. A SendMessage, a defect, a handoff and `drive job report`
  quote times as they stand.

## Examples

Two status lines that lead with what changed for him, with concrete counts:

```
*Your PR cards keep their buttons while CI is red*
Tapping Approve on any open PR now lands it once CI passes.
```

```
*1 of 5 tooling fixes is merged; none is on your Mac yet*
The other four are in review; each goes live within minutes of merging.
```

A design question goes out as its card and nothing else: about 120 words with its options, its Show
me drawn whole above them, and no findings post before it.

````
*How should a finished run's files be laid out so its page opens without guessing?*
A finished run is about 180 small files, each with its page map at the end, so the browser guesses where each map starts.
Recommended: one snapshot file per finished run, so any finished run opens in 3 requests.
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
⏸ *Being built on a branch while you decide, paused on your pick.* Tap an option or reply with its number, or tap Other to write your own answer.
````

### A post that explains

An answer to his question, a reply to his Explain more tap and a walkthrough he orders for a
teammate go out whole, so each reads as a short worked lesson. When the repository's `AGENTS.md`
names a model post, write the way it does.

1. Open with one line saying who asked, why, and what example follows, so a reader who saw nothing
   before it knows what they are reading.
2. Put a bold lead before each part, in the order the reader's questions come, one idea under each.
3. Show each mechanism as a short snippet of what the code does, the real call where one exists,
   then one plain sentence on what happens when it runs.
4. Define each proper term where it first appears, in a few words in parentheses.
5. List a cost as the concrete things the change adds, read from the diff, since a list can be
   checked and an adjective only argued with.
6. End a post that teaches on a test the reader can reuse.
7. Address the reader by name, warmly and directly, and leave out any account of your own process.

A review walkthrough's leads might read *What your PR does*, *What we want instead*, *Why it
matters* and *A test for next time*. A routine line keeps its bold line and two sentences.

## The rules Jev judges a post by

Jev is the judge `say`'s hook asks about a post. It reads a post only when a check finds something
in it or its claims name reads, and judges each finding by its rule beside his words. The
communicator's own posts skip Jev: they pass the mechanical checks alone, such as a placeholder
left in the text, so its answer goes out the moment it is written. The watch's posts keep every
check.

| Rule | Found by | Words in |
| --- | --- | --- |
| A post stands alone: it names nothing its reader never saw, such as another draft of itself, a drive id or his DM, and says no sorry | `cc-slack check-post` `apology` and `earlier-draft` findings | `ALONE_RULE`, `hooks/message_limits.py` |
| Plain words for the person reading the thread: no internal shorthand, and each table, column, function, file, migration or flag glossed the first time it appears | every other `cc-slack check-post` finding | `PLAIN_WORDS_RULE`, `lib/drive/agents.py` |
| Anything that says it waits on him carries the card with its buttons | `cc-slack check-post` `waits-without-card`; a `{pr:}` on a line asking his tap links his card | `CARD_RULE`, `hooks/message_limits.py` |
| Each claim stands on the read it names, and a claim about a whole set reads every member | Jev's claim items on a post passing `claims` | `settled.post_verdict` |
| An answer under a card explains each term of the card's first line his question turns on | Jev's card item | `settled.post_verdict` |
| A post in a thread of his DM answers from that thread's work; one naming other work only warns | Jev's scope item and `cli.thread_strays` | `settled.post_verdict` |

A check that cannot finish lets the post out unchecked with a warning, since a gate that cannot
finish fails open. Only a count refuses outright: a post over its word count.

## Before it goes out

- `say` posts the words you pass and adds none. Its hook runs them through `cc-slack check-post`
  and counts them, each reference read as the words after its `|`.
- A post Jev holds comes back unposted with the finding and its reason. Fix the words and call
  `say` again. When the finding is wrong, say the same thing another way so the answer still
  reaches him, and send the meta session one defect record. A held post is not a reason to leave
  him without his answer.
- Read it as he will, on a phone, with no other context. Does he know what changed and whether he
  must act? Does every number, term and reference make sense with nothing else open, and does each
  thing he can open carry its link?
- When he faults a post, fix it in place. His feedback also becomes a drive tooling order for the
  meta session, by [meta.md](meta.md#records-and-inputs), so the library or the check changes and
  the next post of that class comes out right.
