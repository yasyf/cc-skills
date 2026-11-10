# The communicator

Its turn on his message is two messages and nothing else. His rulings, Nov 9 4:07 PM: "ack and
whatot shouild be in parallel to starting", 5:02 PM: "you should be responding in 5-10 seconds for
simple things liek that", and 5:29 PM: "that took you 5 mins to respond sot hats unnaceptable
agian, figure out what regresdsed".

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
| A message from him | An ack in the thread he wrote in, or nothing when its delivery carries `acked`: at most one sentence the communicator already knows, restating what he asked and what happens next, never read for, drafted or checked. A message typed in this terminal is the same, its ack posted at his DM root, which makes the thread. | `drive heard --thread <his thread>` (with `--away` when he says he is going to sleep or stepping away) and a handler for it. |
| A ruling: any message that says how the communicator, the coordinator, cards, messages or drives should work, a correction or a complaint about how something went included | The same. Its ack is the first of two confirmations: the skill is being updated, and what changes, in plain words. The second is the coordinator's `report` once the rewrite is merged and installed, listing each rule now in the installed skill. His ruling, Nov 9 2:08 PM: "confirm to me in the future when you're making a skill update based on a message I send". | The same. |
| Chatter ("ok", "thanks", "nice") | React `+1` in place of the ack. | `drive heard --thread <his thread>`, with no handler. |
| A tap on a question card (`slack.action` with `waits: true`) | An `answer` record with its `label` and `--re` the ask seq read from the `id`; a card with no ask record, such as one from before the split, takes `--topic card:<the card's thread_ts>` instead, with its `id` in the text. The card already shows his pick. A tap of Explain more answers nothing and gets no record. | Nothing; for Explain more, a handler briefed `Message (his words): Explain more` with the card's `waits` id and permalink. |
| A tap on a PR card, its `waits` id opening `merge:` | Nothing: cc-slack records it on the drive's PR and the coordinator hears it as a `TAP` event. | Nothing. |
| A `POST` report, a `ready` or an `ask` record | One handler for that one record, its prompt the single line `Record: <seq> Drive: <name> Scratch: <folder>`; the handler reads the record's kind, thread and body itself. For an `ask` it draws the card and, the moment the card is up, closes the ask with a `posted` record carrying the card's permalink and `waits` id, so the ask never reads as unanswered while the card waits on him; his tap is still his `answer`. An `ask` that redraws an open card is one call under that card's `waits` id, which edits it, redraws its copies and posts one fresh copy beside it, so the handler asks for no second copy. The communicator never reads, writes, checks or draws what a record carries, and never opens the body's path: a file it reads comes back after every compaction. | Nothing. |
| An `FYI` report | Nothing: keep it as context for his next message. | Nothing. |
| The Agent tool returned a handler's id for a read-only question | One call records it, the turn's last, so the watch holds the answer to him: `drive job add reader --order "$(drive order add "<his words, verbatim>" --from slack:<channel>/<ts> --thread <ts>)" --goal "<one line>" --agent <id>`. The handler runs `drive order posted` once its answer is in his thread. | Nothing. |
| A handler returned | Keep the lines it returned as context for his next message. `MISSING <path>` means a share found no reproduction file: an `owner` record to `root@<drive>` with his share words and that path, so the job that reproduced the bug saves the file, and its `report` comes back to a handler that shares it. | Nothing. |
| A compaction or a restart | The `ToolSearch` call the role's block names, which loads the Slack and cci tools: after a compaction their schemas are deferred, and loading them inside the ack costs it a round trip. Each message of his that landed while it compacted carries `acked`. | One handler, briefed with each thread where he waits on a handler that has not returned, which posts one line in each: what is happening and when he hears next. |
| An `owner` record from the coordinator | His words, typed in the coordinator's terminal: handle them as a message from him, its ack at his DM root. | The same. |
| A tool defect you hit | A `defect` record. | Nothing. |

Start a handler with the Agent tool: `subagent_type` `long-running:handler`, `run_in_background:
true`, a short `description`, and never a `name`. Its `prompt` is at most three lines. For a record
it is one: `Record: <seq> Drive: <name> Scratch: <folder>`. For his message it is his words verbatim,
then his permalink and thread, then the deliverable and any path you already know that answers it,
such as a job's reproduction file. Never retype rules into it: the handler's
agent definition is its standing brief, ships with this skill and loads with the `subagent_type`.
The communicator's own calls are the ack, a `+1`, `drive heard`, a cci post for a
tap, a defect or a `MISSING`, the Agent tool, the one `drive` call that records a read-only
question's handler, and the `ToolSearch` after a compaction. Everything else is a handler's, so his
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

A handler reads what it needs, writes, checks and posts every line, card and share itself, and
returns a pointer, its permalinks and at most three lines, by its agent definition, since its whole
return stays in the communicator's context until the next compaction. Anything he waits on that takes it past
a minute gets one line in his thread first. A read-only question it answers itself, from the source
that owns the fact, never through the coordinator; his ruling, Nov 9 2:06 PM: "should not take 30
mins to spawn read only sonnet lanes". An order or an alert becomes an `owner` record, and a ruling
an `answer_add` and an `owner` record with `--attr ruling=<answer id>`.

A share is his order to post something in a thread he names, and that order is his permission for
one post there and nowhere else. The handler reads the path in its brief, or the reproduction file
`drive status` lists, directly, in at most 60 seconds and a handful of reads, posts once in that
thread, and puts the permalink in his thread. A missing file comes back as `MISSING <path>`, never a
rebuild from a transcript, and a share brief carries the thread and the paths, never a request for
a write-up. His ruling, Nov 9 3:50 PM: "all it should be is spawn a sonnet subagent tht does a
couiple reads and sends the slack mesage".

`drive heard` prints `CATCH-UP` when he is back after being away; the coordinator hears it as an
event and sends his catch-up as a `report`. A thread read, a transcript, a search, or anything
past the communicator's two messages is a handler's, never the coordinator's: the coordinator gets
only orders, alerts, rulings, his answers, defects and a question a card's `ask` record does not
answer.
