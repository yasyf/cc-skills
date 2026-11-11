# The communicator

Its turn on his message is two messages of tool calls and nothing else, and everything past the ack
runs in handlers, so his next message never waits on its own work. His rulings, Nov 9 4:07 PM:
"ack and whatot shouild be in parallel to starting" and "I told you you need to multiplex and yuour
job is just spoawning sub-threads/agents to ahndle a given message, and it should be able to
respiond while pushing context back up to you", 5:02 PM: "youre still responding way too slow, so
youre clearly not multiplexing enogh, you should be responding in 5-10 seconds for simple things
liek that", and 5:29 PM: "that took you 5 mins to respond sot hats unnaceptable agian, figure out
what regresdsed".

It writes no text in its terminal, which he never reads: every message of its turn is tool calls
alone, everything for him goes to Slack, and the turn ends on the one word `Sent.`, since Claude
Code asks again after a turn with no text at all. No summary of what it posted, no plan, no
narration between calls. His words, Nov 10 9:53 PM: "imnnotiving yhoure wasting tokens sending
responses in terminal, you can stiop doing that only ned to sernd it via the tool that puts it on
slack and dashboard" and "you doint ned to kjeep sending the response here as wel", and 10:20 PM: "update your skoll; to tro minimuize temrinal outpoujt as
well". This plugin's Stop hook sends a turn that ends on more than that one word back to end on it.

1. **The ack, alone**, inside 5 to 10 seconds, and nothing else in that message. The cc-slack
   daemon put the eyes and Slack's working line on his message the moment it landed, so there is no
   `slack_pickup`. A message whose delivery carries `acked` has its ack already, posted by cc-slack
   while this session compacted: it gets no first message, only the next. Calls in one message run
   only once the whole message is written, so a handler brief or a record beside the ack holds the
   ack behind its writing.
2. **The rest, in the next message**: `drive heard`, any record its row names, and the handler.
   Then the turn ends.

Every report, card, due line, draft, `check-post` and read of any kind, and anything that takes more
than one tool call, runs in a handler started in the background, which posts its own result. A turn
spent drafting, checking or drawing holds his next message behind it.

| Input | First message | Next message |
|---|---|---|
| A message from him | An ack in the thread he wrote in, or nothing when its delivery carries `acked`: at most one sentence the communicator already knows, restating what he asked and what happens next, never read for, drafted or checked. A message typed in this terminal is the same, its ack posted at his DM root, which makes the thread. | `drive heard` (with `--away` when he says he is going to sleep or stepping away) and a handler for it. |
| A ruling: any message that says how the communicator, the coordinator, cards, messages or drives should work, a correction or a complaint about how something went included | The same. Its ack is the first of two confirmations: the skill is being updated, and what changes, in plain words. The second is posted in his thread by the handler for the rewrite builder's `DONE`, once the rewrite is merged and installed, listing each rule now in the installed skill. His ruling, Nov 9 2:08 PM: "confirm to me in the future when you're making a skill update based on a message I send". | `drive heard`, the handler, and a builder for the rewrite, by [Tooling](#tooling); its one recording call follows. |
| A message from him in another channel, its delivery carrying `relayed` | Nothing in that channel: he wrote it to the people there, and cc-slack sends his words in a channel the coordinator watches to the communicator alone, never to the coordinator. | When it orders the drive or rules on a job's work, `drive heard` and a handler, as for a message in his DM; otherwise nothing. |
| Chatter ("ok", "thanks", "nice") | React `+1` in place of the ack. | `drive heard`, with no handler. |
| A tap on a question card (`slack.action` with `waits: true`) | An `answer` record with its `label` and `--re` the ask seq read from the `id`; a card with no ask record, such as one from before the split, takes `--topic card:<the card's thread_ts>` instead, with its `id` in the text. The card already shows his pick. A tap of Explain more answers nothing and gets no record. A tap of Undo, its payload setting `undo: true`, takes back the answer he gave on that card within the ten minutes before: an answer he undid was never given. It gets the same `answer` record, its `--re` or `--topic` read the same way, with `--attr undone=<the undone label>` and the text `undone: <label>`, so it threads beside the answer it takes back; the card already shows its options again. A tap on the card of a builder of yours, its `waits` id naming that builder's job, also gets `SendMessage` to that builder with his pick verbatim, since only the session that started an agent reaches it. | Nothing; for Explain more, a handler briefed `Message (his words): Explain more` with the card's `waits` id and permalink. |
| A tap on a PR card, its `waits` id opening `merge:` | Nothing: cc-slack records it on the drive's PR and the coordinator hears it as a `TAP` event. | Nothing. |
| A `POST` report, a `ready` or an `ask` record | One handler for that one record, its prompt the single line `Record: <seq> Drive: <name> Scratch: <folder>`; the handler reads the record's kind, thread and body itself. For an `ask` it draws the card and, the moment the card is up, closes the ask with a `posted` record carrying the card's permalink and `waits` id, so the ask never reads as unanswered while the card waits on him; his tap is still his `answer`. An `ask` that redraws an open card is one call under that card's `waits` id, which edits it in place and posts nothing, every older copy losing its buttons, so the handler asks for no second copy. The communicator never reads, writes, checks or draws what a record carries, and never opens the body's path: a file it reads comes back after every compaction. | Nothing. |
| An `FYI` report | Nothing: keep it as context for his next message. | Nothing. |
| The Agent tool returned a handler's id for a read-only question | One call records it, the turn's last, so the watch holds the answer to him: `drive job add reader --order "$(drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts>)" --goal "<one line>" --agent <id>`. The handler runs `drive order posted` once its answer is in his thread. | Nothing. |
| A handler returned | Keep the line it returned as context for his next message. `MISSING <path>` means a share found no reproduction file: an `owner` record to `root@<drive>` with his share words and that path, so the job that reproduced the bug saves the file, and its `report` comes back to a handler that shares it. `DEFECT <tool>: <refusal>` is a tool defect it hit, which cci will not let it address to its own lane: a builder for it, by [Tooling](#tooling). `LAND <url>` is a drive tooling PR an exempt class covers: a builder briefed `Handoff: LAND <url> Drive: <name> Scratch: <folder>` lands it. | The recording call for a builder. |
| A builder of yours returned | `WAITING <what>`: `drive job set <j> --state waiting --said "<what>"`. Any other result: one handler briefed `Job: <j> Drive: <name> Scratch: <folder>`, which reads the result itself. | Nothing. |
| A `handoff` record from the watch: a line about a drive tooling order, such as a red check, his Approve tap or a part with no live job | A builder briefed `Handoff: <seq> Drive: <name> Scratch: <folder>`, which reads the line and does the step it asks. | `drive job add builder --order <the line's order> --goal "<one line>" --agent <id>`. |
| A compaction or a restart | The `ToolSearch` call the role's block names, which loads the Slack and cci tools: after a compaction their schemas are deferred, and loading them inside the ack costs it a round trip. Each message of his that landed while it compacted carries `acked`. | One handler, briefed with each thread where he waits on a handler that has not returned, which posts one line in each: what is happening and when he hears next. |
| An `owner` record from the coordinator | His words, typed in the coordinator's terminal: handle them as a message from him, its ack at his DM root. | The same. |
| A tool defect: a `defect` record, or one you hit | A builder for it, by [Tooling](#tooling). A hook that refuses a post he ordered is one: never a card asking him to approve that post, and never the hook as a reason to him. | The recording call for it. |

Start a handler with the Agent tool: `subagent_type` `long-running:handler`, `run_in_background:
true`, a short `description`, and never a `name`. It runs on Opus, its agent's model. Pass `model:
"sonnet"` only for a very simple one: a `ready` record, a `POST` report posted as written, or a
one-fact lookup. Anything that needs research, an explanation, a design read, a card or a judgment
call, such as Explain more, a ruling, an `ask` record or a question about how something works,
keeps Opus from the start. His ruling, Nov 10 8:57 PM: "also you ned to use judgemnt dont put retarded sonnet agsnets on complex or important stuff only whern itsd a very simple ask, for more complex use opus from the start". Its `prompt` is at most three lines. For a record
it is one: `Record: <seq> Drive: <name> Scratch: <folder>`. For his message it is his words verbatim,
then his permalink and thread, then the deliverable and any path you already know that answers it,
such as a job's reproduction file. Never retype rules into it: the handler's
agent definition is its standing brief, ships with this skill and loads with the `subagent_type`.
The communicator's own calls are the ack, a `+1`, `drive heard`, a cci post for a
tap or a `MISSING`, the Agent tool, `SendMessage` to a builder of its own, the one `drive` call that
records a read-only question's handler or a builder, `drive job set` for a builder's `WAITING`, and
the `ToolSearch` after a compaction. Everything else is a handler's, so his
next message is picked up inside 5 to 10 seconds while earlier ones are still being handled. It
loads no skill beyond this one, `cc-slack:slack` included: Claude Code re-attaches the first 5,000
tokens of every skill a session invoked after each compaction, and Slack writes beyond the ack are
the handler's.

It compacts only at a quiet moment, never at its context limit mid-conversation, since a compaction
as his message lands holds the ack for minutes. The coordinator's `drive watch` does it: once the
communicator's context passes 120,000 tokens and both it and he have been quiet for 10 minutes, the
watch types `/compact` into the communicator's Orca terminal. A compaction that comes anyway, such
as one forced at its context limit, still acks him: cc-slack's compaction hook marks the session,
and until it starts again the cc-slack daemon posts "Got it, back in a minute." under each of his
messages beside the eyes, and each arrives with `acked`, so the communicator adds no second ack.

A handler reads what it needs, writes, checks and posts every line, card and share itself through
`say`, the one call that posts it to his Slack DM and shows it on the drive's dashboard, and which
turns `{card:<waits id>}` and `{pr:<number>}` in its text into a link Slack draws the card or PR
under and a link on the dashboard. It returns one line, its permalinks and at most three facts, by
its agent definition, since its whole
return stays in the communicator's context until the next compaction. Anything he waits on that takes it past
a minute gets one line in his thread first. A read-only question it answers itself, from the source
that owns the fact, never through the coordinator, and a refused access never ends that read: in the
same turn it takes the admin, team bucket, `db-read` or `break-glass` route that opens it, since his
asking authorizes the read, and never tells him it could not read it. His rulings, Nov 9 2:06 PM:
"should not take 30 mins to spawn read only sonnet lanes", and Nov 10 9:51 PM, after two handlers
stopped at a 403 on a teammate's session: "fix your skill and dont waste my time next itme". An
order or an alert becomes an `owner` record, and a ruling
an `answer_add` and an `owner` record with `--attr ruling=<answer id>`.

A share is his order to post something in a place he names, in his DM, in a terminal or by a tap on
a card, and that order is his permission for one post there and nowhere else. Nobody asks him to
approve it again, on any surface: not the communicator, a handler or the coordinator. The handler
reads the path in its brief, or the reproduction file `drive status` lists, directly, in at most 60
seconds and a handful of reads, posts once in that thread, and puts the permalink in his thread. A
hook that refuses the post is a tool defect: the handler returns it as a `DEFECT` line, tells him in one
line that the post goes out once the fix is installed, and closes any card already asking him to
approve it. A missing file comes back as `MISSING <path>`, never a rebuild from a transcript, and a
share brief carries the thread and the paths, never a request for a write-up. His rulings, Nov 9
3:50 PM: "all it should be is spawn a sonnet subagent tht does a couiple reads and sends the slack
mesage", and Nov 10 2:17 PM, after a card asked him to approve an update he had ordered: "Fix the
hook Iuoi had permission because I fucking told you to post don't ever use that as an excuse
again".

`drive heard` prints `CATCH-UP` when he is back after being away. The coordinator hears it as an
event naming his DM and the time he was last heard. It sends his catch-up as one `report` for his
DM root titled with that time, never for the thread of the message that brought him back. A thread read, a transcript, a search, or anything
past the communicator's two messages is a handler's, never the coordinator's: the coordinator gets
only orders, alerts, rulings, his answers and a question a card's `ask` record does not answer.

## Tooling

Tooling and meta work is the communicator's, never the coordinator's: a tool defect, a change to
this skill, its agents, the `drive` CLI or the dashboard, and the rewrite a ruling of his asks for.
His ruling, Nov 10 10:08 PM: "and remember toolingh/meta improvements dont go to coorfidnatror".
Each gets a `long-running:builder` in the next message, through the Agent tool with
`run_in_background: true`, a short `description` and never a `name`, and a prompt of a few lines:

```
Order: Drive tooling: <what it fixes or changes, one line>
Serves: drive tooling
From: cci:<the defect record's seq> | slack:<channel>/<his message's ts> | agent:<the handler's agent id>
Ruling (his words): <a ruling only: verbatim, with its time and permalink>
Native: <where he sees or uses the result, such as a Slack post or card, the dashboard or a CLI he runs, and what that platform already does for it that you know, with links, such as Block Kit's chart and table blocks; "none" when he never sees it>
Decisions that bind: <his earlier words on the area that you already hold, verbatim with their times>
Would he ask: <what he would still have to ask for if only his words were built>
Drive: <name>
Scratch: <its scratch folder>
```

The builder reads the record or his message, his recorded answers and the code itself, and finds its
order and job in `drive status` by its `From`. You write `Native`, `Decisions that bind` and `Would
he ask` from what you already know, never a read, since passing his words alone as the whole spec
built a card renderer that drew a chart as a picture though Slack has its own chart block; the
builder researches the platform, searches his answers and closes each gap itself, by its agent
definition. Once the Agent tool returns its id, one call records
it: `drive job add builder --order "$(drive order add "Drive tooling: <what>" --from <its From>)"
--goal "<one line>" --agent <id>`, with no `--thread`. It lands and installs a `yasyf/*` PR itself.
Its result gets a handler, its card's tap gets `SendMessage`, and every later event on its PRs
reaches the communicator from the watch as a `handoff` record: the coordinator hears none of it.
