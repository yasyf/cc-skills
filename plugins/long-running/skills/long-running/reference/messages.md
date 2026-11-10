# Messages to the owner

Only the communicator's side writes to him, in the shapes below and on the schedule below, from his
own messages and the `report`, `ready` and `ask` records the coordinator sends. The communicator
posts the ack alone in its first message and nothing else; its handlers write, check and post every
other message, each for one message of his or one record. The coordinator's record carries the facts
and the handler writes the words, and a handler answers his read-only questions itself. Each message
is short, says what changed for him, and carries the card with its buttons whenever it says
something waits on him.

## The shapes

**An ack**, in the thread of his message, posted alone in the communicator's first message,
inside 5 to 10 seconds; cc-slack put the eyes on his message the moment it landed. It is at most
one sentence the communicator already knows, restating what he asked and what happens next, and is
never read for, drafted or checked. It names no time:
the coordinator sets the order's due time, and the first summary says it.

**A summary**, in a thread, which a handler writes from a `POST` report and posts with
`slack_dm_status(text=…, thread_ts=<his thread>)`: what changed for him since the last one, grouped
by feature, one bold line per feature and at most two sentences under it.

```
*<feature>: <what is true now, for him, in under 12 words>*
<one or two sentences: what it means and what happens next, by when>
```

**A question card**, which a handler writes from an `ask` record, only after the findings it
rests on are in his thread: what was hit, what was measured, the cause, and what each option
changes and costs, from the record's `Findings:` section. Then the card at his DM root with
`slack_dm_status(card=…)` and no thread, then `cc-slack card-copy --url <its permalink> --thread
<his thread>`, so the card and its buttons sit in his thread under the findings. A record with no
findings draws no card. Title: one question in one
sentence. Body: at most three sentences saying why it matters and what the drive is doing meanwhile.
Two to four options, each saying in full words what picking it does. No option asks a reporter or
anyone else to retry, confirm or check: the drive proves a fix itself. When something waits on his
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
`slack_dm_status(tap_prs=[<each url, bottom first>], text=<line>, thread_ts=<his thread>)` makes
cc-slack draw one card at his DM root for the stack those PRs form (`drive card`), with Approve and
Hold while it waits on him. His Approve lands exactly those PRs, bottom first, and a PR added later
gets its own card. The line in his thread is followed by `cc-slack card-copy` of the card, so that
thread carries its buttons. Each PR on it gets its own section, opened by its title, link and Open
button, then its Refresher, its biggest surprise, what he will hate most, where it stands and what
it waits on only when that is not green and his tap, how it differs from the plan when it does, and
its Show me with its pictures inline. A stack card's title and opening summary come from `## The
stack` in its bottom PR. Once he answers, the card folds to its title, his pick, and one line per
PR. Its words come from each PR's title and body, with record ids dropped, so a bad card is fixed in
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
first, then each feature by state. Nothing to post; a line that says where things stand links it.

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
- **His next message gets one catch-up**, right after its ack: `drive heard` tells the coordinator
  every order that changed while he was away, and it sends one `report` for his thread covering
  them, grouped by feature, with what waits on him last, then `--lined` each.
- **Anything that says it waits on him carries its card.** A summary, a catch-up, a TICK line or a
  link line links each open card it names by permalink, and cc-slack draws that card with its
  buttons right under the line, in the same thread, so he answers it there without clicking away;
  a tap on either copy answers both. A link is never the embed: Slack shows a link to a DM card with
  no buttons. cc-slack refuses a line that says something waits on him with no open card linked,
  and `check-post` flags it before it is sent.
- **His read-only question gets its answer within minutes** from the handler, which reads the
  source that owns the fact; the communicator reads nothing itself.

## What goes in

- **What changed for him first.** "You can now tap Approve on any open PR card and it lands"
  beats "the rule that pulled your card when checks went red is merged and gone".
- **Plain words.** No record ids, row numbers, card ids, hashes, test ids, lane or job names,
  plan step numbers, file paths, or code identifiers. Product and tool names in backticks are
  fine. Gloss anything he has not used lately in a few plain words.
- **Name every PR by its title, linked**, once: `<url|the run page shows its history>`. Link
  every build, alert, time, and earlier decision you mention.
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
  release's commit and build, never inferred.

## What stays out

- **The machinery.** Pins, packs, installs-as-process, who handed what to whom, which hook
  refused what. When he asks why, give the cause in one or two sentences, then the fix and
  when it is live.
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

He called the first two "terrible". The third is the one he answered "Excellent" in 61 seconds.

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

## Before it goes out

A handler passes every message it posts through `cc-slack check-post -` and fixes every line it
prints; the ack alone skips it, being one sentence the communicator already knows. Then the
handler reads it as he will, on a phone, with no context: does he know what changed, whether he
must act, and when he hears next?
