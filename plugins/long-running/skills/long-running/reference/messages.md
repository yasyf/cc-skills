# Messages to the owner

Only the driver writes to him, in the shapes below and on the schedule below. Each message is
short, says what changed for him, and carries its own buttons when it waits on him.

## The shapes

**An ack**, in the thread of his message, the second call after `slack_pickup`, before any other
work: one line restating what he asked and what happens next, by when.

**A summary**, in a thread, posted with `slack_dm_status(text=…, thread_ts=<his thread>)`: what
changed for him since the last one, grouped by feature, one bold line per feature and at most two
sentences under it.

```
*<feature>: <what is true now, for him, in under 12 words>*
<one or two sentences: what it means and what happens next, by when>
```

**A question card**, at his DM root with `slack_dm_status(card=…)` and no thread, then one
line in his thread linking it. Title: one question in one sentence. Body: at most three
sentences saying why it matters and what you are doing meanwhile. Two to four options, each
saying what picking it does. When something waits on his answer, set `waits` to
`<drive>:<short slug>` and `recommended`; when work continues on your pick, leave both out
and say in the body which option you are building. Set `drive` to the drive's name on every
card: cc-slack reads a `waits` card that names its drive as the drive's question, whose home is
Slack, and posts it with no cc-present board warning.

**A PR card**, which you never write: one card per stack. `slack_dm_status(tap_prs=[<each url,
bottom first>], text=<line>, thread_ts=<his thread>)` makes cc-slack draw one card at his DM root
for the stack those PRs form (`drive card`), with Approve and Hold while it waits on him; his
Approve lands exactly those PRs, bottom first, and a PR added later gets its own card. Each PR on
it shows its ELI5 first, then what changes for him, what he will hate most, where it stands, how it
differs from the plan when it does, and its Show me picture. Its words come from each PR's title
and body, with record ids dropped, so a bad card is fixed in the PR body, never in the message.

The status card at his DM root is drawn, not written: after an order changes state, post
`drive rollup`'s card with `slack_dm_status(card=…, root_only=true)`.

## When it goes out

- **His message gets its ack at once**, whatever else is running. The work it starts reports back
  in that thread when it is done.
- **Everything else waits for a summary.** No line per event: a job result, a merge, a CI turn or
  a promised time feeds the next summary of its thread. The watch's `TICK` comes every 30 minutes
  and names the orders with news; post at most one summary per thread per tick, then
  `drive order set <o> --lined` for each order it covered.
- **He is away** when he says he is going to sleep or stepping out (`drive heard --away`), or when
  he has written nothing for an hour. Then only cards post: PR cards and question cards that wait
  on him, and an incident line [incidents.md](incidents.md) calls for. Summaries, due-time lines
  and job results wait.
- **His next message gets one catch-up**, right after its ack: `drive heard` prints `CATCH-UP`
  with every order that changed while he was away. Post one summary in his main thread covering
  them, grouped by feature, with what waits on him last, then `--lined` each.

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
- **Done means live and checked.** Say how you checked in a few words with the link. Claim
  only what you saw; mark the rest `Unproven:`.
- **State comes from live reads** (`drive status`, GitHub, a transcript), never from memory.

## What stays out

- **The machinery.** Pins, packs, installs-as-process, who handed what to whom, which hook
  refused what. When he asks why, give the cause in one or two sentences, then the fix and
  when it is live.
- **Messages with no new fact.** Chatter gets a `+1` reaction instead of an ack. Never "nothing new".
- **Second copies.** One post per thing; a correction edits it in place; never text and a
  card for the same thing.
- **Promises without a timer.** A time you write is set with `drive order set <o> --due` in
  the same turn. When it passes unmet, the watch says so and you post what is late and why
  before he notices.
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

Write the text to a file and run `cc-slack check-post <file>`; fix every line it prints.
Then read it as he will, on a phone, with no context: does he know what changed, whether he
must act, and when he hears next?
