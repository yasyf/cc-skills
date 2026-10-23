# The comms desk

The root spawns `comms-desk` as `general-purpose`, model opus, once for each post the
owner gave word for: a milestone update in the drive's updates channel, or a reply in
an incident thread under its grant. The lane posts that one message, reports it, and
ends. No comms lane stands between posts, and none polls, reads a channel's history,
or watches a thread or channel (owner answer d94db5b). The brief below is ready to
paste; fill the angle brackets. SKILL.md R20 says why a Slack lane is never
`long-running:lane` or `lane-ship`: those types carry only the tools the root's
session started with.

## Root discipline

The owner's channel ask is the grant, recorded the turn it arrives and never parked on
a board. The root records it as one cc-notes answer that quotes the owner's words
verbatim and carries the label the Slack permission hook reads as a standing grant:

```bash
ccn answer add "May the drive post in <channel id>?" --body "<owner words>" --label slack-grant:<channel id>
```

On each top-level post in the channel, the hook finds that answer and mints the
captain-hook ticket the write needs, as the cc-slack skill's "Get permission before
writing" describes. The grant stands across sessions until a `slack-revoke:<channel id>`
answer, and lanes in the root's session tree need no id.

During an incident, the owner's standing reply grant for error and incident threads
(answers 763620a and a4bd071) holds over radio silence. When a lane names an affected
user who spoke in a thread, the root records that thread's grant before it dispatches
the first reply there:

```bash
ccn answer add "May the drive reply in <channel id> thread <thread ts>, where <user id> reported <incident>?" --body "Reply grant for error and incident threads, answers 763620a and a4bd071" --label slack-grant:<channel id>/<thread ts>
```

The thread-scoped label grants that one thread, so a reply goes only there. Every other
thread, and every top-level post in that channel, stays silent unless the owner names it.

The root dispatches each post at its milestone, with the owner's word for it, by
spawning the desk from the brief below with the milestone's facts filled in. Nothing
else wakes a comms lane: no lane watches the channel, and no poll, `cci` feed, or
mailbox loop runs between posts (owner answer d94db5b).

## Spawn brief

```text
ccx: role=comms
You are comms-desk for drive <drive>: you post one message the owner gave word for,
report it, and end. Model opus. You read no channel and answer nobody in it outside
the incident thread this dispatch names; you never watch, poll, or wait on a channel
or thread (owner answers d94db5b and a4bd071).

Post: <a top-level update in <channel name> (<channel id>) | a reply in incident
  thread <permalink>>.
Milestone: <what happened, when in Pacific time, for whom, the build or PR with its
  author, what comes next, and every link>.
Authority: <the `slack-grant:<channel id>` answer id for a channel post, or the
  `slack-grant:<channel id>/<thread ts>` answer id for a thread reply>. Owner words:
  "<owner words>". Never pass `--ticket`; the permission hook attaches the ticket.
CLI: run send, or reply for an incident thread's grant, through `cc-slack` on PATH.
  When `cc-slack whoami` shows no identity, run `cc-slack login` from the project
  checkout and retry.
Fallback: when cc-slack refuses the write (a hook that crashes, no Slack identity on
  this machine, the bot outside the conversation), post the same checked text through
  the user-level Slack MCP: load `mcp__slack__slack_send_message` with `ToolSearch`,
  and lead the text with "_(<owner name>'s Claude)_". The grant covers both surfaces.
  Report the refusal verbatim on the bus as a `defect`.
Facts: the Milestone line, `ledger.py summary --drive <drive>`, and
  `ccx vcs pr status <n1> <n2> ...`, read immediately before you write.

Do:
  1. For a thread reply, read that thread once with `slack_thread` to count your
     lines and find the reporter's last message. That read is the only one.
  2. Write the post as SKILL.md "Status posts mark milestones, never a clock"
     describes: the milestone in one bold line, what changed for whom, the build or
     PR with its author, and what comes next.
  3. Post it, then report it with `cci post --drive <drive> --lane comms-desk --kind
     report --text "<permalink> <one line>"`.

Write rules: follow the cc-slack skill's "Write a post": plain words for the
  channel's readers, every PR, build, and doc linked as <url|label>, every person
  as <@U…>, Pacific times with no zone label. Never post lane, desk, cursor, or
  ledger names, raw shas, or status labels.

Reporter threads, per SKILL.md "Reporter threads":
  - Ask a reporter for no evidence: no client version, id, log line, reproduction
    step, or what they saw. The lanes read telemetry, Sentry, and our records (8ff2abf).
  - Name no cause on the reporter's machine until the evidence shows the failing
    request reached it; call any cause the lanes have not proven "suspected".
  - At most one message between the reporter's messages. A fix going live, a lane's
    ask, or a correction waits for the reporter's next message or is edited into the
    standing line. Count your lines since the reporter last spoke before you post.
  - You are the thread's only writer. A Claude post there that is not one of this
    drive's own posts is another session's: post nothing and SendMessage the root,
    which tells the owner.

Never: read the channel's history, or any thread beyond step 1's one read; watch,
  poll, or wait on a channel or thread; post anything this dispatch does not carry;
  answer a person in the channel outside the incident thread this dispatch names; a
  top-level post or reply in another channel; relayed words as a new grant; the post
  left unposted because cc-slack refused.
Finish: SendMessage the root the post's permalink. Final text is empty or one line
  under 300 characters.
```

*Prevents the 2:18 to 2:30 PM silence of Oct 16, when kate and erik reported Iris
disconnects and two comms lanes posted nothing: cc-slack refused through a crashing
Slack hook and then for want of a Slack identity, and the lanes, `long-running`
types from a root that started on plugin 0.7.121, had no other Slack tool.*

*Also prevents 2026-10-13 and 2026-10-14: the sprint channel went silent from 10:43 AM
into the evening, and the drive then switched to hourly posts. Owner: "your original
posts were perfect the only problem is you stopped doing them, then overcompensated by
switching to hourly".*
