# Messages to the owner

Only the communicator's side writes to him, in the shapes below and on the schedule below, from his
own messages and the `report`, `ready` and `ask` records the coordinator sends. The communicator
posts the ack alone in its first message and nothing else; its handlers write, check and post every
other message, each for one message of his or one record. The coordinator's record carries the facts
and the handler writes the words, and a handler answers his read-only questions itself. Each message
is short, says what changed for him, and carries the card with its buttons whenever it says
something waits on him.

The tooling holds every message to a length, counted as the post goes out, a link as the words of
its label, so a report record names no cap of its own. These limits are the coordinator's reading
of his Nov 9 9:33 PM words, "What the fuck is this insane shit you're sending me your messages need to be way
shorter rewrite this for a human", said under a findings post of about 900 words:

| What he reads | At most |
|---|---|
| A line in his thread | 60 words |
| A summary of two or more features, each under its own bold line | 120 words |
| A question card: question, summary and options together | 150 words |
| A question card's Show me, the worked example between its summary and options | none: drawn whole as its visuals, outside the 150 |
| Each option on a card | one line of 25 words, saying what he gets and what it costs |
| A findings post | none, ever: findings stay on disk |
| An answer to a question or order of his in that thread, his words quoted as `asked` | no limit: the whole answer goes out |
| An edit that rewords a line already posted | that line's own length and 10 words more, or the line's limit when that is longer |

The long-running plugin's hook refuses a post in his DM over its limit, from the communicator or
any handler, with the count, the limit and what would pass, and `drive job ask` refuses a job's
`ASK` whose card runs over it, or that cc-slack would refuse to draw, in cc-slack's words. Text
passed beside a card is drawn atop its body and counts. Routine lines and summaries stay short. A line that answers a
question or order of his carries the whole answer, at any length: what he asked, a ruling he gave,
or his tap on Explain more, which the handler's brief quotes. On Nov 10 between 3:50 and 3:56 AM his
"figure out how it got into a bad state" lost its answer four times to the 60-word cap. The call
passes as `asked` each message of his the post answers, word for word, one per line or one list
entry each, with `…` where it skips words. The hook finds each in a message of his in the thread
the post goes to, read from Slack, or in the handler's brief, with curly and straight quote marks
alike, and one found quote is enough: on Nov 10 at 4:37 AM the 236-word answer to "What’s wrong
with how it is now?" was cut to 59 words, and that question stood in another thread than his
follow-up the post went to. A refusal says which limit applied and why: no `asked`, so not an
answer; quotes it could not find, named; or an edit's own length. An edit that rewords a line is
held to that line's length and 10 words more, since putting the proper terms into three 55 to 70
word lines on Nov 10 grew them by 2 to 6 words and the cap cut what they said. A question card keeps
its 150 words whatever it quotes, and its Show me counts toward none of them and is never cut to fit them: on
Nov 10 at 4:55 PM a handler drew "a short code sketch, not the full Show me, because of the 150-word
limit".

## The short form

- Lead with what changed for him, in his words, never with how the machinery got there.
- Ack each of his messages inside 5 to 10 seconds, alone in the communicator's first message;
  cc-slack has put the eyes on it already, and acks it itself while the communicator compacts. A
  question of his that reaches the coordinator gets a `report` of what it has in the same time, and
  the read follows in a second `report`. Every other post is a handler's, a summary at most every 30
  minutes per thread, grouped by feature. While he is away only cards post, and his next message
  gets one catch-up.
- Short, held there by the tooling. These limits are the coordinator's reading of his Nov 9 9:33 PM
  words, "your messages need to be way shorter rewrite this for a human": a line in his thread is
  at most 60 words, a summary of two or more features at most 120, a question card's question,
  summary and options at most 150 in all with its Show me drawn whole outside that count, each
  option one line of at most 25 words saying what he gets and what it costs, and no separate
  findings post, ever. This plugin's hook refuses a post in his DM over its
  limit, from the communicator or any handler, with the count, the limit and what would pass, and
  `drive job ask` refuses an `ASK` that would draw a card over it. An answer to a question or order
  of his in that thread, a ruling's installed confirmation included, goes out whole once the call
  quotes each message of his it answers as `asked`.
- A bold line and at most two sentences. A table only for three or more parallel items.
- Link each thing you name that he could open, where you name it, by [What goes in](#what-goes-in).
  A message that mentions a change names it, its PR's link with link text saying in one line what
  the change does, or it is not sent: never "a fix", "the change" or "a cc-slack fix" alone. A
  handler given a record that mentions a change without its PR finds the PR before it posts, with
  `drive status` or `gh pr list --search`, and names it. His words, Nov 10 8:46 PM and 8:47 PM:
  "fucking say what it is jesus" and "why the fuckl di yuou think it was acceptbale to send the last
  message and omit that". The line says what each PR serves: the order of his it carries
  out, in his words, "port of #N", or "Drive tooling:" and what it unblocks. Anything else goes in by what
  it is, never by a name he has not used himself, such as a pipeline step, an id or a function name.
  Name only what the message needs, and no mechanism he did not ask about.
- Every card and line in a feature's thread is about that feature. Drive tooling, such as a fix to
  the launcher its builders start from, goes at his DM root under its own words, never in the
  thread of a feature it unblocks and never under his words for that feature. His ruling, Nov 10
  3:21 AM, on a launcher fix carded in his journal thread: "Why are you talking about sprites and
  dev tooling for run journals? This makes no sense at all?"
- Every line stands alone, since he has no other context. A number, fraction, size or ratio says
  what it measures, why that value, and gives one concrete example; a mechanism gets one plain
  sentence before it is used; nothing points at a list, finding, thread or PR he cannot see in
  that same message, such as "the third finding" or "as above". His ruling, Nov 9 9:12 PM, about
  a ready line that said "a sixteenth of it, at most 64 KiB": "What is the sixteenth? You say it
  with no context you always have to assume I don't have any other context". The coordinator's
  `report`, `ready` and `ask` records are written the same way, since a handler copies their words.
- Done means live and checked; say how in a few words, with the link. A line to him, and every
  `report` body behind one, says what was observed and never lists what is not proven, under any
  label: each unknown gets a job, and he gets its result or nothing. A job's `UNPROVEN:` list is the
  coordinator's input, never copy for him. An incident line carries only a step of the fix with its
  time, its proof on a real machine and users having it, or what is running and when the next line
  lands, plus what the incident's record holds about the cause, quoted, by [incidents.md](incidents.md):
  no side facts about a release.
- A report is one record per thread per coordinator turn, opening `POST` or `FYI`, never a burst:
  everything a turn has for a thread goes in that one record, written at the length of the post it
  becomes, a line of 60 words or a summary of 120, so the handler posts it as written. Its first
  sentence says what changed for him, the one sentence no cut may take. The coordinator cuts what
  does not fit, keeping each item that waits on him first, and never sends
  the rest as a second record: four posts of 60 words are the same wall as one of 240. On Nov 10
  the handler's cut took lines out of five reports written longer, an incident line among them.
  Each item in it that waits on him carries its card's permalink or `waits` id, so the handler
  never searches for the card.
- Any line that names a card, in a summary, a catch-up or a link line, links the card's own
  permalink under words that name it, `<permalink|the lock card>`. The line reads as those words,
  Slack draws the card with its buttons under the line, and cc-slack posts no copy of it, so the
  card stays one message he answers where it sits. Never the bare URL, which shows him a raw address
  above the card; never the URL as its own link text, `<permalink|permalink>`, which Slack never
  draws; and never a copy of the card's words. Slack draws only a message's first five links, and
  none it drew in the same thread a moment earlier, so the words alone must still say which card it
  is. His words, Nov 10 10:02 PM, under a line holding nothing but a card's bare URL: "dont just
  paste the URLs embed the cards properlt in the slack native way". At 7:31 PM, under "The first
  needs your tap: <permalink|permalink>", which drew no card: "this should have ben an embeeded
  exopanded card, instad its just a random URL", and 7:32 PM: "you keep doing variants of not just
  embeddingf htre card and havingf slack unfurl it: you copied it, yuoiu put the bare link, but why
  not teh slack-native thing?". They repeat Nov 9 1:49 PM, "Anything that says waiting on me you
  always need to embed the card with the buttons", and 6:19 PM, "if u are ever asking for my pick it
  has to embed and unfirl the link to the card that has buittons to action". cc-slack refuses a line
  that says something waits on him with no open card linked, or that shows a card's link as a raw
  URL, and `check-post` flags both first. Nothing else has buttons.
- In a bug report's thread the drive posts once, after the fix ships: plain English, no jargon, and
  no asks such as "please retry" or "please confirm". His ruling, Nov 9 1:53 PM: "we have
  authorization to message once in bug fix threads after we ship the fix … it needs to be in plain
  simple English no jargon, no crazy shit, and no asks". No card option offers to ask a reporter
  anything.
- One copy of everything. A correction edits the message in place.
- A time promised to him is set with `--due` by the coordinator in the same turn, so the watch
  holds the drive to it, from Slack's own record and never a session's word: `drive order add --due`
  counts from the ack that named the time, `drive order set --due` from the next line in the order's
  thread, and `DUE` repeats every 10 minutes until a drive post is in that thread. The handler
  records each line it posts there with `drive order posted <o> <link>`: a line that delivers a
  part names it with `--part`, and a progress or incident line names none, so it keeps the time
  and delivers no part. His ruling, Nov 9
  5:24 PM: "fix your toolking so tracking this stuff beciomes deterministic and it cant get lost at
  the whims of an agent".
- No post in his DM goes out unchecked. The hook runs `cc-slack check-post` on the exact words of
  each post and edit, the ack's included, and refuses one it flags. A handler's new line in his
  thread also waits for that handler's own read of the thread in the last 10 minutes.

## The shapes

**An ack**, in the thread of his message, posted alone in the communicator's first message,
inside 5 to 10 seconds; cc-slack put the eyes on his message the moment it landed. It is at most
one sentence the communicator already knows, restating what he asked and what happens next, and is
never read for, drafted or checked. It names no time:
the coordinator sets the order's due time, and the first summary says it.

**A summary**, in a thread, which a handler writes from a `POST` report and posts with
`slack_dm_status(text=…, thread_ts=<his thread>)`: what changed for him since the last one, grouped
by feature, one bold line per feature and at most two sentences under it, 120 words in all. A
summary of one feature is a line, 60 words at most.

```
*<feature>: <what is true now, for him, in under 12 words>*
<one or two sentences: what it means and what happens next, by when>
```

**A question card**, which a handler draws from an `ask` record: the job's `ASK` as `drive job ask`
kept it, with nothing posted before or after it. Title: its question, in one sentence. Body: its
summary, one to three lines: what was found and what is being decided, the recommendation, and
what happens meanwhile, then a `*Show me*` line and the block's Show me under it, as written. That
first line is how he gets the results of the investigation before the pick, by his Nov 9 6:22 PM
words, "you need to give me the results of the investigation etc before sahowing this to me". The
Show me is the worked example he picks from, above the options: the real code, call, query or text
the choice changes, before and after, or one example for each option. For an API question it is
the calls themselves in code, such as an imperative API beside the declarative one. His words, Nov
10 4:38 PM, under a design card that had none: "this would also satidyt the rtequriement you care
curently violating by not having a show-me bc then shw show-0me cvould be code snipeptrs". Every
row and cell holds real contents, measured or worked out from the code and marked as such, never
the names of what will be measured later, and `drive job ask` refuses a table with one value in
every cell. His words, Nov 10 7:35 PM, under a card whose table read "time, bytes, disk" in every
row: "this table just says `time,bytes,disk` for everything".

Each visual is one fence whose language names its kind, drawn from that one source on both
surfaces: as a picture on his Slack card and live on the dashboard, by the same module,
`lib/drive/site/visuals.js`. Pick the kind that shows what the words cannot:

| The question turns on | Kind | Fence holds |
|---|---|---|
| an API, call, query or schema shape | code | the real code, in a fence naming its language: ```sand, ```sql, ```go, ```console for a command and its output |
| components, data layout or flow between them | arch | an arch/v1 JSON document, the diagram the dashboard and cc-present draw |
| steps over time, a state machine, a small graph | mermaid | Mermaid source |
| numbers: sizes, counts, timings, before and after | chart | a Vega-Lite JSON spec with its data inline, drawn in the dataviz palette |
| a tradeoff between options | compare | `{"options": [{"name", "recommended", "language", "code", "points": ["+ a gain", "- a cost"]}]}`, drawn side by side |
| a UI or anything only a picture shows | image | `![what it shows](/absolute/path.png)` on a line of its own, a screenshot or a gen-image or codex imagegen picture |

A caption line of at most 25 words sits just above each fence, and `drive job card` makes it the
picture's title: it replaces the fence with `![caption](/abs.png)`, which cc-slack posts as a Block
Kit image block, so the words stay text and the picture holds only the visual. Code is drawn at a
12.5 px font and cut to its longest line, so a one-line call is one short strip, highlighted in the
language its fence names, Sand by its own grammar. A comparison of words goes in as a markdown table,
which cc-slack draws as a native table. Words never go in a fence: a fence naming no language or
`text` is a picture of words, and a visual that does not draw is left off, so either leaves the card
with its caption and nothing in its place; a card with nothing to show has no Show me. His words,
Nov 10 10:30 PM, under cards whose pictures were text: "these images are not useful at all, at ALL
… if theres nothing to show its bette rnot to show anyhting but her we could ahve done a mermaid
chart"; 10:10 PM: "look at these images rthey are iunsnae, they are so big and not syhntax
highlighted at all"; 9:58 PM: "buold out a libary of difernetg visualizaions (charts, code sample,
archietucre diagram, rendered from the same thing that shows it interactiverly in browser, mermaid
dfiagrams, compatre and contracts, and custom image) and then use the approporiuate one each time".
A picture inside a sentence or a table cell stays text, so `drive job ask` refuses one there. The
Show me counts toward no limit.

Two to four options, each one line of at most 25 words saying what he gets and what it costs, the title,
summary and options 150 words in all. The findings stay in the job's file: Explain more on the card
posts the one section its question rests on, in plain words, and that is the only way they reach
him.

The card is one `slack_dm_status(card=<what drive job card prints>, thread_ts=<his thread>)` call,
the card printed by `drive job card <the record's ask file> --waits <its waits id>`, never one put
together by hand. cc-slack posts a card that
names its drive and waits on him at his DM root, whatever thread it is given, so where it sits
never rests on a handler's reading. In that thread it posts the card's permalink under the card's
question, and Slack draws the card with its buttons under it.
A drive tooling card is given no thread, since its order has none. The handler then closes
the `ask` record with a `posted` record carrying the card's permalink and `waits` id. His tap
still comes back as an `answer` record, so card posted and he answered stay two states. A card that
an answer or ruling he gave elsewhere replaces never keeps asking: the coordinator's `drive answer`
settles it in place, showing his words and a link to them where its buttons were. To change
the words of a card he has not answered, draw it again under its `waits` id. That one call edits
the card where it sits, its layout, pictures and buttons included, and posts nothing: every older
copy of it loses its buttons and reads one line linking the card. His answer, Nov 10: "edit the
original message to remove the buttons". Never `slack_edit` a card: an edit with text alone replaces
its layout and buttons with plain text, and cc-slack refuses it. While a card he has not answered is being redone, it says so where it sits and keeps its
buttons. The moment a redo starts, the coordinator sends an `ask` record with `--attr
ask=<the card's kept file>`, `--attr waits=<its waits id>` and `--attr reworking=<what is being
redone, and when it is back>`. The handler draws the card again under that id from that file, its
title, body and options unchanged, with one fact, `Being reworked`, holding those words. A pick he taps meanwhile stands, as on any card.
The redone `ask` comes under the same `waits` id and draws the new words with no fact. His words, Nov
10 4:44 PM: "when the card is being re-wqritten it shgould be updated to say tghat, but still keep
the opotonbs inc ase I want to click ione". No option asks a reporter or anyone else to retry,
confirm or check: the drive proves a fix itself. When something waits on his
answer, set `waits` to `<drive>:<job>-<the ask record's seq>` and `recommended`; when work continues
on the recommended pick, leave both out and say in the body which option is being built. Set `drive`
to the drive's name on every card: cc-slack reads a `waits` card that names its drive as the drive's
question, whose home is Slack, and posts it with no cc-present board warning.

Each option on a question card stands alone. cc-slack draws the recommended option first and
numbers the rest after it, so the numbers in an `ask` record are not the ones he sees. No title,
body or option points at another option by number or position, such as "as 1", "same as 2",
"options 1 and 2" or "the first option". An option that builds on another repeats what that one
does, such as "Own process plus a memory cap per script". cc-slack refuses a card that breaks this
and quotes the words, and the coordinator writes an `ask` record's options the same way.

**A PR card**, which nobody writes: one card per stack, which a handler draws from a `ready` record.
It is only for a PR that carries out an order of his whose words ask for it, or for a `Forge-AI/*`
drive tooling fix no exempt class covers, which serves its own order by [Scope](scope.md). The line
above it quotes that order: his words, or the tooling order's "Drive tooling: …" words, at his DM
root. A port never gets one: he reads "port of #N landed" in its order's thread once it lands, and
so for every class that lands with no tap.
`slack_dm_status(tap_prs=[<each url, bottom first>], text=<line>, thread_ts=<his thread>)` makes
cc-slack draw one card at his DM root for the stack those PRs form (`drive card`), with Approve and
Hold while it waits on him. His Approve lands exactly those PRs, bottom first. A PR added later
gets its own card when posted alone; posted with the rest of its stack, it joins one new card for
them all, and cc-slack marks each older card of those PRs replaced, with a link to the new one.
The same call posts the card's permalink under the card's question in his thread, and Slack draws
the card under it, so that thread carries its buttons. The call's `card_permalink` names the card and its `permalink` only
the line; when the card command gives a PR no card, the call fails with its reason and posts
nothing. Each PR on it gets its own section, opened by its title, link and Open
button, then its Refresher, its biggest surprise, what he will hate most, where it stands and what
it waits on only when that is not green and his tap, how it differs from the plan when it does, and
its Show me with its pictures inline. A stack card's title and opening summary come from `## The
stack` in its bottom PR. Once he answers, the card folds to its title, his pick, and one line per
PR; once an answer or ruling of his replaces the work it carries, `drive answer` settles it the same
way as a question card, with his words and their link and no buttons. Its words come from each PR's title and body, with record ids dropped, so a bad card is fixed in
the PR body, never in the message.

**A confirmation of a skill update**, twice for each message of his that leads to one: the ack
says the skill is being updated and what changes, in plain words, and a summary says it is merged
and installed, listing each rule now in the skill.

**A fix-live note in a bug report's thread**, once per bug, after the fix ships and is checked, never
before: one or two plain sentences saying what is fixed and since when, with the link. No jargon, no
internal names, and no asks: never "please retry", "please confirm" or "let us know". Nothing else
goes in that thread: no progress, no test results, no follow-up.

```
Fixed: Iris picks up again right after you answer its question, on today's build.
```

**The status page**, which you never write: `drive watch` redraws the drive's canvas, a tab named
after the drive in his DM with the app, within a minute of any change. It lists what waits on him
first and in full, then each open feature as one line by state, then finished work as counts with
the newest five named, and ends with the dashboard link. It stays under 40,000 bytes: past that it
leaves out finished work first, then each open state from the bottom, and says what it left out in
one line. Slack stops taking redraws of a canvas once it has been rewritten past a total it does not
publish; the watch then deletes that tab and draws the page on a fresh one of the same name.
Nothing to post; a line that says where things stand links it.

## When it goes out

- **His message gets its ack inside 5 to 10 seconds**, whatever else is running. The ack goes
  alone in the communicator's first message, so no handler brief, record, report, card,
  due line, draft or `check-post` holds the ack. The handler it starts answers in that thread when
  it is done. While the communicator compacts, cc-slack posts "Got it, back in a minute." under
  each of his messages itself, and the message arrives with `acked`, so it gets no second ack.
- **Everything else waits for a summary.** No line per event: a job result, a merge, a CI turn or
  a promised time feeds the next summary of its thread. The watch's `TICK` comes every 30 minutes
  and names the orders with news; the coordinator sends at most one `report` per thread per tick,
  then `drive order set <o> --lined` for each order it covered. An incident is the exception: until
  its fix is live he hears a line at least every 5 minutes, as [incidents.md](incidents.md) says.
- **He is away** when he says he is going to sleep or stepping out (`drive heard --away`), or when
  he has written nothing for an hour. Then only cards post: PR cards and question cards that wait
  on him, and an incident line [incidents.md](incidents.md) calls for. Summaries, due-time lines
  and job results wait.
- **His next message gets one catch-up, at his DM root**, right after its ack. `drive heard` tells
  the coordinator every order that changed while he was away. It sends one `report` for his DM root
  covering them, grouped by feature, with what waits on him last, then `--lined` each. The catch-up
  opens with the bold title `*Catch-up since <the time he was last heard>*`, and never goes in the
  thread of the message that brought him back, since a thread gets only lines about its own order.
  His words, Nov 10 6:43 PM, under a thread that got a catch-up's lines about other orders: "why
  did all of these get sent in this thread?", and 6:47 PM: "this si the catch up post? it should be
  tititled as such".
- **Anything that says it waits on him carries its card**, its permalink linked under words that
  name it as [The short form](#the-short-form) says, so he answers it there without clicking away.
- **His read-only question gets its answer within minutes** from the handler, which reads the
  source that owns the fact through whatever access opens it, a staff route included, and never
  answers that it could not read it; the communicator reads nothing itself.

## What goes in

- **What changed for him first.** "You can now tap Approve on any open PR card and it lands"
  beats "the rule that pulled your card when checks went red is merged and gone".
- **His names, not ours.** A thing goes in by what it is, in plain words, unless he has used its
  name himself. A pipeline step, a job, record, card or row id, a hash, a lane, a plan step, a file,
  table or function name and a code identifier are our names, so each is replaced, never quoted as
  it stands in a record or a log: "waiting on a manual roll press" becomes "waiting for the button a
  person must press before a customer cluster's changes go out". His words, Nov 10 1:03 PM, under a
  line that said the first: "I dunno what a roll press is". A product or tool he uses keeps its name
  in backticks. Only what the message needs goes in, and no mechanism he did not ask about.
- **It stands alone.** He reads every line with no other context, so assume he has none. His
  words, Nov 9 9:12 PM: "What is the sixteenth? You say it with no context you always have to
  assume I don't have any other context". A number, fraction, size or ratio says what it
  measures, why that value, and gives one concrete example. A mechanism gets one plain sentence
  before it is used. Nothing points at a list, finding, thread or PR he cannot see in that same
  message: no "the third finding", "the remaining two" or "as above". Not "reads each journal
  file's page index sized to the file (a sixteenth of it, at most 64 KiB)", but:

  ```
  To open a run's page the browser first fetches a small index from the end of every journal file.
  It used to fetch the last 64 KiB of every file; now it fetches the last one-sixteenth of each file
  (about 6 KiB of a 97 KiB file), never more than 64 KiB.
  ```
- **Everything he could open is linked where it is named**: a PR, a teammate's fix, a card, a
  release, a build, an alert, a thread or an earlier decision. His words, Nov 10 4:36 PM, under a
  card that said "A teammate's fix landed at 2:12 PM today" and linked no fix: "fix ur skill prompt
  bc this should ahve lnked josh’s PR< never mention somehting and expect me to go look it up
  myself". The link's words say what the thing is: a PR by what it does,
  `<url|the run page shows its history>`, never by its number alone, with what it serves: the order
  of his it carries out, in his words, or "port of #N" with the old PR linked. Each other PR the
  message names is linked the same way, never dropped to keep to one. A card's title and options
  draw no links, so whatever they name is linked in its summary or a Show me caption. `drive job
  ask` and this plugin's hook refuse an `ASK` or a post that names such a thing with no link there,
  and say which words.
- **Numbers over adjectives.** "129 of 476 passed, up from 100" beats "much better".
- **Answer his next question in the same message**: why, did he already decide this, is it a
  port, what comes after, why it is held.
- **Done means live and checked.** Say how you checked in a few words with the link. A line to
  him says what was observed, with its proof's link, and never lists what is not proven, under any
  label: "unproven", "not proven yet", "not seen working yet", "unverified", "untested". Each unknown
  gets a job, and he gets its result or nothing. A job's `UNPROVEN:` list is the coordinator's input,
  never copy for him. His words, Nov 9 4:52 PM: "fuck of with this not prioven bull shit jujst fucking fix the actual isue". An incident line also carries no side
  facts about a release, by [incidents.md](incidents.md).
- **State comes from live reads** (`drive status`, GitHub, a transcript), never from memory. What a
  release carries, such as its PRs, the steps it runs or which build fixes what, is read from that
  release's commit and build, never inferred. A tap is asked for only when owed. Before any card or
  line asks him to tap, approve or hold a PR, read `drive status <pr>`; when he tapped it already or
  it merged or closed, say what happened to it instead. His words, Nov 10 6:46 PM, under a card
  that asked him to tap a fix he had approved hours before: "its alrady approved".
  `drive job ask` and this plugin's hooks refuse such a card, record or post, naming the PR and when
  he tapped it.

## What stays out

- **The machinery.** Pins, packs, installs-as-process, who handed what to whom, which hook
  refused what. When he asks why, give the cause in one or two sentences, then the fix and
  when it is live. A post he ordered that a hook refused gets one line: it goes out once the fix
  is installed.
- **A second ask for a post he ordered.** His order to post, in his DM, in a terminal or by a tap
  on a card, is his permission for one post in the place he named. No card asks him to approve it,
  no line says a hook needs his Yes, and a card already up asking that is closed the moment it is
  seen. A post nobody ordered, such as a reply offered to someone who reported a bug or a line for a
  teammate, still needs his word once, on a question card asking whether to tell them.
- **Asks of a reporter.** A bug thread gets its one fix-live note and nothing that asks them to act.
- **Messages with no new fact.** Chatter gets a `+1` reaction instead of an ack. Never "nothing new".
- **Second copies.** One post per thing; a correction edits it in place; never text and a
  card for the same thing.
- **Promises without a timer.** A time a message promises is one the coordinator set with
  `drive order set <o> --due` in the same turn, and its `report` names it; an ack promises no
  time of its own. When it passes with no drive post in the order's thread, the watch says `DUE`
  every 10 minutes until one is there and the order cannot close before, so the coordinator
  reports what is late and why before he notices.
- **The current time, and any time you estimated.** Slack already stamps every post. A time
  you promise is computed from `date` (or written as `+30m` to `--due` and read back from
  `drive status`), never from your sense of how long the turn has run, which drifts by an hour.

## Tonight's posts, rewritten

He called the first two "terrible" and the last "insane shit". The third is the one he answered
"Excellent" in 61 seconds.

> *Tooling overhaul, 1:20 AM* Since 1:05: the rule that pulled your card when checks went
> red is merged and gone; your tap on any open pull request now adopts it into the drive
> instead of being refused, merged too. The card pictures and the Fix, New and Cleanup titles
> you approved are merged but not yet on your Mac: the Mac runs a build from before them…

```
*Your PR cards keep their buttons while CI is red*
Tapping Approve on any open PR now lands it once CI passes. Pictures on cards go live by 1:30 AM.
```

> *10:50 PM: one build merged, one half merged, three open or unpushed* Read from the repos
> directly, since the fixer lane has not reported since 10:12 PM: | Build | State | …

```
*1 of 5 tooling fixes is merged; none is on your Mac yet*
The other four are in review and land by 11:10 PM; each goes live within minutes of merging.
```

> *Build, install and clean up first; the one-pager after*
> Order changed as you said: your Opus subagent builds the overhaul, merges and installs it,
> rewrites the recent posts in this DM and removes their duplicates and closed-PR cards, and
> only then sends you the one-pager with the design questions about forward tweaks. …

The last was a builder's findings passed straight through: 1,110 words in one post above two
question cards, which he answered at 9:33 PM on Nov 9.

> *Two picks for you below, one on how a run's files are laid out and one on what the runner keeps
> so the page never reads every call. The third part, the Sand functions that read a run, needs no
> pick.* *Step logs.* Both the new and the old runner still write the steps log, but nothing on the
> new side reads it. … *How a run's files are laid out.* A run's journal is a SQLite database that a
> replication tool (our own copy of Litestream) uploads to the team's bucket as many small files…

Rewritten, each question is its card and nothing else, about 120 words with its options, and its
Show me drawn whole above them:

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

The hook passes the exact words of every post through `cc-slack check-post` and counts them as they
go out. A post it flags, one over its limit, or a handler's post naming something he could open
with no link there comes back refused with what to fix, and a handler's line in his thread comes
back until that handler has read the thread. Then the handler
reads it as he will, on a phone, with no context: does he know what changed, whether he
must act, and when he hears next, does every number, term and reference in it make sense with
nothing else open, and does each thing it names that he could open carry its link right there?
