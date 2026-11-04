# The drive dashboard

Contents:
- [Dashboard app startup](#dashboard-app-startup)
- [Going live](#going-live)
- [Owner bar and user testing](#owner-bar-and-user-testing)
- [One model, one stream](#one-model-one-stream)
- [Seven states, one each](#seven-states-one-each)
- [What the drive supplies](#what-the-drive-supplies)
- [Words at rest](#words-at-rest)
- [Older `/live-dashboard` layout](#older-live-dashboard-layout)
- [Plan progress (`lr.plan-progress`)](#plan-progress-lrplan-progress)
- [Plan table](#plan-table)

## Dashboard app startup

At drive start, `lr dashboard install` writes the launchd job `com.yasyf.long-running.dashboard.<drive>` running `dashboard/app/server.py --config <state dir>/dashboard.json`. The config names the drive, ledger, board session and URL, repo, progress-doc label, release doc, story file, host, port, `tailnet_owner`, which is the owner's Tailscale sign-in, and keeper lane `update-desk`, the communicator's helper briefed from [communicator-brief.md](communicator-brief.md).

`python3 dashboard/app/keep.py --config <state dir>/dashboard.json` runs `lr dashboard restart` when the app is not answering and tells the coordinator; the job logs to `server.log` beside the config. The coordinator hands the owner its tailnet URL once, and never hands him a cc-present board in its place. A build failure posts one `defect` record to the coordinator.

## Going live

`lr dashboard restart` records the git tree of the dashboard it serves in `<state dir>/dashboard.served`. When the installed `dashboard/` tree differs and the app still answers, the restart refuses unless `--proof <file>` proves that tree or `--ordered <m-id>` names the owner's own order in his ledger. A restart of an unchanged tree, or of an app that stopped answering, needs neither, so crash recovery is never held. The proof is JSON:

```json
{"commit": "<full sha>", "tree": "<git rev-parse <commit>:plugins/long-running/dashboard>",
 "served": "http://127.0.0.1:<non-live port>/", "text_only": false,
 "pages": [{"page": "Review", "desktop": "<png>", "phone": "<png>",
            "answers": [{"id": "<ccn answer id or ledger m-id>", "shows": "what the tab shows for it"}]}]}
```

Before writing it, the lane serves that commit from any worktree on a port other than the live one. It screenshots each page the diff touches at desktop and phone widths and looks at every shot. For each page or tab it names every owner answer and dashboard ruling governing that tab's contents, from `ccn answer search` and the ledger, with what the tab shows for it. A text-only change sets `text_only` and needs one screenshot of its page. The verb checks only that the proof names the tree going live, an off-live port, existing screenshots, and per page at least one answer that says what the page shows. It cannot tell whether the page is right; the lane's own look does that. Moving the live checkout and restarting run on the Mac.

The app serves Overview, Needs you, Review, Releases, Workers, Timeline, and Ask from the cci daemon's record stream, `cc-present watch` on the board, the ledger, the drive's cc-notes answers, and the newest progress doc carrying `## Ordered plan of action`.

The assistant runs in the browser on the design-doc client (`_shared/html/ai-core.js`), calling `POST /ai/chat/completions` on the server. The server adds the Cerebras key, fixes the endpoint and the model to `gpt-oss-120b`, and streams the answer back, so the page never holds the key. The server reads the key once at start from the Keychain item `cerebras-api-key` (`security add-generic-password -U -s cerebras-api-key -a "$USER" -w` stores it), and the plist carries no key. Without the item, or on any device but the owner's, every assistant control stays hidden.

Only the owner can act. Opening a review is `POST /api/review/<n>`, never a `GET`, so a link or image on another site cannot start one. A verdict, a review and a model call each first check that the `Host` header is one of this Mac's names, which are the configured host, its tailnet name, the short name and its tailnet IPs from `tailscale status` at start. That refuses a DNS-rebinding page before anything runs. The server then asks `tailscale whois` for the caller's address and accepts only an untagged device signed in as `tailnet_owner`; anyone else gets a 403. Reads stay open.

The review assistant runs `claude -p` with `--restricted`, so the Mac's Claude settings and the plugin hooks they enable do not apply. It runs in `dontAsk` with `--tools Agent,Bash,Read,Grep,Glob,ToolSearch`, `--plugin-dir` for the installed cc-review, `--add-dir ~/.cc-review`, and an allowed list of the cc-review tools, `Agent`, reads of its own review, and the `echo`, `rulings.py match`, `cci grep` and `cci post` commands its prompt names. The server reads the PR's board row and puts it in the prompt, so the assistant needs no `curl`. Command-K (or `/`) jumps to any screen, pull request, stack, plan step, worker, release section, owner answer, or record, and Tab sends the query to the assistant.

Needs you sorts the owner's taps into safe-to-approve-together and read-first, flags a tap that conflicts with a quoted owner answer, and approves or holds in place. The Overview opens with what changed since the owner last looked, the Timeline opens with the day in five sentences, and every pull request and stack offers a plain-words summary.

## Owner bar and user testing

Builders of the app, the progress desk that feeds it, and the user-test lane that grades it all work to this document. Every revision of the app is graded against the owner's bar. A five-second glance tells him how far along the drive is, with nothing to read.

The first screen shows the plan's steps as a visual, interactive table with each step's state, what blocks it, what happens next, and its landed, queued, and waiting-on-his-tap counts. Beside the table, the end state sits as a picture with its pieces colored done, doing, or not started. That screen also shows one live box per worker saying what it is doing now, with the exact current plan one click away full-screen.

Needs you lists every tap and card he owes. Approve and Hold act in place, posting the board's own decision event, and never open the board. The PR review screen embeds cc-review for each PR and each stack and cc-present's row, never a link out. Text comes on demand through Command-K, which jumps anywhere, while the client-side Cerebras assistant says what changed since he looked, what needs him, and what any PR or stack does.

Phone and desktop both work, and the page moves live. Every local state, including the board store's taps and questions, cci heads, gos, landings, lane state, and the ledger, reaches the page within seconds over server-sent events with no API call. GitHub is read only for CI and review state, cached, on a floor.

Every revision of a surface the owner uses, whether this app, a board layout, or any other page, goes through a `user-test` lane briefed from [user-test-brief.md](user-test-brief.md) before he sees it.

## One model, one stream

The Overview reads `GET /api/model` and nothing else, and re-renders on every `/api/stream` push. Every number on it comes from one model and one counting function, so no two numbers on the page can disagree. Everything local, including PR states, board verdicts, workers, and steps, streams with no API key; only short names and Command-K answers call Cerebras.

| Field | Shape | Source |
|---|---|---|
| `drive.prs[]` | `{number, state, tree, part, step, worker}` | ledger rows, board verdicts, `go` records, `steps.json`, the config's parts |
| `steps[]` | `{id, label, title, state, blocker, next, prs[{number, state}], tone, workers[]}` | the progress desk's `steps.json` |
| `workers[]` | `{lane, step, prs[], at, boxes[12]}` | cci lanes and records, coordinators left out |
| `needs[]` | ordered `{kind: prs\|card\|ask, ...}` | the cc-present store and the story's asks |
| `parts[]` | `{key, name, home, becomes, scopes[]}` | `dashboard.json` |
| `now` | `{release, releases[], blocker\|null}` | `steps.json` |

The client has one counting function, `tally(list)`, returning the seven state counts and the total for any list of `{state}`. The progress ring is `tally(drive.prs)`, a part's percent is `tally` over its PRs, and a step's fill is `tally(steps[i].prs)`. The server sends no precomputed counts. The denominator is every ledger PR open or landed, including one registered minutes ago with no state yet.

## Seven states, one each

The server gives every PR exactly one state, first match wins; the client never derives one.

| State | Means |
|---|---|
| `landed` | merged |
| `you` | waiting on his tap at its current head |
| `failing` | tests failing at its head |
| `held` | a hold is set |
| `queued` | approved at its current or a carried head, waiting to land |
| `doing` | a worker wrote about it in the last 20 minutes, or checks are running |
| `waiting` | open, unapproved, nothing running |

A PR is `you` when the board holds no approval at its current or carried head and no coordinator `go` names that head; either one makes it `queued`. Verdicts are read live from the cc-present store on every rebuild, and Approve or Note on the dashboard writes the board's own `triage.decided` event, so no board page opens.

A step's tone takes the first match over its PRs, reading done when all landed, then any `you`, any `failing`, any `held`, moving for any `doing` or a live worker, queued when every PR is landed or queued, and otherwise waiting.

A worker's five-minute box takes the most severe record kind it posted in that window.

| Box color | Record kinds |
|---|---|
| red | `blocker`, `defect`, `incident` |
| amber | `ask`, `owner` |
| green | `landed`, `go`, `fix-live` |
| blue | any other record kind |
| empty | silent |

## What the drive supplies

`dashboard.json` carries the drive's `parts`, where each part has a key, a one-word name, a home in the cloud, the Mac, or tools, the one line it becomes when done, and the PR title scopes that belong to it. A title's scope is the text before its first colon. A PR in the old tree sits in its home's "old, kept" band instead of a part.

`lr update --render --publish` (or `--sent`) rewrites `<state dir>/progress-desk/steps.json` atomically from the same rows as the owner update, with no counts, so the dashboard and the update never disagree; [owner-report-table.md](owner-report-table.md) lists its row fields. That file lists `steps[]` in plan order, each with an `id`, a label of at most two words, a plain title with no PR numbers, its `state`, and `blocker`, which is `null` when nothing blocks it. Each step entry also includes `next`, every open or landed PR of the step, and the lanes working on it. Beside the steps, `now.release` and `now.releases[]` describe the next release and every release with the parts it ships, in plain words. The `now.blocker` field holds a drive-wide stop only, such as a red trunk or a dead merge queue, with its start time, or `null` otherwise. A step's own blocker stays on the step.

`drive.py overview` (alias `plan-status`) prints the owner's plan report from `steps.json` in about a tenth of a second. The report follows [owner-report-table.md](owner-report-table.md), one row per step and per milestone. Below the rows, it lists everything left until completion with the gate each item waits on. A step's state counts its changes from `pr_state`; change numbers fold into "a change," lane names into "a worker," and drive words into their plain names. `--json` serves tools, `--step <id>` prints one row by id, label, title, or milestone number, and `--since <90m|7:05 PM|ISO>` keeps the rows that moved and says what moved. The coordinator answers "where does the plan stand" with `drive.py overview`, never by parsing `/api/model` by hand.

A row or record the owner reads says he approved or tapped a PR only when the coordinator's board holds his approval at that head (`cc-present outcomes --no-doc`), and gives its time. A coordinator `go` alone reads "cleared to land by the coordinator at <time>," never "you approved it."

## Words at rest

Lane names never appear on the Overview, and numbers, percentages, times, PR numbers, release ids, hashes, and glyphs do not count toward its cap. The Overview at rest shows at most 40 words. A card opened by hover or tap holds at most 25 words; the plan sheet is structured, not capped. Everything else moves to a drill-in such as a step card, the PR page with its embedded cc-review, the plan sheet, or Command-K.

## Older `/live-dashboard` layout

Drives that do not run the app use the `/live-dashboard` layout, which answers what waits on me, what is broken, what is live, and what is in flight, in that order, followed by Review. The `drive` preset lays the page out that way:
- First section: pins `lr.needs-owner`, showing owner tasks, cci asks and decides addressed `--to owner`, and boards that are still asking.
- Broken: shows `release.incidents` and `open-records`.
- In flight: opens with `lr.plan-progress` and the Plan table, then `cci-lanes`, `orca`, `tasks`, and `asks` for the owner's own asks.
- Plumbing: collapsed.

The drive's pack adds `lr.needs-owner`, `lr.plan-progress`, `lr.inbox-feed`, `lr.drive`, `lr.compactions`, and `lr.watches`. The owner's actions on `lr.needs-owner` and `pr-review-queue` arrive at main as `owner` records; on a `Reviewed #N` record, main sets `owner_reviewed_at` on that PR's ledger row.

Before the first milestone report, the coordinator adds the drive's own sources, such as the `release.*` cards for a release drive, per `/live-dashboard` `reference/picking.md`. The coordinator revisits the layout at each milestone and runs `live-dashboard check` after every edit.

Every source card drops settled items itself per `/live-dashboard` `reference/keeping-live.md`. When a row stays on the page after its work closed, post a closing record with `--resolves` or `--re`. A stale row is a source defect for a cc-skills lane, never a hand edit to `layout.yaml`. Keep status written into `banner`, a manual card where a source exists, and internal ids where a name exists off the page.

## Plan progress (`lr.plan-progress`)

`lr.plan-progress` shows what is done and what is left, read from cc-notes alone. A plan counts when it carries `program:<slug>`; a task counts when it carries that label or hangs off such a plan. The plan's step numbers are the ones its `### <n>.` headings carry, and the card draws each plan's `### <n>. <title>` headings as steps. A task joins step `<n>` through a `step:<n>` label. Without one it joins its plan's "No step label" row, and with no plan it lands under "Outside any plan."

Each step shows a bar of done, in progress, blocked, and open tasks, and opens onto its tasks with status, assignee, age, and what each waits on. A task is blocked while a `ccn task dep` blocker is unfinished or a criterion has failed; otherwise, it waits on its first unmet criterion. The progress record's `## Open tasks` ends with the same counts, one line per plan. A plan with no task registered appears in the card's note, never as an empty plan.

Register a unit when the lane starts:

```
ccn task add "<unit>" --plan <plan id> --label step:<n> --label program:<slug> \
  --criterion "<observable done-when>"
```

Keep its state current:
- `ccn task start <id>` when work begins.
- `ccn task dep <id> <blocker>` when it waits on another task.
- `ccn task criterion met|failed <id> <criterion>` as each check settles.
- `ccn task done <id>` when the PR lands, or `ccn task cancel <id>` when it is dropped.

## Plan table

The Plan table panel is a `view` card over `plan-table.md` in the dashboard dir. It shows the owner report table with each row's PR states refreshed and the watchdog's newest retro line appended. `scripts/plan-table.py --doc <table doc id> --retro-log <watchdog log id> --repo <checkout> --out <dashboard dir>/plan-table.md` regenerates the file from the owner table doc, refreshing each row's PR states in one batched `ccx vcs pr status` call. Until the first run the panel says nothing matches yet. A drive seeded before the preset carried the panel adds this card to its In flight section:

```
- {use: view, id: plan-table, title: Plan table, width: 3, with: {file: plan-table.md, table: Plan table, columns: [step, what_it_delivers, state, what_blocks_it, what_we_are_doing_to_unblock_it, what_went_wrong_or_slowed_it_so_far]}}
```
