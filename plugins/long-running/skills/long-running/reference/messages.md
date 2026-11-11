# Messages to the owner

Only the communicator's side writes to him, in the shapes and on the schedule below, from his messages and the coordinator's `report`, `ready` and `ask` records. The communicator posts the ack alone in its first message and nothing else; its handlers write, check and post every other message, each for one message of his or one record, and answer his read-only questions themselves. Each message is short, says what changed for him, and carries the card with its buttons whenever something waits on him.

The tooling holds every message to a word limit counted as the post goes out, with each link counted as the words of its label, so a report record names no cap of its own:

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

Text passed beside a card is drawn atop its body and counts toward its limit. To send an unlimited answer to a question, order, installed ruling confirmation, or Explain more tap of his, pass as `asked` each message of his the post answers, word for word (using `…` for skipped words); the hook matches straight and curly quotes alike across the thread or the handler's brief. The message-limits hook refuses an over-limit post or edit in his DM and `drive job ask` refuses an out-of-shape `ASK` block.

## The short form

- Lead with what changed for him in his words, never with how the machinery got there. Use a bold line and at most two sentences, and a table only for three or more parallel items.
- Keep every card and line in a feature's thread about that feature; put drive tooling at his DM root under its own words instead of in the thread of a feature it unblocks.
- Send one `report` record per thread per coordinator turn, opening `POST` or `FYI`, written at the 60-word or 120-word post limit so the handler posts it as written. Put what changed for him in the first sentence, keep items that wait on him (with each card's permalink or `waits` id) before cutting what does not fit, and leave the rest for the next turn rather than sending a second record.
- Whenever a line names a card or says something waits on him, link the card's own permalink under descriptive words, `<permalink|the lock card>`, within the message's first five links so Slack unfurls the card with its buttons in place. Never paste a bare URL, `<permalink|permalink>`, or a text copy of the card; cc-slack and `cc-slack check-post` refuse a waiting line without a linked open card or with a raw card URL.
- Keep one copy of everything; edit a message in place to correct it.
- Set any promised time in the same coordinator turn with `drive order add --due` (counted from the ack) or `drive order set --due` (counted from the next line in the order's thread) so the watch repeats `DUE` every 10 minutes until a post lands there. The handler records each post in that thread with `drive order posted <o> <link>`, adding `--part` only when the post delivers a part; `drive order set` and `drive order posted` refuse unrecorded or misrouted posts.

## The shapes

**An ack**, in the thread of his message, posted alone in the communicator's first message inside 5 to 10 seconds (cc-slack adds eyes on arrival). It is at most one sentence the communicator already knows, restating what he asked and what happens next, without reading, drafting, or promising a time.

**A summary**, in a thread, written by a handler from a `POST` report and posted with `say(text=…, thread_ts=<his thread>)`: what changed for him since the last summary, grouped by feature, one bold line and at most two sentences per feature (120 words total, or 60 words for a single feature).

```
*<feature>: <what is true now, for him, in under 12 words>*
<one or two sentences: what it means and what happens next, by when>
```

**A question card**, drawn by a handler from an `ask` record with nothing posted before or after it. Title: the one-sentence question. Body: one to three summary lines giving the investigation results and what is being decided, the recommendation, and what happens meanwhile; then, when the block has one, `*Show me*` and its visuals.

Include a `Show me:` only when a visual shows flow, numbers, side-by-side options, or real API calls that prose cannot, using real measured or code-derived values in every cell. Split mixed visuals into separate diagram fences and markdown tables, and place a caption line of at most 25 words just above each fence so `drive job card` turns the fence into `![caption](/abs.png)` rendered by `lib/drive/site/visuals.js` (with code highlighted at 12.5 px, including Sand):

| The question turns on | Kind | Fence holds |
|---|---|---|
| the shape of an API, call, query or schema the options change | code | the real calls, in a fence naming its language: ```sand, ```sql, ```go |
| components, data layout or flow between them | arch | an arch/v1 JSON document, the diagram the dashboard and cc-present draw |
| routing, ownership, steps over time, a state machine, a small graph | mermaid | Mermaid source |
| numbers: sizes, counts, timings, before and after | chart | a Vega-Lite JSON spec with its data inline, drawn in the dataviz palette |
| a tradeoff between options | compare | `{"options": [{"name", "recommended", "language", "code", "points": ["+ a gain", "- a cost"]}]}`, drawn side by side |
| words compared across rows | table | a markdown table, which cc-slack draws as a native Slack table |
| a UI or anything only a picture shows | image | `![what it shows](/absolute/path.png)` on a line of its own, a screenshot or a gen-image or codex imagegen picture |

`drive job ask` and `drive job card` refuse an empty `Show me:`, a constant-only table, an untyped or `text` fence, raw data or logs (`JSON`, `YAML`, terminal output, stack traces), unparseable JSON specs, or an inline image.

Write two to four self-contained options of at most 25 words each. A question card's options come from the job's own evidence: the fix its diagnosis points at is always one of them, and it is the recommended one unless the summary says why not. `drive job ask` refuses a block whose findings carry no `Direct fix:` line naming that option by its words, or `Direct fix: none: <why>` when no diagnosis points at one. Because cc-slack draws the recommended option first and renumbers the rest, state what each option does in full rather than referencing another option by number or position; cc-slack refuses cross-option references. Findings stay in the job's file and reach him only when he taps Explain more.

Post the card with `say(card=<what drive job card prints>, thread_ts=<his thread>)` using the output of `drive job card <the record's ask file> --waits <its waits id>` (omit `thread_ts` for a drive tooling card), then close the `ask` record with a `posted` record carrying the card's permalink and `waits` id. Set `drive` to the drive's name on every card; set `waits` to `<drive>:<job>-<the ask record's seq>` and `recommended` when waiting on him, or omit both and state which option is being built when work continues. To update an unanswered card, redraw it under the same `waits` id instead of calling `slack_edit` (which cc-slack refuses); when a redo starts, send an `ask` record with `--attr ask=<the card's kept file>`, `--attr waits=<its waits id>` and `--attr reworking=<what is being redone, and when it is back>` so the card shows `Being reworked` while keeping its buttons, then redraw under that `waits` id when done. When an answer elsewhere supersedes a question or PR card, settle it in place with `drive answer`.

**A PR card**, drawn by a handler from a `ready` record with `say(tap_prs=[<each url, bottom first>], text=<line>, thread_ts=<his thread>)` (`drive card`), quoting the order or "Drive tooling: …" above it. Only PRs whose order asks for a tap or non-exempt `Forge-AI/*` drive tooling fixes by [Scope](scope.md) get a card; ports and other no-tap classes just report `"port of #N landed"` in the order's thread when they land. Fix any bad card text in the PR's title or body (or `## The stack` on the bottom PR), where `drive card` reads it. The PR card check, run by `drive pr set --tap` and by every card draw, refuses a stack of more than four PRs or a PR that needs no card.

**A confirmation of a skill update**, sent twice: the ack states in plain words what skill update is being made, and a summary confirms it is merged and installed, listing each rule now in the skill.

**A fix-live note in a bug report's thread**, posted once after the fix ships and is verified: one or two plain sentences stating what is fixed and since when, with the link, and no progress notes, jargon, or asks to retry or confirm.

```
Fixed: Iris picks up again right after you answer its question, on today's build.
```

**The status page**, which `drive watch` redraws automatically on the drive's canvas tab under 40,000 bytes; link it when a line says where things stand.

## When it goes out

- **His message gets its ack inside 5 to 10 seconds** in the communicator's first message, unless cc-slack already posted `"Got it, back in a minute."` during compaction and marked it `acked`.
- **Everything else waits for the 30-minute `TICK` summary**, where the coordinator sends at most one `report` per thread and marks covered orders with `drive order set <o> --lined`, except incident lines every 5 minutes by [incidents.md](incidents.md).
- **While he is away** (`drive heard --away` or an hour of silence), post only waiting PR or question cards and required incident lines.
- **His next message gets one catch-up at his DM root** right after its ack, titled `*Catch-up since <the time he was last heard>*`, grouping everything from `drive heard` by feature with waiting items last, followed by `--lined` on each order.
- **His read-only question gets its answer within minutes** from a handler that reads the authoritative source (including staff routes) directly.

## What goes in

- **What changed for him first**, in numbers rather than adjectives ("129 of 476 passed, up from 100"), answering his natural follow-up (why, prior decision, port status, next step, or hold reason) in the same message.
- **His names, not ours.** Replace internal identifiers (pipeline steps, job/record/card/row IDs, hashes, lanes, file or function names) with plain descriptions of what they do, keeping only product or tool names he uses in backticks.
- **Self-contained context.** Assume he has no other context: explain what any number, fraction, size, or ratio measures, why it has that value, and a concrete example; introduce any needed mechanism in one plain sentence; and never point to "the third finding" or "as above". Write `report`, `ready` and `ask` records the same way:

  ```
  To open a run's page the browser first fetches a small index from the end of every journal file.
  It used to fetch the last 64 KiB of every file; now it fetches the last one-sixteenth of each file
  (about 6 KiB of a 97 KiB file), never more than 64 KiB.
  ```
- **Links for everything he could open**, where it is named. Name every PR by what it does in its link text (`<url|the run page shows its history>`) and state what order it serves (or `"port of #N"` with `#N` linked); if a record mentions a change without its PR, the handler finds it first with `drive status` or `gh pr list --search`. Put links for items named in a card's title or options into its summary or Show me caption. `drive job ask` and the post hook refuse unlinked references.
- **Observed proof only.** State how a live fix was checked with a link, and turn any unproven question into a job instead of listing "unproven" or "untested" items (`UNPROVEN:` in a job is coordinator input only). Incident lines follow [incidents.md](incidents.md) with no side release facts.
- **Live state from `drive status`, GitHub, or transcripts**, never memory or inference. Check `drive status <pr>` before asking for a tap; `drive job ask` and the hooks refuse a card, record, or post asking to tap an already-approved, merged, or closed PR.

## What stays out

- **Machinery details** (pins, packs, handoffs, hook refusals) unless he asks why; if a hook refuses a post he ordered, say in one line that it goes out once the fix is installed.
- **Unapproved external posts, or second asks for approved ones.** A post nobody ordered (such as a reply to a bug reporter or a line for a teammate) needs his approval once on a question card; a post he ordered in his DM, in a terminal, or via a card tap needs no further approval, and any open card asking to confirm it is closed immediately.
- **Asks of a bug reporter**, whether in a bug thread or as a card option.
- **Zero-news messages, duplicate copies, current timestamps, or estimated times.** React with `+1` to chatter instead of acking, and compute promised times from `date` or `+30m` on `drive order set <o> --due` read back via `drive status`.

## Tonight's posts, rewritten

Short status updates lead with the user-facing effect and concrete counts:

```
*Your PR cards keep their buttons while CI is red*
Tapping Approve on any open PR now lands it once CI passes. Pictures on cards go live by 1:30 AM.
```

```
*1 of 5 tooling fixes is merged; none is on your Mac yet*
The other four are in review and land by 11:10 PM; each goes live within minutes of merging.
```

A design question goes out as a self-contained question card (~120 words) with its Show me above the options:

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

The message-limits hook runs `cc-slack check-post` on every post and edit, checks word limits and required links, and requires the handler to have read the target thread within the last 10 minutes. Before posting, read the message as he will on a phone with no other context: verify that what changed, whether he must act, and when he hears next are clear, every number and term stands alone, and every openable item carries its link.
