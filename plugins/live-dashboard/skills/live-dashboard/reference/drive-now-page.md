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
   place of the list.
5. The drive's own diagram.

## The keys a drive sets

| Key | What it changes |
|---|---|
| `milestones` | The named stops in the progress view, in order. Name each by product and outcome, never by a release code. `release` ties a stop to an entry of `now.releases` in the progress desk's `steps.json`, so the model reads that release's state and blocker. |
| `milestones_label` | The line above the stops, such as Milestones to everyone on Iris. |
| `views` | Which version of each piece shows by default. Any of them can be flipped for one visit with a URL parameter, such as `?blockers=cards`. |
| `brief_dir` | Where the curated brief and its last 50 versions are written. |
| `stories_dir` | Where each piece of work's story lines are saved, so reopening a story costs nothing. |
| `transcripts` | The root session's `subagents` directory. The story for a piece of work reads the newest transcript named after its worker. |
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

The curator in `dashboard/brief.py` builds a digest of the drive's facts, about 30
thousand characters, and asks the curator model for one brief that matches a JSON schema.
The digest holds:

- each milestone and its release entry from `steps.json`;
- the plan steps with their state, blocker, and next step;
- the counts of tracked changes by state, for the code line;
- the owner's open taps and questions;
- each active worker with its last three records;
- blocker, incident, ask, hold, and decide records from the last three hours, which is
  where the blocker chart comes from;
- the owner's words from the last two hours;
- the previous brief, so a sentence whose facts did not change stays word for word.

To give the blocker chart another source, add it to `digest()` with a key that says what
it is. The brief names what blocks which milestone, and the page draws it.

The curator runs when a record that can change an answer arrives, when `steps.json`
changes, or when the board changes. One run is in flight at a time, at most one starts
every two minutes, and no timer runs it.

## The plain-words gate

`dashboard/plainwords.py` is the owner's rule as code. A string fails on any of these:

- a release or plan code, a record or pull request number, or a hash;
- code, a path, or a UTC time;
- a worker's name, one of the drive's coined words, or a git verb such as rebase;
- more words than its slot allows.

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
