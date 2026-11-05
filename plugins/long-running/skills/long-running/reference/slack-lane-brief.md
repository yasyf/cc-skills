# Slack rules and lane briefs

## Contents

- [Owner links and R20 mechanics](#owner-links-and-r20-mechanics)
- [Copy and pre-post checks](#copy-and-pre-post-checks)
- [Permission and grants](#permission-and-grants)
- [Agent types, surfaces, and fallbacks](#agent-types-surfaces-and-fallbacks)
- [Reporter threads](#reporter-threads)
- [The bug list](#the-bug-list)
- [The updates channel and milestone posts](#the-updates-channel-and-milestone-posts)
- [Automated Slack posts and the coordinator status card](#automated-slack-posts-and-the-coordinator-status-card)
- [Owner questions on the board](#owner-questions-on-the-board)
- [Slack lane spawn brief](#slack-lane-spawn-brief)
- [Incident comms lane spawn brief](#incident-comms-lane-spawn-brief)

## Owner links and R20 mechanics

**Act on every owner Slack link in the same turn (R20 in `SKILL.md`).** A Slack permalink from the owner, bare or with text, belongs to the coordinator regardless of who wrote the Slack message. It always asks the coordinator to act and is never informational. An in-thread reply from the owner saying "Looking," "on it," or "checking" hands the thread to the drive.

- **Launch both lanes in one batch.** The Slack lane adds `eyes` within one minute on the asking message through the `cc-slack` CLI or tool, reads the thread, and returns the ask in at most five lines. On a public channel it adds `wrench` beside `eyes` when the doing lane starts.
- **A production break runs as an `orca-desk: incident <slug> <link> reported=<HH:MM> :: <what fails>` line.** The runner then fills `alert-fix-brief.md`, launches `<slug>-fix` and clocks it, with no hand-written brief. The Slack lane sends its capture to the fix lane over `cci`, addressed to that lane.
- **For an alert or a broken pipeline under R16, the fix lane is the doing lane and its comms lane is the Slack lane** ([active-alert-brief.md](active-alert-brief.md)).
- **Brief readers for action, never for relevance.** A reader or Slack-lane brief asks "who asks, what they ask, and what fixes it," never "what, if anything, it asks of the coordinator."
- **A line from the owner that needs nothing back gets a 👍, not a reply.** Use `slack_react` with `name` `+1` on his message. Reply only when the reply carries a condition, correction, time, answer, or consequence he lacks. `owner_replies_carry_news.py` refuses a short reply with no number, link or question mark.
- **Close the loop in the thread.** When the doing lane has the answer to the ask, or the fix is live, the Slack lane posts the one in-thread report under [Reporter threads](#reporter-threads) and swaps `eyes` and any `wrench` for `white_check_mark` once the ask is complete. A cause, PR or ETA alone is progress, never a post.
- **A post that asks a person for a grant, a permission, an approval, or an answer opens an R3 wait-then-do chain** (`SKILL.md`). Any fence, hold, or `until <person>` line it gates names the thread as `until reply in <channel>/<thread_ts>`. No hook enrolls a posted thread or pasted Slack link for watching, and no lane watches or polls a Slack thread or channel. A thread about someone else's tooling or release, reaching the drive's Slack watch, is no incident, no bug-list item and no reason to post; drop its watch.

## Copy and pre-post checks

- **The Slack lane composes the copy.** The coordinator never composes Slack copy, never writes a "proposed reply" into a question, and never tells a lane to post coordinator words "verbatim". It hands the Slack lane only the facts: what happened, when it happened in Pacific time, who asked, and every link.
- **Re-read PR state right before drafting.** A post about a PR states current approvals, CI, and landed status. The writer lane runs `ccx vcs pr status <n>` itself immediately before writing copy, never relying on the coordinator's memory.
- **Follow the `cc-slack` writing rules.**
  - Write plain words for the thread's reader.
  - Link every build, PR, deploy, alert, monitor, dashboard, run, commit, and doc as `<url|label>`. Format every PR and issue as `<url|#N>` and every person as `<@U…>`.
  - Use Pacific times without a timezone label.
  - Omit internal ids, drive inbox or ruling ids, lane/desk/cursor/register names, raw shas, ULIDs, run/browser/exec ids, status labels, and unglossed code nouns.
- **Check every post before sending.** Write the draft to a temp file and run `<cc-slack plugin dir>/skills/slack/scripts/check-post <tmpfile>`; fix every finding. Then run `slop-cop check <tmpfile> --lang=markdown --llm-effort=off` and fix real flags. Incident retro drafts also run `retro.py comms-check <tmpfile> --url <rendered-url>`.

## Permission and grants

**Every Slack write needs the owner's permission. The `cc-slack` skill owns the mechanics.**

### Writes the owner asked for

- An owner instruction naming what to post or who gets it ("explain to X …," "tell them …," "reply there," "manage the comms") is the approval.
- The coordinator quotes the owner's words verbatim to the Slack lane that turn, and the lane posts directly with no draft round trip.
- The Slack permission hook counts the coordinator's prompt as the owner's words and mints the captain-hook ticket for that target. Lanes never pass `--ticket`.
- The owner's surface word is literal: _channel_ means a top-level channel post, _thread_ a thread reply, _DM_ a direct message.
- An answer the owner dictated goes out as one message with the grant in the dispatch, never staged across several instructions.
- A permission hook refusal on a judge timeout is a misfire of the fail-open rule below: the lane retries once, then launches the hook's fix lane ([SKILL.md](../SKILL.md#hooks-and-refusals)) and continues on what the refusal does not block.
- The Slack permission hook fails open: a hook error, a missing grant file, an unreadable ticket or a timeout warns and never refuses a post. The one block it keeps is a post with no grant and no tap.

### Writes the owner did not ask for

- A lane's or the coordinator's request to message a person is not the owner's yes. Post to a person only on the owner's own words or a standing grant; a hook ticket from an earlier reply in a thread does not cover a new request about a different subject.
- The post returns to the coordinator as an exact draft; an approval freezes the exact wording shown.
- The coordinator runs that write, asks the question the `cc-slack` permission hook prints with `cc-present ask -` and the hook's board card, stops until the owner's tap approves it, then retries the same write.
- The permission hook owns that check. No pack hook reroutes an approved coordinator write to a lane.
- The coordinator may run an owner-approved write itself with the `cc-slack` tools. The `root_context` hook blocks the coordinator's Slack reads and never its approved Slack writes.
- `cc-slack dm-status` to the owner's own direct message (DM) stays with the coordinator.

### Grants that stand

- **A grant must come from the owner's own words**, never from owner words a lane relays.
- **Record it the same turn.** When the owner says to post in a thread ("post it," "post updates there") or names an updates channel, the coordinator records the grant in `cc-notes`:

```bash
ccn answer add "May the drive post in <channel id>?" --body "<owner words>" --label slack-grant:<channel id>
```

- **Check before asking.** Before any Slack permission question, the coordinator runs `ccn answer list --label slack-grant:<channel id>/<thread ts>` (or `slack-grant:<channel id>`) and never asks again for a granted target. A grant is by subject, not by thread: one covering replies in any errors or incidents thread covers a thread the lane did not open, so search grants by subject before asking.
- A channel grant stands across sessions until a `slack-revoke:<channel id>` answer is recorded. Lanes in the coordinator's session tree need no id for top-level posts in that channel.

### Error and incident threads

During an incident, the owner's standing reply grant for error and incident threads overrides radio silence. When a lane names an affected user who spoke in a thread, the coordinator records that thread's grant before dispatching the first reply:

```bash
ccn answer add "May the drive reply in <channel id> thread <thread ts>, where <user id> reported <incident>?" --body "Reply grant for error and incident threads" --label slack-grant:<channel id>/<thread ts>
```

- A thread grant covers replies in `<channel id>/<thread ts>` only. Every other thread and top-level post in that channel stays silent unless the owner names it.
- An incident comms lane posts under the grant id on each ask (`slack-grant:<channel id>`) without a coordinator turn or draft return (`reference/active-alert-brief.md`).

## Agent types, surfaces, and fallbacks

**Spawn every Slack writer as `general-purpose` on `opus`, and never as a codex lane, which cannot post.** That covers the R20 Slack lane and `comms-desk`, while `lr launch incident` starts the incident comms lane as Claude on `incident-opus`; never spawn them as `long-running:lane` or `lane-ship`. Plugin agent tool lists are frozen when the coordinator session starts, and `general-purpose` carries all session Model Context Protocol (MCP) tools plus `ToolSearch`. Even if the coordinator session started before `cc-slack` was installed and lacks `mcp__plugin_cc-slack_*`, `cc-slack:slack-waiter`, or `cc-slack:slack-triage` tools, the coordinator never writes Slack inline; the lane reaches Slack by using the CLI or user-level MCP.

**Use `cc-slack` MCP tools first, then the CLI, then the user-level Slack MCP.** Every writer lane follows this order under the same grant and never leaves a reporter unanswered because one surface refused:

| Priority | Surface | Action on failure |
| --- | --- | --- |
| 1 | `cc-slack` MCP tools (`slack_thread`, `slack_react`, `slack_unreact`, `slack_reply`, `slack_send`) | Fall back to `cc-slack` CLI on `PATH` when a tool call errors. |
| 2 | `cc-slack` CLI on `PATH` | If `cc-slack whoami` shows no identity, run `cc-slack login` from the project checkout and retry. |
| 3 | User-level Slack MCP (`mcp__slack__slack_send_message`, `mcp__slack__slack_add_reaction` by using `ToolSearch`) | Use when both tool and CLI reject the write because a hook crashes, an identity is missing, or the bot cannot join. Prefix the checked text with `_(<owner name>'s Claude)_` and report the refusal verbatim to the coordinator or on the bus as a `defect`. |

**Replace a stalled Slack lane with a fresh sender lane.** Urgency never licenses the coordinator to write Slack inline.

- The fresh lane's brief carries the grant and the exact approved or dictated text. It posts and reports the post's `ts`.
- The coordinator tells the owner it is posting by using a lane, and checks the post's `ts` on disk within two minutes.
- In an incident, the coordinator replaces a silent comms lane, and the replacement posts the unanswered ask without restating prior posts.

## Reporter threads

Every lane that writes in a thread where a person reported a break follows these six rules. Both spawn briefs below point here instead of repeating them:

- **Show a public-channel pickup on the message.** The lane adds `eyes` to the message it picks up when it first looks, and `wrench` beside it when work starts. Both carry the owner's standing rule on public channels and need no grant id.
- **Read the whole thread, then the shared message.** The lane that writes reads the thread itself with `slack_thread`, never from another lane's summary; the `cc-slack` hook refuses a reply without a full read by that agent in the last 10 minutes. A message that lists `shares` is a share: the report, the reporter, their machine, and their screenshot belong to the shared message's original author, never the person who shared it. Open each image at its `path`, and never ask for what the thread already holds.
- **Read telemetry, never ask the reporter.** No lane asks a reporter for evidence such as a client version, an id, a log line, a reproduction step, or what they saw. Lanes read metrics, logs, Sentry, and run or session records first. If a fact is missing from all of them, spawn a tooling lane to add it to telemetry (for example, logging the client version on every request).
- **Prove the architecture path before naming a client cause.** Before any message names a cause on a reporter's machine, the lane states which component served the failing request and shows that the request reached the reporter's client. The cloud serves a cloud run. Every unproven cause is labeled "suspected" both to the owner and in the thread. Bug-thread comms post only confirmed causes: hold the post until the cause is confirmed, and never post a cause that flips or a machine-side workaround.
- **Post to a person only when they must act or the fix they reported is live, once per fix.** A test, progress, or "a follow-up" is never a post, and a coordinator-directed line obeys the same rule. Hold the fix's one post until the fix is verified live, then put the result and whatever they must do in it, such as starting a new run. A correction or a gap is edited into that post. An answer to the reporter's question is always allowed, once per question. An incident's notices in a page thread, `#outage` or an account channel keep the events [active-alert-brief.md](active-alert-brief.md#incident-comms) names and post nothing between them. Before you post, name which of these it is; when it is none, post nothing.
- **Keep one writer per thread.** One lane writes in a thread; all other lanes route through it. If a Claude post appears in the thread that is not from this drive, it belongs to another session: the writer stops posting at once and posts a `cci` record to the coordinator, which tells the owner that turn which thread and which post.

## The bug list

The lane watching the users' bug channels numbers every report it opens and owns the list. It opens each item with one record that stamps the number:

```bash
cci post --kind incident --attr item=<n> --to root,bugs-comms --text "<reporter, permalink, their words>"
```

The stamp is what a grader reads. When a PR body names `item <n>`, the coordinator carries the `incident` or `correction` records stamped with that number into the grader's job, and the grader reads the report there. A number no record stamps says so in the job, and the PR grades as if it named no item. Words in a record's text open nothing. An item opened without the stamp gets one `--kind correction --attr item=<n> --re <seq>` record carrying the report's words. Each record stamps one number, so a report that opens two items gets two records.

## The updates channel and milestone posts

- **Record the grant when the owner names a channel.** The coordinator records the `slack-grant:<id>` answer that turn.
- **Spawn `comms-desk` once per milestone.** The coordinator spawns it from `reference/comms-desk-brief.md` with the milestone facts and the owner's grant words. The desk posts once and ends. No lane watches, reads, or replies to people in the updates channel.
- **Post at milestones only, never on a clock.** Milestones are a fix live on a named build, a release that passed or failed, a finished pass or migration, an opened or closed incident, or a decision the owner must see. Never post per event or on an hourly or other timer.
  - A drive that stops posting while milestones pass is a `defect`, and so is one that makes up for it with timed posts.
  - Silence between milestones is expected; the coordinator reports a quiet stretch only if the owner asks.
  - Before reporting a milestone to the owner, the coordinator verifies that the updates channel carries its post.
- **Follow the milestone post shape.** Each milestone gets exactly one post following the kickoff exemplar at `https://poetic-slack.slack.com/archives/C0MILESTN01/p1791841367899079`. Lead with the milestone in one bold line, and state in plain words what changed and who it affects. Link the build or PR with its author and the next PR, use Pacific times, and include no lane, desk, or register names.

## Automated Slack posts and the coordinator status card

**Send merge cards and nothing else automatically.**

- Besides the communicator's `reply` cards ([communicator-brief.md](communicator-brief.md#replies)), the only automated Slack message the drive sends is the per-group merge card with Approve and Hold buttons, posted at the root of the owner's DM with its own thread.
- The prompt watch also sends a `needs_you` card when a drive opts in with `lr watch prompts bind --slack-cards` (`reference/watchers.md`).
- With `landing.merge_ask_surface` set to `board`, the board's `pr-groups` block mirrors each merge group to one card, posted once and edited in place on head changes and resolution (`reference/landing.md`). A tap on that card is a board tap that records the same verdict at the same head as the board row, and a verdict given on the board edits the card at once.
- Lanes and the coordinator never post status updates, per-landing lines, ready-to-tap lists, batch posts, lane reports, corrections, or prose replies in a merge card's thread. The communicator alone answers an `Explain more` tap. Status lives on the drive dashboard (`reference/drive-dashboard.md`).
- Any new automated poster requires the owner's approval first. A post the owner names goes out through `comms-desk` or the R20 Slack lane.

**Let the landing desk redraw the coordinator status card.** When `landing.status_card` is `true` in `runner.json`, the coordinator never writes rollup JSON itself.

- On each pass that changes the ledger picture, the landing desk inherits `CLAUDE_CODE_SESSION_ID` and runs `cc-slack dm-status --root-only`. The first call posts the coordinator card at the root of the owner's DM; each later call edits it in place.
- The call passes the drive's lanes and open PRs as `rollup`, queued PRs as `refs`, landed-out-of-tracked PRs as `progress`, and open, queued, held, and landed counts as `facts`.
- It needs a `cc-slack` release with `dm-status --root-only`. A refused redraw logs `CARD-FAILED` once per error and retries on the next pass.

## Owner questions on the board

Owner picks go on the cc-present board. An urgent yes/no goes to his Slack as an ask card with buttons (`cc-present ask`), synced with the board, and nobody calls `AskUserQuestion`. The board, `lr ask-owner`, `Explain more` taps, `cci owner-waits` and the board recorder are in SKILL.md under "The owner's surfaces".

## Slack lane spawn brief

Fill the angle brackets and spawn `general-purpose` on `opus` under R20:

```text
ccx: role=comms
You are <Slack lane name>, owning the acknowledgment and report for this Slack ask.
Model opus; effort low for react/read. gemini-4-argon drafts the copy: hand it the
  facts and the voice rules through `gemini -m gemini-4-argon --skip-trust -p` and post its reply verbatim.
Bound by `reference/slack-lane-brief.md`: read "Copy and pre-post checks", "Agent types,
  surfaces, and fallbacks" and "Reporter threads" first and follow them in full.
Authority: reactions eyes, white_check_mark, and pray carry the owner's standing grant,
  and wrench does on a public channel. A post or reply needs <grant id>: the owner's instruction to post, quoted from the
  coordinator's session, or the id of a `slack-grant:<channel id>` answer. Never pass
  `--ticket`: the permission hook mints and attaches the ticket on the write. With one,
  draft and post in one turn; never return the draft. Without one, return the exact
  draft to <coordinator agent name> and stop; never post on relayed words.
  A thread grant covers replies in this thread only, never a top-level channel
  post, broadcast, edit, or reply to a message whose latest author is a Claude agent.

Thread: <permalink>; channel <channel id>; ask message ts <message ts>.
Surface: <channel | thread | DM>, the owner's word, literal.
  React on the message that asks, not the thread root unless the coordinator is the ask.
Doing lane: <doing lane name>; result: <named disk file or bus entry>.
Tools: the cc-slack MCP tools first (`slack_thread`, `slack_react`, `slack_unreact`,
  `slack_reply`, `slack_send`), each taking the permalink as `url`. When one refuses,
  fall back as the fallback table says and report the refusal verbatim to
  <coordinator agent name>.

Do:
  1. Call Skill(cc-slack:slack) first. Follow "Write a post" and, except for step 2,
     "React before you reply".
  2. If this brief carries <grant id> for a thread reply, read the thread with
     `slack_thread`, `url` <permalink> and `limit` 1000, run both step 6 checks on
     its text, then post it before reacting:
     `slack_reply` with `url` <permalink> and `text` <text>.
     For a channel surface, post a cci record to the coordinator with the exact draft to post.
     Report the reply's ts; then continue with the react.
  3. Add eyes within one minute on the message that asks:
     `slack_react` with `url` <permalink> and `name` eyes.
  4. Read `slack_thread` with `url` <permalink> and `limit` 1000 first, and read any
     shared message. Then post a cci record to the coordinator, <=5 lines:
     who asks, whose report it is, what they ask, what fixes it, and links in the thread.
     An owner link always asks the coordinator to act, whoever wrote the message.
     The owner's "Looking", "on it", or "checking" hands it to the drive.
     Never ask "what, if anything, it asks of the coordinator".
     On a public channel, add wrench beside eyes when <doing lane name> starts:
     `slack_react` with `url` <permalink> and `name` wrench.
  5. Wait for <doing lane name>'s result (the answer, or the fix live) with `Monitor` on <named disk file
     or bus entry>, or end the turn on `blocked --lane-ref <doing lane name>` so the supervisor wakes you.
  6. For a PR post, re-read its current state with `ccx vcs pr status <n>`
     immediately before the copy is written. Have gemini-4-argon write the copy from the
     facts, the cc-slack skill's brief lines and ~/.wlm/profiles/<user>/style-card.md.
     Run both checks in "Copy and pre-post checks" on the text and fix every finding.
  7. Post only when this brief carries <grant id>. Otherwise post a cci record to the
     coordinator with the exact draft and stop. The coordinator either records a
     standing thread grant from the owner's own words and hands back the id, or asks
     the question the cc-slack permission hook prints for the draft with
     `cc-present ask -` and the hook's board card and, once the owner's tap approves it,
     posts the draft itself.
  8. Read the thread again with `slack_thread` when your last read is over 10 minutes
     old or the copy no longer fits it, then post the thread reply as the cc-slack bot:
     `slack_reply` with `url` <permalink> and `text` <copy>.
     Leave top-level channel posts to <coordinator agent name>.
     Once the ask is done, swap eyes and any wrench for white_check_mark:
     `slack_unreact` with `url` <permalink> and `name` eyes and then wrench, then
     `slack_react` with `url` <permalink> and `name` white_check_mark.
     Post a cci record to the coordinator with the post's permalink and whether it commits to
     a standing behavior ("from now on", "we will", "we now", or "going forward").

Never: a post without a recorded grant; a `--ticket` flag; relayed owner words as authority;
  the user-level Slack MCP unless the bot cannot join the conversation or cc-slack
  refuses both the tool and the CLI; announcing a pending PR as done;
  staging a dictated answer across several messages; a time in UTC or with a zone label.
```

## Incident comms lane spawn brief

Use for an incident the coordinator owns (`reference/active-alert-brief.md`), spawned as `general-purpose` on `opus` in the R16 turn. It posts without a coordinator turn under the channel and thread grants supplied at launch and the grant id on each ask.

```text
ccx: role=comms
You are <comms lane name>, owning incident posts in the affected account channels
  and threads, and #outage for a platform-wide incident.
Model opus; gemini-4-argon writes the copy through `gemini -m gemini-4-argon --skip-trust -p`;
  you hand it the facts and post its reply verbatim.
Bound by `reference/slack-lane-brief.md`: read "Copy and pre-post checks", "Agent types,
  surfaces, and fallbacks" and "Reporter threads" first and follow them in full.
Authority: the channel and thread grants supplied at launch, plus the grant id on each ask.
  Never pass `--ticket`; the permission hook attaches the ticket. No per-post owner ask. A thread
  grant covers replies in <channel id>/<thread ts> only, never a channel post,
  broadcast, or another thread. A channel grant covers top-level posts in
  <channel id> only, never a reply, broadcast, or edit. Reactions eyes, white_check_mark, and pray carry
  the owner's standing grant, and wrench does on a public channel. Never post an event that carries no grant.

Thread: <permalink>; channel <channel id>; thread ts <thread ts>.
Account channels and FDEs: ai-oncall skill mapping.
Grants: <channel and thread grant ids for the affected accounts and #outage>.
Incident doc lane: incident-<incident id>-retro; link <live incident-doc URL when ready>.
The retro lane's publish step and its rendered URL are in active-alert-brief.md; the retro-link reply uses that URL, never a PR URL.
Executor: incident-<incident id>. Bus: cci drive <drive>; topic incident:<incident id>.
Tools: the cc-slack MCP tools first (slack_thread, slack_reply, slack_send, slack_react,
  slack_unreact). When one refuses, fall back as the fallback table says and report the
  refusal verbatim to the coordinator on the bus as a `defect`.

Do:
  1. Call Skill(cc-slack:slack) first. Follow "React before you reply" and
     "Write a post". For a page or alert affecting a customer team, first post
     proactively in its account channel: acknowledge the page and say the
     investigation has started. Do this before the mechanism is known, with no
     per-post owner ask. Resolve channels and FDEs through the ai-oncall mapping;
     on a miss, search Slack channels by account name. Report which path found
     the channel and FDEs; never guess an FDE mention.
  2. After the account posts, open #outage when more than one customer or a core
     service is affected. Include impact, timeline, status, and prevention.
     Add the live incident-doc link to #outage and the account channels when the
     doc lane supplies it. Never wait for that link to acknowledge the page.
  3. Follow up in account channels at mechanism and fix-live, and in #outage at
     mechanism, fix-live, and resolution. Every account post, including the
     first, says what we are doing to prevent recurrence. State work underway
     without claiming an unverified fix. Use plain words and Pacific times;
     @-mention the account's FDEs. Apply step 5's drafting and checks to every post.
  4. The fix and evidence lanes post onto the same cci drive.
     Loop in foreground units of at most 60 seconds:
     `cci tail --drive <drive> --reader <comms lane> --kind ask --since 0 --json`.
     The loop reads no Slack thread.
  5. Each ask names the event, the surface and thread, and the facts to report,
     with times already in Pacific. Have gemini-4-argon draft each post per "Write a post".
     Before each post run both checks in "Copy and pre-post checks" and fix every
     finding. Every draft about the retro also passes
     `retro.py comms-check <tmpfile> --url <rendered-url>` before posting.
     Use the URL from the skill's RENDERED: output; fix any PR link or missing
     rendered URL and rerun the check. Post the checked text verbatim. For `thread`, use
     `slack_reply` with `url` <thread> and `text` <copy>. For `channel`,
     use `slack_send` with `channel_id` <channel id> and `text` <copy>. Add eyes
     for `ack`, add wrench beside it on a public channel when the fix lane starts,
     and swap both for white_check_mark after `recovered`.
  6. Answer every entry once it posts:
     `cci post --drive <drive> --lane <comms lane> --kind answer --re <seq> --text "posted ts=<ts>"`.
     An entry left unanswered for two minutes reaches the coordinator as a decision.
  7. Before each reply in a thread, read it once with `slack_thread` and `url` <thread>.
     A human question found there is answered in that reply, drafted from the latest
     asks under step 5's drafting and checks, and posted verbatim under
     the thread grant. Nothing reads the thread between replies.

Never: a post without the matching channel or thread grant; a reply to a from_claude message
  without fresh owner words; a pending PR announced as done; UTC.
Finish: after the resolution update, checked retro-link reply, and answer to `recovered`.
  Final text is empty or one line under 300 characters (outcome + pointer), never
  a repeat of the report.
```
