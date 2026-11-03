# The owner report table

Every update the root gives the owner, whether a milestone report or a status answer, uses this table. The owner reads it to learn what is left of the plan, who is on each piece, what holds it, what the drive is doing about it, and what has gone wrong so far. Lead with the unowned work, then the table, then at most three plain sentences on what the owner must decide, if anything. `owner_update.py --render --drive <drive>` prints the whole update from sources: the plan's steps, the owner table's workstreams, the registry's live lanes under their steps, the ledger's asks, the board's open picks, today's incidents and releases, and every PR with a go owed or a hold. The progress desk runs it, the root relays it, and nobody assembles rows by hand. The generator exits 1 and names any live lane, open ask or open PR the text lacks, and a section with nothing new says so in one line rather than disappearing.

## Shape

| Column | What goes in it |
|---|---|
| Step | The plan's own step, numbered as the plan numbers it and named as the work in plain words, such as "Get every team's sessions into the new store," never an id or a unit code. |
| What it delivers | What is true for users or the system when the step is done, in one or two sentences. |
| State | One of not started, in progress, READY-HELD, queued, landed, live, or done. |
| Who is on it right now | The live lane's name from the registry, or **nobody** in bold. A row whose owner is nobody is also listed at the top of the update as unowned work. |
| What blocks it | The concrete thing in the way right now. A READY-HELD row names its gate and why that order, in one sentence. |
| What we are doing to unblock it, and by when | Who is doing what, now, and the time it is due. A blocker with nobody on it says so and names the lane the root starts that turn. |
| What went wrong or slowed it so far | A mini retro covering what stalled, slipped, or was wrong since the step started, and what it cost. Never blank. |

Every row the generator renders, a plan step, a workstream piece, an incident, a release, a lane or a PR, carries these columns. Include one row for each plan step, and split a step into lettered rows only when its parts sit in different states. The rows follow the plan's order, critical path first. Every READY-HELD row justifies its hold by naming the rule. Readers first means a writer waits until every reader of its format is live. A data gate means a deletion waits until nothing still needs the data. A hold without either reason is a defect, so lift it or name the real gate.

The retro column is mandatory on every row of every update. The `drive-watchdog` retro log from [drive-watchdog-brief.md](drive-watchdog-brief.md) feeds it, one `[step <n>]` entry per finding. "None recorded" is allowed only when that log has nothing for the step.

Plain words come first, and a PR number or record id may follow the description, at most one per cell. Only PRs are written `#N`, so `scripts/plan-table.py` can refresh their state, and lane names appear only in the unblock column. The live copy is one cc-notes doc labeled `owner-table` that the root edits at each update, while the plan carries the format and one filled example, not the live rows.

## Refreshing `plan-table.md`

When a `/live-dashboard` Plan table panel renders the live copy as described in [drive-dashboard.md](drive-dashboard.md), `scripts/plan-table.py` reads the `owner-table` doc and writes `<state dir>/dashboard/plan-table.md`. Each run refreshes the State column from one batched `ccx vcs pr status` call over every PR the table names and appends the watchdog's newest retro line per step. The root runs it when the owner asks for the dashboard, and no lane runs it on a clock.

```
plan-table.py --doc <table doc id> --retro-log <watchdog log id> --repo <checkout> \
  --out <state dir>/dashboard/plan-table.md
```

## Filled example

| Step | What it delivers | State | What blocks it | What we are doing to unblock it | What went wrong or slowed it so far |
|---|---|---|---|---|---|
| 1. Get every team's sessions fully into the new store | Every session that failed to move is either verified or fixed, so the old store can go. | in progress | 140 sessions failed in 11 different ways. The large ones hit the import's 1-minute mount deadline. | Write passes run on all four teams now, and a PR gives the import its own 10-minute deadline (#1234). | The same 47 sessions failed the same way on three full passes before anyone read the cause. The step had no owner for hours after the pass lane ended. |
| 2a. Remove the old journal formats | The old journal shape is gone from code, one deletion per release. | in progress | The first deletion went live at 4.18 AM. The third is READY-HELD by readers first because it raises the format the writer uses, so it waits until the second is live on every client. | The landing desk enqueues the second now, and it ships alone in the next release. | The release ahead of it took two hours, and every deletion behind it waited. |
| 2b. Drop the old session mirror | Sessions read only from the new store. | READY-HELD | Held by a data gate because dropping the mirror before every session has moved loses the ones that have not. It waits on step 1 and on a proof that an old thread still resumes. | The deletions lane restacks it and stages the proof now. | The restack was due at 4.05 AM and had not happened at 4.23 AM, when the watchdog flagged it. |
| 3. Switch the chat writer | Chat writes only the new tables, and old clients are told to update. | READY-HELD | Held by readers first because the writer waits until the chat readers are live everywhere, or it writes a layout a live reader cannot read. The client-floor PR is not opened yet. | The chat lane opens the client-floor PR now. | The root's 3.44 AM ask went unanswered for 35 minutes, and the answer covered one of its three parts. |
| 4. Prove the product works end to end | A recorded proof on released builds covering run, rerun, share, fork, and heal. | not started | Steps 2b and 3 must be live first. | The proof lane drafts the script now and runs it the turn they are. | It had no lane until the plan's eighth revision. |
| 5. Binding refs | The binding-refs writer ships. | landed | The release that carries it. | The release walker ships it in the next pass. | None recorded. |
