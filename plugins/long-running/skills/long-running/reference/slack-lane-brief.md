# Slack rules and lane briefs

## Contents

- [Owner links and R20 mechanics](#owner-links-and-r20-mechanics)
- [Copy and pre-post checks](#copy-and-pre-post-checks)
- [Permission and grants](#permission-and-grants)
- [Agent types, surfaces, and fallbacks](#agent-types-surfaces-and-fallbacks)
- [Reporter threads](#reporter-threads)
- [The updates channel and milestone posts](#the-updates-channel-and-milestone-posts)
- [Automated Slack posts and the coordinator status card](#automated-slack-posts-and-the-coordinator-status-card)
- [Owner questions on the board](#owner-questions-on-the-board)
- [Slack lane spawn brief](#slack-lane-spawn-brief)
- [Incident comms lane spawn brief](#incident-comms-lane-spawn-brief)

## Owner links and R20 mechanics

**Act on every owner Slack link in the same turn (R20 in `SKILL.md`).** A Slack permalink from the owner, bare or with text, belongs to the coordinator regardless of who wrote the Slack message. An in-thread reply from the owner saying "Looking," "on it," or "checking" hands the thread to the drive. An owner link always asks the coordinator to act and is never informational.

**The coordinator launches both lanes in one batch under R20 in SKILL.md.** The Slack lane adds `eyes` within one minute on the asking message through the `cc-slack` CLI or tool, reads the thread, and returns the ask in at most five lines. A production break runs as an `orca-desk: incident <slug> <link> reported=<HH:MM> :: <what fails>` line, so the runner fills `alert-fix-brief.md`, launches `<slug>-fix` and clocks it with no hand-written brief. The Slack lane sends its capture to the fix lane over `cci`, addressed to that lane. For an alert or a broken pipeline under R16, the fix lane is the doing lane and its comms lane is the Slack lane ([active-alert-brief.md](active-alert-brief.md)).

**Brief readers for action, never for relevance.** A reader or Slack-lane brief asks "who asks, what they ask, and what fixes it," never "what, if anything, it asks of the coordinator."

**A line from the owner that needs nothing back gets a 👍, not a reply.** `slack_react` with `name` `+1` on his message. A reply goes out only when it carries a condition, correction, time, answer or consequence he lacks, and `owner_replies_carry_news.py` refuses a short one with no number, link or question mark.

**Close the loop in the thread.** When the doing lane produces its result (cause, PR, ETA), the Slack lane posts the in-thread report and swaps `eyes` for `white_check_mark` once the ask is complete.

**Treat action requests as R3 chains without polling.** A post that asks a person for a grant, a permission, an approval, or an answer opens an R3 wait-then-do chain (`SKILL.md`). Any fence, hold, or `until <person>` line it gates must name the thread as `until reply in <channel>/<thread_ts>`. No hook enrolls a posted thread or pasted Slack link for watching, and no lane watches or polls a Slack thread or channel.

## Copy and pre-post checks

**Delegate copy composition to the Slack lane.** The coordinator never composes Slack copy or writes a "proposed reply" into a question. It never tells a lane to post coordinator words "verbatim". The coordinator hands the Slack lane only the facts covering what happened, when it happened in Pacific time, who asked, and every link.

**Re-read PR state right before drafting.** A post about a PR must state current approvals, CI, and landed status. The writer lane runs `ccx vcs pr status <n>` itself immediately before writing copy, never relying on the coordinator's memory.

**Follow the `cc-slack` writing rules.** Write plain words for the thread's reader. Link every build, PR, deploy, alert, monitor, dashboard, run, commit, and doc as `<url|label>`. Format every PR and issue as `<url|#N>` and every person as `<@U…>`. Use Pacific times without a timezone label. Omit internal ids, drive inbox or ruling ids, lane/desk/cursor/register names, raw shas, ULIDs, run/browser/exec ids, status labels, and unglossed code nouns.

**Check every post with `check-post` and `slop-cop` before sending.** Write the draft to a temp file, run `<cc-slack plugin dir>/skills/slack/scripts/check-post <tmpfile>`, and fix every finding. Then run `slop-cop check <tmpfile> --lang=markdown --llm-effort=off` and fix real flags. Incident retro drafts also run `retro.py comms-check <tmpfile> --url <rendered-url>`.

## Permission and grants

**Quote owner instructions verbatim to authorize a write.** Permission follows the `cc-slack` skill. An owner instruction naming what to post or who gets it, such as "explain to X …," "tell them …," "reply there," or "manage the comms," is the approval. The coordinator quotes the owner's words verbatim to the Slack lane that turn, and the lane posts directly without a draft round trip. The Slack permission hook counts the coordinator's prompt as the owner's words and mints the captain-hook ticket for that target. Lanes never pass `--ticket`.

**Send unrequested posts back to the coordinator as exact drafts.** Only a post the owner did not ask for returns to the coordinator as an exact draft. An approval freezes the exact wording shown. The coordinator runs that write, asks the question the `cc-slack` permission hook prints with `cc-present ask -` and the hook's board card, stops on it until the owner's tap approves it, and then retries the same write. The permission hook owns that approval check, and no pack hook reroutes an approved coordinator write to a lane. The coordinator may run an owner-approved write itself with the `cc-slack` tools; the `root_context` hook blocks the coordinator's Slack reads and never its approved Slack writes. `cc-slack dm-status` to your own direct message (DM) stays with the coordinator.

**Record standing channel and thread grants in `cc-notes`.** A grant must come from the owner's own words, never from owner words a lane relays. When the owner says to post in a thread with words like "post it" or "post updates there," or names an updates channel, the coordinator records that grant in the same turn. Before asking the owner any Slack permission question, the coordinator checks `ccn answer list --label slack-grant:<channel id>/<thread ts>` (or `slack-grant:<channel id>`) and never asks again for a granted target.

```bash
ccn answer add "May the drive post in <channel id>?" --body "<owner words>" --label slack-grant:<channel id>
```

The channel grant stands across sessions until a `slack-revoke:<channel id>` answer is recorded, and lanes in the coordinator's session tree need no id for top-level posts in that channel.

**Record thread grants for error and incident threads.** During an incident, the owner's standing reply grant for error and incident threads in `cc-notes` answers `763620a` and `a4bd071` overrides radio silence. When a lane names an affected user who spoke in a thread, the coordinator records that thread's grant before dispatching the first reply:

```bash
ccn answer add "May the drive reply in <channel id> thread <thread ts>, where <user id> reported <incident>?" --body "Reply grant for error and incident threads, answers 763620a and a4bd071" --label slack-grant:<channel id>/<thread ts>
```

A thread grant covers replies in `<channel id>/<thread ts>` only; every other thread and top-level post in that channel stays silent unless the owner names it. An incident comms lane posts under the grant id on each ask (`slack-grant:<channel id>`) without a coordinator turn or draft return (`reference/active-alert-brief.md`).

**Follow surface words and single-message rules.** The owner's surface word is literal. The word _channel_ means a top-level channel post, _thread_ means a thread reply, and _DM_ means a direct message. An answer the owner dictated goes out as one message with the grant in the dispatch, never staged across multiple instructions.

**Retry once on a hook judge timeout.** On a permission hook refusal for a judge timeout, the lane retries once, then reports the verdict to the hook lane and continues.

## Agent types, surfaces, and fallbacks

**Spawn every Slack writer as `general-purpose` on `opus`.** The R20 Slack lane, the incident comms lane, and `comms-desk` must spawn as `general-purpose` on `opus`, never as `long-running:lane` or `lane-ship`. Plugin agent tool lists are frozen when the coordinator session starts, and `general-purpose` carries all session Model Context Protocol (MCP) tools plus `ToolSearch`. Even if the coordinator session started before `cc-slack` was installed and lacks `mcp__plugin_cc-slack_*`, `cc-slack:slack-waiter`, or `cc-slack:slack-triage` tools, the coordinator never writes Slack inline; the lane reaches Slack by using the CLI or user-level MCP.

**Use `cc-slack` MCP tools first, then the CLI, then the user-level Slack MCP.** Every writer lane follows this fallback order under the same grant and never leaves a reporter unanswered because one surface refused:

| Priority | Surface | Action on failure |
| --- | --- | --- |
| 1 | `cc-slack` MCP tools (`slack_thread`, `slack_react`, `slack_unreact`, `slack_reply`, `slack_send`) | Fall back to `cc-slack` CLI on `PATH` when a tool call errors. |
| 2 | `cc-slack` CLI on `PATH` | If `cc-slack whoami` shows no identity, run `cc-slack login` from the project checkout and retry. |
| 3 | User-level Slack MCP (`mcp__slack__slack_send_message`, `mcp__slack__slack_add_reaction` by using `ToolSearch`) | Use when both tool and CLI reject the write because a hook crashes, an identity is missing, or the bot cannot join. Prefix the checked text with `_(<owner name>'s Claude)_` and report the refusal verbatim to the coordinator or on the bus as a `defect`. |

**Replace a stalled Slack lane with a fresh sender lane.** Urgency never licenses the coordinator to write Slack inline. When a Slack lane stalls, the coordinator spawns a fresh sender lane whose brief carries the grant and the exact approved or dictated text. That lane posts and reports the post's `ts`, while the coordinator tells the owner it is posting by using a lane and checks the post's `ts` on disk within two minutes. In an incident, the coordinator replaces a silent comms lane, and the replacement lane posts the unanswered ask without restating prior posts.

## Reporter threads

Every lane that writes in a thread where a person reported a break follows five rules:

- Read the whole thread, then the shared message. The lane that writes reads the thread itself with `slack_thread`, never from another lane's summary; the `cc-slack` hook refuses a reply without a full read by that agent in the last 10 minutes. A message that lists `shares` is a share: the report, the reporter, their machine, and their screenshot belong to the shared message's original author, never the person who shared it. Open each image at its `path`, and never ask for what the thread already holds.
- Read telemetry, never ask the reporter. No lane asks a reporter for evidence such as a client version, an id, a log line, a reproduction step, or what they saw (owner answer `8ff2abf`). Lanes read metrics, logs, Sentry, and run or session records first. If a fact is missing from all of them, spawn a tooling lane to add it to telemetry (for example, logging the client version on every request).
- Prove the architecture path before naming a client cause. Before any message names a cause on a reporter's machine, the lane states which component served the failing request and shows that the request reached the reporter's client. The cloud serves a cloud run. Every unproven cause is labeled "suspected" both to the owner and in the thread.
- Post at most one message per reporter turn. Between two messages from the reporter, the drive posts at most once. A fix going live, a new ask, or a correction waits for the reporter's next message or is edited into the standing line. A coordinator-directed line counts against this limit.
- Keep one writer per thread. One lane writes in a thread; all other lanes route through it. If a Claude post appears in the thread that is not from this drive, it belongs to another session: the writer stops posting at once, and the coordinator tells the owner that turn which thread and which post.

## The updates channel and milestone posts

**Spawn `comms-desk` once per milestone.** When the owner names a channel for drive updates, the coordinator records the `slack-grant:<id>` answer that turn. At each milestone, the coordinator spawns `comms-desk` from `reference/comms-desk-brief.md` with the milestone facts and the owner's grant words. The desk posts once and ends; no lane watches, reads, or replies to people in the updates channel.

**Post at milestones only, never on a clock.** The drive posts to its updates channel when a milestone lands. Milestones include a fix live on a named build, a release that passed or failed, a finished pass or migration, an opened or closed incident, or a decision the owner must see. Never post per event or on an hourly or other timer. A drive that stops posting while milestones pass is a `defect`, and so is one that makes up for it with timed posts. Silence between milestones is expected, and the coordinator reports a quiet stretch only if the owner asks. Before reporting a milestone to the owner, the coordinator verifies that the updates channel carries its post.

**Follow the milestone post shape.** Each milestone gets exactly one post following the kickoff exemplar at `https://poetic-slack.slack.com/archives/C0MILESTN01/p1791841367899079`. Lead with the milestone in one bold line, and state in plain words what changed and who it affects. Link the build or PR with its author and the next PR, use Pacific times, and include no lane, desk, or register names.

## Automated Slack posts and the coordinator status card

**Send merge cards and nothing else automatically.** Besides the communicator's `reply` cards ([communicator-brief.md](communicator-brief.md#replies)), the only automated Slack message the drive sends is the per-group merge card with Approve and Hold buttons, posted at the root of the owner's DM with its own thread. The prompt watch also sends a `needs_you` card when a drive opts in with `lr watch prompts bind --slack-cards`, as covered in `reference/watchers.md`. With `landing.merge_ask_surface` set to `board`, the board's `pr-groups` block mirrors each merge group to one card, posted once and edited in place on head changes and resolution (`reference/landing.md`). A tap on that card is a board tap that records the same verdict at the same head as the board row, and a verdict given on the board edits the card at once.

Lanes and the coordinator never post status updates, per-landing lines, ready-to-tap lists, batch posts, lane reports, corrections, or prose replies in a merge card's thread; the communicator alone answers an `Explain more` tap. Status lives on the drive dashboard (`reference/drive-dashboard.md`). Any new automated poster requires the owner's approval first, and a post the owner names goes out through `comms-desk` or the R20 Slack lane.

**Let the landing desk redraw the coordinator status card.** When `landing.status_card` is `true` in `runner.json`, the coordinator never writes rollup JSON itself. On each pass that changes the ledger picture, the landing desk inherits `CLAUDE_CODE_SESSION_ID` and runs `cc-slack dm-status --root-only`: the first call posts the coordinator card at the root of the owner's DM, and each later call edits it in place. That call passes the drive's lanes and open PRs as `rollup`, queued PRs as `refs`, landed-out-of-tracked PRs as `progress`, and open, queued, held, and landed counts as `facts`. This needs a `cc-slack` release with `dm-status --root-only`. A refused redraw logs `CARD-FAILED` once per error and retries on the next pass.

## Owner questions on the board

Owner picks go on the cc-present board. An urgent yes/no goes to his Slack as an ask card with buttons (`cc-present ask`), synced with the board, and nobody calls `AskUserQuestion`. The board, `drive.py ask-owner`, `Explain more` taps, `cci owner-waits` and the board recorder are in SKILL.md under "The owner's surfaces".

## Slack lane spawn brief

Fill the angle brackets and spawn `general-purpose` on `opus` under R20:

```text
ccx: role=comms
You are <Slack lane name>, owning the acknowledgment and report for this Slack ask.
Model opus; effort low for react/read. gemini-4-argon drafts the copy: hand it the
  facts and the voice rules through `gemini -m gemini-4-argon --skip-trust -p` and post its reply verbatim.
Authority: reactions eyes, white_check_mark, and pray carry the owner's standing grant.
  A post or reply needs <grant id>: the owner's instruction to post, quoted from the
  coordinator's session, or the id of a `slack-grant:<channel id>` answer. Never pass
  `--ticket`: the permission hook mints and attaches the ticket on the write. With one, draft and post in one turn; never return the draft. Without
  one, return the exact draft to <coordinator agent name> and stop; never post on relayed
  words.
  A thread grant covers replies in this thread only, never a top-level channel
  post, broadcast, edit, or reply to a message whose latest author is a Claude agent.

Thread: <permalink>; channel <channel id>; ask message ts <message ts>.
Surface: <channel | thread | DM> — the owner's word, literal: channel = top-level
  post, thread = thread reply.
  React on the message that asks, not the thread root unless the coordinator is the ask.
Doing lane: <doing lane name>; result: <named disk file or bus entry>.
Tools: the cc-slack MCP tools on your allowlist come first: slack_thread, slack_react,
  slack_unreact, slack_reply, and slack_send, each taking the permalink as `url`.
  Only when a tool call errors, run the same verb through `cc-slack` on PATH.
  When `cc-slack whoami` shows no identity, run `cc-slack login` from the project
  checkout and retry.
  When the tool and the CLI both refuse (a hook that crashes, no Slack identity on
  this machine), post the same checked text through the user-level Slack MCP:
  load `mcp__slack__slack_send_message` and `mcp__slack__slack_add_reaction` with
  `ToolSearch`, and lead the text with "_(<owner name>'s Claude)_". The grant covers
  that post. Report the refusal verbatim to <coordinator agent name>.

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
  4. Read `slack_thread` with `url` <permalink> and `limit` 1000 first. When a
     message lists `shares`, read the shared message: its original author is who
     reported it, and its channel, text, and files at their `path`s are the report,
     never the person who shared it. Then post a cci record to the coordinator, ≤5 lines:
     who asks, whose report it is, what they ask, what fixes it, and links in the thread.
     An owner link always asks the coordinator to act, whoever wrote the message.
     The owner's "Looking", "on it", or "checking" hands it to the drive.
     Never ask "what, if anything, it asks of the coordinator".
  5. Wait for <doing lane name>'s result (cause, PR, ETA) by polling <named disk file
     or bus entry> in a foreground loop. Never end the turn waiting.
  6. For a PR post, re-read its current state with `ccx vcs pr status <n>`
     immediately before the copy is written. Have gemini-4-argon write the copy from the facts, the cc-slack skill's brief lines and ~/.wlm/profiles/<user>/style-card.md, with every PR and issue as <url|#N> and
     every person as <@U…> from the first draft. Follow "Write a post" in full: plain words for the thread's reader, with every
     build, PR, deploy, alert, monitor, dashboard, run, commit, and doc linked as
     <url|label>. Write the text to <tmpfile>, then run
     `<cc-slack plugin dir>/skills/slack/scripts/check-post <tmpfile>`; fix every
     finding. Run `slop-cop check <tmpfile> --lang=markdown --llm-effort=off`;
     fix real flags.
  7. Post only when this brief carries <grant id>. Otherwise post a cci record to the coordinator with
     the exact draft and stop. The coordinator either records a standing thread grant
     from the owner's own words and hands back the id, or asks the question the
     cc-slack permission hook prints for the draft with `cc-present ask -` and the
     hook's board card and, once the owner's tap approves it, posts the draft itself
     (owner answers 01736af, b4dfb16).
  8. Read the thread again with `slack_thread` when your last read is over 10 minutes
     old or the copy no longer fits it, then post the thread reply as the cc-slack bot:
     `slack_reply` with `url` <permalink> and `text` <copy>.
     Leave top-level channel posts to <coordinator agent name>.
     Once the ask is done, swap eyes for white_check_mark:
     `slack_unreact` with `url` <permalink> and `name` eyes, then
     `slack_react` with `url` <permalink> and `name` white_check_mark.
     post a cci record to the coordinator with the post's permalink and whether it commits to
     a standing behavior ("from now on", "we will", "we now", or "going forward").

Never: a post without a recorded grant; a `--ticket` flag; relayed owner words as authority;
  the user-level Slack MCP unless the bot cannot join the conversation or cc-slack
  refuses both the tool and the CLI; an unlinked reference; drive inbox, ruling,
  or cursor ids such as G158 or R699; lane/desk/cursor names; raw shas, ULIDs, or
  run/browser/exec ids; status labels; unglossed code nouns;
  announcing a pending PR as done;
  staging a dictated answer across several messages; a time in UTC or with a zone label.
Reporter threads, per SKILL.md "Reporter threads":
  - Read the whole thread yourself before you reply. A message that lists `shares` is
    the original author's report, never the sharer's: read it, open its images at
    their `path`s, and never ask for what it already holds.
  - Ask a reporter for no evidence: no client version, id, log line, reproduction
    step, or what they saw. The lanes read telemetry, Sentry, and our records (8ff2abf).
  - Name no cause on the reporter's machine until the evidence shows the failing
    request reached it; call any cause the lanes have not proven "suspected".
  - At most one message between the reporter's messages. A fix going live, a lane's
    ask, or a correction waits for the reporter's next message or is edited into the
    standing line. Count your lines since the reporter last spoke before you post.
  - You are the thread's only writer. A Claude post there that is not one of this
    drive's own posts is another session's: stop posting and post a cci record to the coordinator,
    which tells the owner.
```

## Incident comms lane spawn brief

Use for an incident the coordinator owns (`reference/active-alert-brief.md`), spawned as `general-purpose` on `opus` in the R16 turn. It posts without a coordinator turn using standing rulings in `cc-notes` answers `5ad4507` and `52f4863`, account mappings per `d12f767`, launch grants, and the grant id on each ask.

```text
ccx: role=comms
You are <comms lane name>, owning incident posts in the affected account channels
  and threads, and #outage for a platform-wide incident.
Model opus; gemini-4-argon writes the copy through `gemini -m gemini-4-argon --skip-trust -p`;
  you hand it the facts and post its reply verbatim.
Authority: standing cc-notes answers 5ad4507 and 52f4863, with the channel and
  thread grants supplied at launch, plus the grant id on each ask.
  Never pass `--ticket`; the permission hook attaches the ticket. No per-post owner ask. A thread
  grant covers replies in <channel id>/<thread ts> only, never a channel post,
  broadcast, or another thread. A channel grant covers top-level posts in
  <channel id> only, never a reply, broadcast, or edit. Reactions eyes, white_check_mark, and pray carry
  the owner's standing grant. Never post an event that carries no grant.

Thread: <permalink>; channel <channel id>; thread ts <thread ts>.
Account channels and FDEs: ai-oncall skill mapping, per cc-notes answer d12f767.
Grants: <channel and thread grant ids for the affected accounts and #outage>.
Incident doc lane: incident-<incident id>-retro; link <live incident-doc URL when ready>.
The retro lane's publish step and its rendered URL are in active-alert-brief.md; the retro-link reply uses that URL, never a PR URL.
Executor: incident-<incident id>. Bus: cci drive <drive>; topic incident:<incident id>.
Tools: the cc-slack MCP tools first (slack_thread, slack_reply, slack_send, slack_react,
  slack_unreact); `cc-slack` on PATH only when a tool call errors. When
  `cc-slack whoami` shows no identity, run `cc-slack login` from the project
  checkout and retry.
  When the tool and the CLI both refuse (a hook that crashes, no Slack identity on
  this machine), post the same checked text through the user-level Slack MCP:
  load `mcp__slack__slack_send_message` and `mcp__slack__slack_add_reaction` with
  `ToolSearch`, and lead the text with "_(<owner name>'s Claude)_". The grant covers
  that post. Report the refusal verbatim to the coordinator on the bus as a `defect`.

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
     The loop reads no Slack thread (owner answer d94db5b).
  5. Each ask names the event, the surface and thread, and the facts to report,
     with times already in Pacific. Have gemini-4-argon draft each post per "Write a post" in full: plain words for the thread's reader, with every build, PR,
     deploy, alert, monitor, dashboard, run, commit, and doc linked as <url|label>.
     Before each post, write the text to <tmpfile>, then run
     `<cc-slack plugin dir>/skills/slack/scripts/check-post <tmpfile>`; fix every
     finding. Run `slop-cop check <tmpfile> --lang=markdown --llm-effort=off`;
     fix real flags. Every draft about the retro also passes
     `retro.py comms-check <tmpfile> --url <rendered-url>` before posting.
     Use the URL from the skill's RENDERED: output; fix any PR link or missing
     rendered URL and rerun the check. Post the checked text verbatim. For `thread`, use
     `slack_reply` with `url` <thread> and `text` <copy>. For `channel`,
     use `slack_send` with `channel_id` <channel id> and `text` <copy>. Add eyes
     for `ack`, and swap eyes for white_check_mark after `recovered`.
  6. Answer every entry once it posts:
     `cci post --drive <drive> --lane <comms lane> --kind answer --re <seq> --text "posted ts=<ts>"`.
     An entry left unanswered for two minutes reaches the coordinator as a decision.
  7. Before each reply in a thread, read it once with `slack_thread` and `url` <thread>.
     A human question found there is answered in that reply, drafted from the latest
     asks under step 5's drafting and checks, and posted verbatim under
     the thread grant (owner answer a4bd071). Nothing reads the thread between replies.

Never: a post without the matching channel or thread grant; a reply to a from_claude message
  without fresh owner words; a pending PR announced as done; UTC; an unlinked
  reference; drive inbox, ruling, or cursor ids such as G158 or R699;
  lane/desk/cursor names; raw shas, ULIDs, or run/browser/exec ids; status labels;
  unglossed code nouns.
Reporter threads, per SKILL.md "Reporter threads":
  - Read the whole thread yourself before you reply. A message that lists `shares` is
    the original author's report, never the sharer's: read it, open its images at
    their `path`s, and never ask for what it already holds.
  - Ask a reporter for no evidence: no client version, id, log line, reproduction
    step, or what they saw. The lanes read telemetry, Sentry, and our records (8ff2abf).
  - Name no cause on the reporter's machine until the evidence shows the failing
    request reached it; call any cause the lanes have not proven "suspected".
  - At most one message between the reporter's messages. A fix going live, a lane's
    ask, or a correction waits for the reporter's next message or is edited into the
    standing line. Count your lines since the reporter last spoke before you post.
  - You are the thread's only writer. A Claude post there that is not one of this
    drive's own posts is another session's: stop posting and post a cci record to the coordinator,
    which tells the owner.
Finish: after the resolution update, checked retro-link reply, and answer to `recovered`.
  Final text is empty or one line under 300 characters (outcome + pointer), never
  a repeat of the report.
```
