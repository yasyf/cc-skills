---
name: owner-message
description: The one way any lane, desk, subagent or the communicator tells the drive's owner anything, through lr say (the say tool on the lr MCP server), the only writer to his Slack DM, his card threads, his board and Talk. Holds the templates for a decision card, a PR tap card, a thread reply, a table, an incident line and an update, and every rule a message to him must meet, each refused by lr say or a hook before it posts. Use whenever you are about to send him a card, a reply, a question, a status, a table or an incident line, or to edit or pull one.
---

# Every message to the owner

One skill, one verb. When you have something for him, you send it yourself with `lr say`. Nobody else draws it for you, no draft goes to owner-relay or root, and no raw `cc-slack` or `cc-present` post reaches his Slack direct message (DM), since the `owner_writes_go_through_say` hook refuses one and names `say`.

`lr say` runs every check before anything posts. A rule it holds refuses, says the fix, and nothing goes out. A send for a message that already has a live post edits that post in place, so one PR at one head, or one question, is one post on every surface. The full rule text is in [reference/rules.md](reference/rules.md), and what follows is what you need to send.

## Before you send

1. Send only what he needs: a decision, something waiting on him, something live or decided that he asked about, or an incident line. Never progress chatter.
2. Pick the template below by what you are sending.
3. Write down the first question he asks of it and put the answer in the message.
4. Call `say`. If it refuses, fix what the refusal names and call it again with the same `id`.

## What every message meets

| Rule | His words |
|---|---|
| A choice is a card with 2 to 4 buttons, never A or B in text | "why aren't these buttons ... DO A PROPER SLACK EXPERIENCE" |
| Saying something waits on him carries its button in that message | "if you say something is waiting you have to include the buttons" |
| The title is one question in one sentence, ending in "?" | |
| Plain words, as to a five-year-old; no lane names, record ids, hashes, test names, file paths or code terms | "utterly incomprehensible" |
| Name the concrete things it changes and what a user gets | "you haven't said what the follow up's are" |
| Answer his next question in the message | "pre-empt my next question and answer it" |
| A card about a PR names its kind first: Port, Fix, New or Cleanup; a port says what differs in the new tree | "Ports need to always say they're a port" |
| A mechanism a card names is explained on the card | "You need to explain these things" |
| Counts of three or more go in a table; a comparison is a two-column table | |
| Tables are markdown tables, which reach Slack as native data tables | "use those new style nice slack tables" |
| Every PR, build, alert and release named is a link; a release word carries what users get and the Platy #releases permalink | "you have to link shit when you mention it" |
| A show-me by fit: a diagram, a screenshot, a sand api call and its result, or one sentence | "you're still not doing the show-me diagrams" |
| Words about a picture only with the picture | "this is the second time you've claimed there is and lied" |
| One copy: no second post for a live PR or question, no card in a thread and at his root | "I only need one copy" |
| A tap counts once per PR and carries across a patch-identical head; never ask what he answered | "I literally just answered this" |
| A card whose PR merges, closes or is superseded loses its buttons at once, done by the supervisor | "Next time do it before I tell you" |
| Never pull a card while he is writing in its thread | "I was reading and responding then this disappeared" |
| Every message of his gets eyes and the working line naming what is happening, from `lr owner watch`, cleared by your reply | "The bar should also say things like 'waiting for subagent'" |

## Templates

### Decision card

```json
{"title": "Cleanup: close #31950 and #31960, two October upgrades of our infrastructure tool that dev already has?",
 "md": "**In plain words:** two old changes from Oct 7, [#31950](https://github.com/Forge-AI/monorepo/pull/31950) and [#31960](https://github.com/Forge-AI/monorepo/pull/31960), move the tool we deploy our infrastructure with to a newer version. Dev already runs that version or a newer one.\n\n**What you get by closing them:** nothing you use changes.\n\n| | Close both | Keep them open |\n|---|---|---|\n| What happens | both close, with the reason written on each | they stay open and keep showing as blocked |\n| Cost | none | each needs rebuilding on today's dev |\n\n**Your next question, answered:** nothing depends on them.",
 "picks": ["Close both :: Both close now with the reason written on them; nothing changes for anyone.", "Keep them open :: They stay as they are and still need rebuilding before they could land."],
 "recommended": "Close both", "status": "needs_you", "order": "m2017"}
```

Each pick is `Label :: at most two sentences` with its pro, its con and its clock. Pass the asking record's seq in `ask`, the branch the recommended pick is built on in `build`, and the card it replaces in `replaces`.

### PR tap card

Send the PR in `prs`, and `lr say` draws its one card from the PR ledger. The header is its kind and question, and the ELI5 comes first. A field grid says what changes for users, where it stands and what it waits on. The plan line shows only when the PR departs from its plan. Then its risk in one plain sentence, its show-me, and Approve and Hold. A missing ELI5 or show-me is fixed in the PR body, never in the reply.

### Thread reply

Answer him where he wrote: `order` names his ledger row, and the reply lands in that row's thread. Two or three sentences: the answer, what it changes, his next question answered. A pick he is asked there is a card, in that thread only.

### Table

A markdown table in `md`, with rows read from live state, such as `lr board rows` or `gh`, never from memory. A row he can act on carries its button, as a card per row or `blocks` with row actions.

### Incident line

`status: incident` with `alert` set. One line each at spawn, at mechanism and at fix-live, carrying the alert's Slack message and its Sentry or Datadog issue as bare links that unfurl.

### Update

`update: true`. The tool writes it from the PR ledger and his board; add only an opening line in `md`.

## Edit or pull a message

- Change a message: call `say` again with its `id`. Every surface updates in place.
- Take a card down: `lr withdraw <card or #PR> --why <plain words>`. A duplicate names the card that stays with `--duplicate-of`.
- A card whose owning lane stood down is taken over by the lane that sends it next.
