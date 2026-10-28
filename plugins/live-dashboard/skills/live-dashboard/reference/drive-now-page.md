# Fit the Now page to a drive

The drive dashboard's main page answers four questions at a glance: how far along the
drive is, what is being worked on, what the biggest blockers are, and how each one is
being unblocked. Every sentence on it is written for the owner by a model from the
drive's records and checked by a plain-words gate before it shows. The app lives in the
long-running plugin's `dashboard/` directory. This reference covers what a drive sets to make
the page its own.

Start from `templates/drive-now.json`. Copy it to the drive's state directory as
`dashboard.json`, fill in the drive's values, and start the app:

```bash
python3 plugins/long-running/dashboard/app/server.py --config <state dir>/dashboard.json
```

## What is on the page

From top to bottom:

1. A band, only when needed. A red band for a customer-facing incident with no fix live.
   An amber band with the buttons for each tap or question that waits on the owner.
2. The headline: one sentence of at most 25 words that a person could read aloud at
   standup.
3. Overall progress: the drive's milestones, each with its state.
4. Two columns. Blockers come first, drawn as a dependency chart with arrows into each
   milestone they hold up. Work comes second, as three rows that each open a live story in
   place of the list. Every open blocker and work row says how long it has waited and since
   when.
5. The drive's own diagram.

## The keys a drive sets

| Key | What it changes |
|---|---|
| `milestones` | The named stops in the progress view, in order. Name each by product and outcome, never by a release code. `release` ties a stop to an entry of `now.releases` in the progress desk's `steps.json`, so the model reads that release's state and blocker. |
| `milestones_label` | The line above the stops, such as Milestones to everyone on Iris. |
| `views` | Which version of each piece shows by default. Any of them can be flipped for one visit with a URL parameter, such as `?blockers=cards`. |
| `status_now` | A JSON file `{at, paragraphs: [{label, text}]}` the progress desk rewrites, such as one paragraph for the main work and one for a side project. When it exists, its paragraphs replace the curator's headline, so the owner's standing status question is answered where he looks. |
| `brief_dir` | Where the curated brief and its last 50 versions are written. |
| `stories_dir` | Where each piece of work's story lines are saved, so reopening a story costs nothing. |
| `transcripts` | The root session's `subagents` directory. The story for a piece of work reads the newest transcript named after its worker. |
| `defects_to` | The lanes a build defect goes to, such as a root go with no approval on the board. Only the live copy sets it; a dev or test copy leaves it out and posts nothing. |
| `stack` | The stall watch's open-PR ledger, `<drive dir>/inbox/stack.json`. The Work page lists its rows, the ones stuck two hours first, and each PR page shows its landing stage, how long it has sat there, and the next step with its owner. |
| `mirror` | The live copy's base URL, such as `http://<host>:61210`. A dev or test copy sets it and then runs no model of its own: it reads the live copy's curated pages every 30 seconds and relays its live stories. Only the live copy leaves it out. |
| `curator_model`, `story_model` | Claude Opus 5.5 writes the page; Claude Sonnet 5.5 writes the stories. Change these only when the owner names another model. |
| `parts`, `trees` | Feed the end-state diagram: which parts the drive builds and where each lives. |

| View | Values | Default |
|---|---|---|
| `progress` | `journey` names every milestone; `compact` shows a bar of segments, the live count, the next milestone, and anything stuck | `journey` |
| `blockers` | `chart` draws each path to a milestone with arrows; `cards` shows at most three ranked cards | `chart` |
| `story` | `column` opens a story in the work column; `page` gives it the whole page under a one-line summary | `column` |
| `work` | `rows3` or `rows5` show that many rows with the latest step; `boxes` shows every piece of work as a small box | `rows3` |
| `diagram` | `end-state` shows how much of the end state has landed; `parts` shows one bar per part; `none` leaves the slot empty | `end-state` |

## Add the drive's own diagram

The bottom of the page is a slot. `DIAGRAMS` in `dashboard/app/now.js` maps a name to a
function that returns the slot's HTML from `state.model`. To give a drive its own
picture, add an entry and set `views.diagram` to its name:

```js
const DIAGRAMS = {
  'end-state': () => /* the cloud and Mac picture */,
  'test-matrix': () => `<h2 class="nw-h">Scenarios passing on the test Macs</h2>${matrix(state.model)}`,
  none: () => '',
};
```

Give the slot a heading that says what the picture measures. When the picture needs data
the model does not carry yet, add it to `Drive.live` in `server.py` from a local source;
never poll GitHub for it.

## Where the words come from

The curator in `dashboard/app/brief.py` builds a digest of the drive's facts, about 30
thousand characters, and asks the curator model for one brief that matches a JSON schema.
The digest holds:

- each milestone and its release entry from `steps.json`;
- the plan steps with their state, blocker, and next step;
- the counts of tracked changes by state, for the code line;
- each open change with its ledger owner, the coordinator's last three rulings about it,
  the two newest worker records about it, and its fixers, which are the active workers those
  rulings name, else the owner or the owner's numbered successor;
- the status paragraphs the progress desk writes, so the page uses their names and never
  contradicts them;
- the owner's open taps and questions;
- each active worker with its last three records;
- blocker, incident, ask, hold, and decide records from the last three hours that no later
  record resolved, which is where the blocker chart comes from;
- the owner's words from the last two hours;
- the previous brief, so a sentence whose facts did not change stays word for word.

To give the blocker chart another source, add it to `digest()` with a key that says what
it is. The brief names what blocks which milestone, and the page draws it.

The curator runs when a record that can change an answer arrives, when `steps.json`
changes, or when the board changes. One run is in flight at a time, at most one starts
every two minutes, and no timer runs it.

## The plain-words gate

`dashboard/app/plainwords.py` is the owner's rule as code. A string fails on any of these:

- a release or plan code, a record or pull request number, or a hash;
- code, a path, or a UTC time;
- a worker's name, one of the drive's coined words, or a git verb such as rebase;
- more words than its slot allows;
- in a blocker's fix, a line that only says someone answers the review or fixes the failing
  tests without saying what the finding is.

The curator also sends a brief back in three more cases. A blocker on a change with fixers
reads No one assigned. An open blocker or work row has no start time. Something waits on the
owner while his board asks nothing. Whatever still breaks those rules after one retry
is corrected in code: the blocker becomes being fixed, and the false waits-on-you rows drop
out.

A failing brief goes back to the model once with every
violation listed. Whatever still fails keeps its last passing text, so a raw string never
reaches the page.

When the owner flags a new coined word, add it to `COINED` in `plainwords.py` and add the
string he flagged to `tests/test_plainwords.py`.

## Live stories

Clicking a piece of work opens its story at `#/story/<item>`. The server starts one
story writer per piece of work on the first view:

1. It finds the newest transcript whose name carries one of the work's workers, so a
   worker's successor continues the same story.
2. It sends the last 30 minutes of that transcript and asks for three to five lines.
3. It watches the file for writes and sends new events when the worker says something,
   a tool fails, or it pushes, opens a pull request, or posts a record.
4. Each line passes the plain-words gate before it is saved and shown.

A writer stays three minutes after its last viewer leaves, and at most four run at once.
The app cannot be restarted while a writer runs, so close story tabs before a deploy.

## The other pages

The app has five tabs, Now, Plan, Work, Review, and History, and a Jump to anything button; each is a page with its own URL, and nothing in the nav bar opens a modal. Jump lives at `#/jump`, and Command-K opens it from any page. The Review count covers only what his board asks. Every named thing links to its own page. A milestone opens `#/plan/m<i>` and a plan part `#/plan/<part id>`. A worker opens `#/worker/<lane>`, which lists its pull requests, its last word, and what it waits on. A change opens `#/review/<number>` and an open board question `#/review/q/<card id>`.

Plan, Work, History, and Review each have their own curator using the same machinery as the Now page. Each curator makes one model call per page when a record that can change that page arrives. At most one run happens per page at a time, never on a timer, and each page enforces its own minimum gap between runs. Every output passes the plain-words gate. A failing string goes back to the model once and otherwise keeps its last passing text.

| Page | What it shows | Runs on | Minimum gap |
| --- | --- | --- | --- |
| Plan | One row per milestone with its state and what it waits on, where a milestone not started says only that it follows the one above; each row opens the milestone's own page with what it ships, what went wrong, and the board question it waits on. One row per open plan part with state, what blocks it, and who acts next; each opens the part's own page with what it delivers, its open changes, and what landed. Parts not started and parts done each fold into one list. A milestone or part says it waits on the owner only when an open board question asks it, and links to that question. | `blocker`, `ask`, `answer`, `decide`, `go`, `landed`, `release`, `incident`, `hold`, `lift`, and `defect` records, plus `steps.json` writes | 3 minutes |
| Work | Every piece of work today. The three most important running items show at rest and the rest fold under a count. Finished items and the helpers that keep the drive running sit behind folds. A row opens its live story in place. Under it, every open pull request from the stack ledger shows its state, next step, owner, and how long it has sat there; the ones stuck two hours show at rest and the rest fold. Work still waiting on a release, an approval, tests or another change stays running. | `state`, `head`, `report`, `opened`, `blocker`, `landed`, `review`, `done`, `stopped`, `stand-down`, and `handoff` records | 3 minutes |
| Review | Every merge row waiting on the owner as four plain lines, for users, plan, risk, and review naming codex or the reviewer bot, with what it waits on above them when that is more than his approval. Groups get a plain title and one line on how their changes relate. Each open board question shows as one plain question, what the recommended answer changes, and the recommended answer's button, with the other answers one click away; its background stays on the board, linked to that question. A question leaves the page as soon as an owner answer recorded in cc-notes names its card id. The tab count, the band on Now, and the Review page read one list. A pull request opens on what it does. That tab holds its four plain lines, the plain-words section of the reviewer bot's comment, or the bot's summary when it wrote none, and the pull request's own description; the code and its review session sit on the second tab. Open on GitHub and Open in Graphite sit beside its title. Its Tests and Blocks landing facts, and what happens next, come from the Now page's newest curated blocker for that change when there is one, and the reviewer bot's latest verdict sits above its summary. Its code review session starts only when the owner presses Start the code review. | `review`, `head`, `go`, and `report` records, plus board changes | 1 minute |
| History | The day as a short timeline of the owner's decisions, releases, incidents, and what landed grouped by feature, each one line with its detail on click, under the time the page covers. At rest it shows the six newest decisions, releases, incidents, fixes and changes that got stuck or moved again; older events, landings and approvals fold under a count. No heartbeats or receipts. Each filter shows its six newest events and folds the rest. | `owner`, `release`, `incident`, `mechanism`, `fix-live`, `hold`, `lift`, `blocker`, `go`, and `landed` records | 5 minutes |

Review rows are the merge board's own text. The Review curator rewrites only the failing fields of a row that fails the plain-words gate, plus every group title and every open question. Each row, group, and question carries a key hashed from its source text, so a rewrite stays until the board changes that text, and a fallback never borrows another entry's words.

Every blocked or waiting row on Plan, Work and Review says how long it has waited and since when, such as "for 40 min, since 5:48 PM", counted from the record that started the wait. Every page reads each change's state from the same list the Now page uses, `open_changes` in `dashboard/app/brief.py`, so no two pages disagree about a change. Work and History read their records straight from the bus for their own window, not from the app's recent-records buffer. Work reads the last 12 hours. History reads since midnight Pacific, covering at least eight hours.

Each page's output is saved beside the brief as `plan.json`, `work.json`, `history.json`, and `review.json`, with the last 50 versions under `history/<page>/`.

## Add a page

Define a `Page` in `dashboard/app/pages.py`. It holds a name, a prompt built from the shared `RULES`, and a JSON schema. Its `digest(model, config, previous, now)` returns the facts, or `None` to skip the run. Its `slot_of(path)` maps each rendered field to a plain-words word limit. It also names the record kinds that wake it and its minimum gap.

Add the page to `PAGES`. The server runs its curator and serves its output as `model.pages.<name>`. Render it from `dashboard/app/pages.js` and add its tab to the `tabs` list in `render()` in `app.js`. When a digest needs older records than the app holds, call `model["fetch"](kinds, since)`.
