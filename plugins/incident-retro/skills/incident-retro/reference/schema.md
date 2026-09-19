# The retro.json contract

`retro.json` is the contract shared by the evidence snapshots and
`incident-retro.html`. The `retro.py` driver reads the same contract through
`check`, `prose`, `text`, `snapshot`, `links`, `render-check`, and `pdf`.
Markdown-bearing string fields accept `[text](url)` links and `` `code` ``.
They also accept `**bold**` and `*italic*`. The same mini dialect as the
design-doc renderer handles `[^n]` footnote tokens. Run
`retro.py scaffold <dir> --example` for the filled Acme instance.

## Timestamps

Every timestamp in the file is ISO 8601 with a UTC offset: `2026-08-14T17:42:00-07:00`. A bare `Z` is accepted as `+00:00`. `check` errors on a naive timestamp. `meta.timezone` is the zone the page and `text` display times in; it never changes what a timestamp means.

The Internet Assigned Numbers Authority maintains the timezone database used
here.

## `meta`: identity

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | Astra-written headline used for the page's `h1`, rail brand, and browser title. Names the failure within `DOC_TITLE_WORDS = 8` words and `DOC_TITLE_CHARS = 60` characters; either excess is an error. No final period. Colons and identifiers draw strict warnings. [reference/writing.md](writing.md) gives the rule and examples |
| `subtitle` | yes | Astra-written causal sentence beneath the headline, within `SUBTITLE_WORDS = 20` words and `SUBTITLE_CHARS = 120` characters. Excess characters are an error; excess words, colons, and identifiers draw strict warnings |
| `tags` | yes | Operator-chosen data from a controlled vocabulary for filtering, outside the prose field list. Use 2 to 6 distinct topical tags matching `[a-z0-9]+(?:-[a-z0-9]+)*`, such as `migration`, `release-pipeline`, `paging`. Name the system, failure class, and surface. Repeating a team codename warns |
| `slug` | yes | the URL and the download filename `<slug>-incident-retro.md`. `<incident date>-<three to six plain words>`, at most 60 characters. Names the incident independently of `title` to keep long titles out of URLs. `scaffold` builds one from the title's content words; `--slug` overrides it |
| `date` | yes | date of the writeup, `YYYY-MM-DD` |
| `status` | yes | `draft`, `in-review`, `reviewed`, `resolved`; rendered Draft, Under review, Reviewed, Closed out. `draft` is the only status that may leave `timestamps.onset` or `resolved` null; `reviewed` and `resolved` expect at least one cause with kind `root`; `resolved` expects every action `done` or `dropped` |
| `incident` | no | `{number?, severity?, severityLink?}`; `number` a positive integer, `severity` matches `sev-N`, `severityLink` an https URL |
| `authors`, `attendees` | no | lists of people's names; never an email or `mailto:` (`check` errors on `@`). A retro past `draft` names its authors |
| `commander` | no | the incident commander's name |
| `teams` | no | team codenames the retro concerns, as strings |
| `repo`, `ref` | no | as design-doc: `owner/repo` and a Git ref; the page renders a link into `repo` as `#1234` and others as `owner/repo#1234` |
| `timezone` | no | an IANA zone name, the display zone (default `UTC`) |
| `subIncidents` | no | `[{id, t, h}]` with ids `I\d+`, for a retro that covers several incidents; windows and causes may carry `incident: "I1"` |
| `homeLink` | no | `{href, label}`, a back link the rail renders above the brand |
| `sections` | no | `{<sectionId>: {sub?, takeaway?}}`. `sub` is one line of context under the header; a narrative section's `takeaway` states its conclusion in 18 words or fewer (`TAKEAWAY_WORDS = 18`). Reference sections carry no takeaway. Ids are `overview`, `timeline`, `causes`, `impact`, `resolution`, `lessons`, `recognize`, `actions`, `evidence`, `unknowns`, `glossary`, `notes`, in that reading order |
| `ai` | no | `{suggest?: {<sectionId>: ["…"]}}` the questions the assistant offers while a section is on screen; the endpoint and keys live in `ai.json`, never here |
| `acronyms` | no | words the capitalization lint holds to their own spelling, on top of the built-in list plus `TTD`, `TTE`, `TTM`, `TTR`, `SEV` |
| `draft` | no | boolean; pins a draft banner as in design-doc |
| `canonical` | no | a sentence stating what each file contains |
| `rev`, `revisions` | no | written by `retro.py snapshot`, never by hand; `revisions[].files.evidence` is the snapshot's digest of all `evidence/` files |

`REFERENCE_SECTIONS = ("evidence", "glossary", "notes")` open on their heading
and collapsed structure. Conclusions belong in the narrative sections that
argue them. A reference takeaway or a narrative takeaway over 18 words draws
a strict warning.

For a retro written before 0.3.0, move the old `meta.title` into
`meta.subtitle`, clear `meta.title`, and run
`retro.py prose <dir> --quick` to write the newly required prose through
Astra. Replace the old subtitle value, including
"Incident retrospective"; it no longer supplies a browser-title suffix.
Add topical tags separately from the codenames in `meta.teams`.

`scaffold --subtitle "…" --tags "migration,release-pipeline" fills these
fields. Without those flags, the subtitle copies the title and tags are
empty. `import-gdoc` puts the source document's heading into `subtitle`,
leaves `title` and `tags` empty, and records that work in the import notes.

The page shows tag chips below the subtitle and sets the space-separated
tags on `document.documentElement.dataset.tags`. The design-docs retro index
is hand-maintained HTML whose cards fetch each retro's `retro.json`. The
card convention is to read `meta.title`, `meta.subtitle`, and `meta.tags`;
the retro page exposes `data-tags` for filtering. Update older card readers
that still take their description from `summary.p` to follow that convention.

The page reads four `localStorage` keys: `design-doc-ai`, `design-doc-github`, `design-doc-theme`, and `design-doc-wording`. They keep their design-doc names so one browser override works on both kinds of page.

## `summary.html`: the executive summary

`summary.html` sits beside `retro.json` and is the only part of the retro
written as markup. It is a body-level fragment of
`<section class="xs-panel" data-kind="…">` blocks and shares the design-doc
skill's summary contract. The page places it above the Overview after
stripping event handlers and any URL that is not HTTP or HTTPS.

One panel answers each question, in this order: `what-happened`, `impact`,
`why`, `what-changed`, `still-open`. Each opens with an `<h2>` or `<h3>`
stating the answer. Each panel, including its heading, has 35 words or fewer
(`SUMMARY_PANEL_WORDS = 35`); the whole summary has 150 or fewer
(`SUMMARY_BUDGET = 150`). Cite registers as the rest of the retro
does, so `(C1)` renders as its handle. A `.xs-stats` block of `.xs-stat` tiles
shows the numbers readers need before reaching the tiles below.

`check` errors on a page tag, an embed, an inline handler, a foreign URL
scheme, or an unknown or repeated `data-kind`. It warns on a missing panel, a
panel out of order, a missing heading, a word count over budget, a citation
no register defines, and a leftover TODO. A reviewed retro answers every
question. The first panel's heading also draws a strict warning when at least
60% of its distinct non-stopwords occur in `meta.title`; headings under four
words draw the short-heading warning instead. `retro.py text` prints the
summary first, and print renders every panel expanded.

## `summary`, `impact`, `resolution`, `detection`: twinned prose

Each block contains `text` and `p` plus section-specific fields. `text` is the wording the team stands behind. `p` is the plain twin a reader outside the team sees first: 30 words or fewer, or a third of the original, excluding ids and file paths. `check --strict` errors on a missing twin or one that breaks those rules. It also errors when `text` changes after a snapshot but `p` does not. Every cause carries the same pair.

- `impact.teams`: `[{codename, text}]`, one row per team and what it saw.
- `impact.metrics`: `[{label, value, unit?, delta?, measured, cites?}]`. `value` is a display string (`"3 180"`); `measured` is a boolean, so every number is measured or tagged estimated; `cites` names windows or causes.
- `resolution.links`: links as below, `closes` refused.
- `detection.monitors`: `[{id, file?, role, fired?, recovered?}]` with `role` in `caught`, `missed`, `added`. `fired` and `recovered` are timestamps; the page derives "fired N after onset" from `fired` and `timestamps.onset`.
- `impact`, `resolution` and `detection` may carry `component`, the id of a declared component the page renders under that section's text.

## `timestamps`: the numbers the tiles derive

```json
"timestamps": {"onset": "…", "detected": "…", "engaged": "…", "mitigated": "…", "resolved": "…", "allClear": "…"}
```

Every key is present; a value is a timestamp or `null`. `check` enforces `onset ≤ detected ≤ engaged ≤ mitigated ≤ resolved ≤ allClear` over the values present, and that `onset` and `resolved` are set unless `status` is `draft`.

The file never stores derived values. The time-to-detect tile uses
`TTD = detected − onset`, while the time-to-engage tile uses
`TTE = engaged − detected`. The time-to-mitigate tile uses
`TTM = mitigated − onset`, while the time-to-resolve tile uses
`TTR = resolved − onset`.

A window's duration is `end − start`, and a monitor's latency is
`fired − onset`. Action progress is done over total. The `derived_numbers`
helper in `retro.py` computes the four tiles for `text` and `check`. The page
computes the same values from the same keys. `check` warns when prose states
minutes or hours `after`, `to`, or `before` near detect, mitigate, resolve,
engage, onset, or fired. The `TTD`, `TTE`, `TTM`, and `TTR` tiles stay the only
source.

## `windows`: outage windows

`[{id, h, kind, start, end, text?, teams?, incident?}]`. Ids `W\d+`; `h` is the handle; `kind` is `outage`, `degraded` or `partial`; `end` is after `start`. Two windows that overlap on the same team draw a warning.

## `timeline`: what happened, in order

The shape is `[{id?, ts, kind, h, text, actor?, window?, phase?, key?, refs?}]`, sorted by `ts`. `check` errors on an entry out of order.

- `h`: the short name shown in each closed row, beside its time, kind, and actor. Expanding the row reveals `text` and refs.
- `text`: 25 words or fewer. More than 25 draws a strict warning; more than 40 is an error even without `--strict`. URLs and inline code are excluded from this count.
- `kind`: `deploy`, `alert`, `report`, `hypothesis`, `action`, `mitigation`, `resolution`, `allclear`.
- `key`: `true` on an entry needed to explain the incident. The page opens on the key moments. The full list is collapsed, searchable, and filterable by kind, phase, and actor. `check` warns when no entry is marked as key or more than eight are.
- `id` is optional and `T\d+`: an entry gets one only when a cause's `evidence` or prose cites it. `check` warns on an id nothing cites and errors on a cited `T#` no entry carries. A `(T7)` citation uses `h` on the page; `retro.py text` still substitutes the entry's local time.
- `phase`: `before` or `after`, required on an entry whose `ts` falls outside `[onset, allClear or resolved]`; an entry inside the incident carries none.
- `window`: a window id the entry belongs to.
- `refs`: links as below, either strings or `{url, kind?, label?}` objects, with `closes` refused. A Slack ref must be a message permalink, `https://<workspace>.slack.com/archives/C…/p…`; one with no snapshot under `evidence/slack/` draws a warning because the page cannot quote it. A `deploy` entry with no pull request or build ref draws a warning.

## `causes`

Each cause entry follows `[{id, kind, t, h, text, p, evidence?, code?, links?, incident?, component?}]` and the rules below.

- Ids `C\d+`; `kind` is `root`, `contributing` or `trigger`; `t` is a noun phrase of twelve words or fewer; `h` names the closed row and the causal-chain node. Expanding the row shows `t` and the selected wording from `text` and `p`. `text` is at most 90 words excluding URLs and inline code; excess draws a strict warning. The plain twin, or the first sentence of `text` when no twin exists, is at most 25 words.
- `evidence`: a list of https URLs and citeable ids such as a `T#`, `W#`, or another `C#`; every id must resolve.
- `code`: `{lang, source, caption?}`, a snippet the page shows behind a toggle.
- `links`: links as below, with `closes` refused. A change closes an action, never a cause.
- `component`: the id of a declared component the page renders inside the cause's card, typically an `ir.notebook` with a `cells` subset.

## `actions`

Each action entry follows `[{id, t, h, owner, source, state, links?, due?, note?}]` and the rules below.

- Ids `AI\d+`; `t` is one line of sixteen words or fewer; `h` names the table row. The Full wording toggle reveals `t` and `note`.
- `owner`: a person's or team's name. `check --strict` errors on a missing owner.
- `source`: the cause id the action answers, or `lessons` or `review`.
- `state`: `todo`, `in-progress`, `done`, `dropped`; rendered To do, In progress, Done, Dropped.
- `links`: links as below; `closes: true` marks the pull request or issue whose landing completes the action. `retro.py links --fetch` reports an action `done` whose closing change is still open, and one `todo` or `in-progress` whose closing change merged.
- `due`: `YYYY-MM-DD`. `note`: one sentence, for a dropped action the reason.

## `decisions`: choices made during the response

Each entry follows `[{id, t, h, who, when, why, alternatives?, refs?, links?}]`.
Ids are `D\d+`. `t` is the decision as one line of sixteen words or fewer.
`who` names the person or team who made it, and `when` records its timestamp.
`why` holds the reasoning recorded at the time, at most 60 words excluding
URLs and inline code; excess draws a strict warning. `alternatives` names
the options not taken.

Each decision appears as a collapsed row under
Detection and response. The closed row shows `h` and its time;
expanding it shows the decision, who made it, and the reasoning.

## `hypotheses`: suspected causes and the evidence that settled them

Each entry follows `[{id, t, h, status, exonerated?, evidence?}]`. Ids are
`H\d+`; `status` is `ruled-out`, `confirmed`, or `open`. `exonerated` is the
observation that settled it, and `check` requires one on a ruled-out entry.
`evidence` resolves as a cause's does. The page renders the entries as a
table so the next responder can see which explanations were ruled out.

## `recognize`: how to recognize this next time

`[{signal, means, do}]`, each column 25 words or fewer. `signal` is what a
responder sees, `means` what it tells them, and `do` the next step. The
page renders the rows as a table in their own section after Lessons.

## `unknowns`: questions the record leaves unanswered

Each entry follows `[{id, q, h, why?, owner?, refs?}]`. Ids are `U\d+`; `q`
is the open question in 25 words or fewer; `why` says why it stayed open or
why it matters, at most 45 words excluding URLs and inline code; excess draws
a strict warning. `owner` names the person responsible for answering it.
The closed row shows `h`; expanding it shows `q` and `why`.

## `glossary`

`[{term, def}]`, each definition 30 words or fewer. Each term has a meaning
specific to this system; general vocabulary does not need a glossary entry.
`check` errors on a repeated term.

## `lessons`

`{well: [{text}], wrong: [{text}], lucky: [{text}]}`. Each lesson is one line of forty words or fewer; an empty column is hidden.

## `evidence`: the register of what the page renders and links

Every key is present as a list, empty when unused. A `file` is a path under `evidence/`, relative to the retro directory; `check` errors on a missing file and on a snapshot whose `id` or `permalink` disagrees with its register entry.

| Key | Entry | Snapshot |
|---|---|---|
| `notebooks` | `{id, h, url, file}` | `evidence/datadog/notebook-<id>.json`, schema `ir.notebook/1` |
| `monitors` | `{id, h, url, file}` | `evidence/datadog/monitor-<id>.json`, schema `ir.monitor/1` |
| `slack` | `{url, file}` | `evidence/slack/<channel_name>-<ts>.json`, schema `ir.slack/1` |
| `sentry` | `{url, label}` | none |
| `linear` | `{url, key, label?}` with `key` like `ENG-123` | none |
| `builds` | `{h, url, label}` | none |
| `prs` | `{h, url, role}` with `role` in `cause`, `fix`, `monitor`, `followup` | none |
| `runs` | `{id, label, url?}` | none |
| `images` | `{file, alt, caption?, cites?}` | the image itself under `evidence/images/` |
| `docs` | `{url, label}` | none |

The snapshot formats and the output from `retro.py evidence fetch` and `evidence slack new` are in `reference/evidence.md`. `check` reads each snapshot's `schema` and `id`. It warns when a notebook's `time` does not span onset to resolved or the notebook was fetched live. It also warns when a monitor in `detection.monitors[]` has no file or a cited Slack permalink has no snapshot.

`EVIDENCE_NAMED = ("notebooks", "monitors", "builds", "prs")` defines the
evidence entries that need `h`, even if they have a `label`. The renderer
still uses snapshot names and labels for these entries.

A Slack snapshot is verbatim evidence, not authored prose. Never pass it
through a language model. Slack entries carry no authored `h`.

`irSlack` derives the row name from the participants. For one
message, `openingWords` takes the first eight words of its plain text
(`OPENING_WORDS = 8`) as the secondary line; longer snapshots show their
message count. The row also shows the first message's local time.

The page groups snapshots by channel and the outage window containing the first
message. Each group starts closed and shows its channel, participants,
message count, and time span without message text. The individual row labels
sit inside that group and are hidden by default.
[reference/components.md](components.md) defines the grouping details.

## `notes` and `footnotes`

`notes`: `[{t, md, component?}]`, prose blocks the Notes section renders in order. Each starts closed with `t` as its short heading; notes do not carry a required `h`. Expanding reveals `md` and the declared `component`, if present. `footnotes`: `[{n, b}]`, referenced from prose as `[^n]`; `check` errors on a token with no footnote and warns on a footnote nothing references.

## `components`: declared interactive blocks

`components` maps a lower-case, hyphenated id to one block. The same schema walker as design-doc validates it against `reference/components/<kind>.json`. The retro kit is `ir.tiles`, `ir.windows`, `ir.timeline`, `ir.notebook`, `ir.monitor`, `ir.slack-thread`, `ir.actions`, and `ir.causes`; `reference/components.md` describes them. The reusable design-doc kinds `dd.tabs`, `dd.before-after`, `dd.whatif`, `dd.steps`, `dd.timeline`, `dd.matrix`, `dd.flow`, and `dd.lanes` also work.

Tiles, the windows gantt, the swimlane, notebooks, monitors, Slack threads, the actions table, and the causes tree render without a declaration. Declare a component to place it through a cause's `component`, a note's `component`, `impact.component`, `resolution.component`, or `detection.component`. A declaration can also override the component's options.

`check` warns when a declaration has no placement. It also rejects an `ir.notebook` with an unregistered notebook or missing cell index, an `ir.monitor` with an unregistered monitor, and an `ir.slack-thread` with no snapshot. For the remaining kinds, it rejects `ir.tiles` keys outside `timestamps`, an unknown `ir.windows` window, a `dd.timeline` gate with no register id, and a `dd.matrix` whose cells do not match its rows and columns.

## Ids, handles and citations

Citeable ids are `W\d+`, `T\d+`, `C\d+`, `AI\d+`, `I\d+`, `D\d+`, `H\d+`, and `U\d+`, unique across the file. On the page, `(C1)` or `(C1, T7)` renders as the handles in parentheses. `retro.py text` uses handles except for timeline citations, which remain local times.

A handle `h` is required on every window, timeline entry, cause, action,
decision, hypothesis, unknown, sub-incident, and entry in `evidence.notebooks`,
`evidence.monitors`, `evidence.builds`, and `evidence.prs`. Missing handles draw
strict warnings. A nonempty handle must contain 2 to 6 words excluding URLs
and inline code. A count outside that range is an error without `--strict`.
Write a noun phrase with no trailing period or register id. A trailing period
warns; a register id draws a strict warning.

## Links

A link is either an https URL string or `{url, kind?, label?, closes?}`. In the
object form, `url` is https and `kind` is `pr`, `issue`, `commit`, or `doc`. A
`github.com` URL determines the kind, which must agree with that URL. A Linear
or other issue tracker URL is a `doc` with a `label`. `closes` belongs only on
an action's link.

## Snapshots and history

`retro.py snapshot --note "…" [--item "…"]…` writes `meta.rev` and appends to `meta.revisions`. It archives the whole file as `history/rev-<N>.json`. The revision entry records `files.evidence`, a digest over every file under `evidence/`, so a re-fetched notebook is a revision; `changed: ["evidence"]` marks one where only the evidence moved. `check` verifies that `rev` equals the last revision, revisions increase from 1, and every `history/rev-N.json` exists and parses. It warns when the evidence digest has moved since the current revision.

## `prose`: authored fields and provenance

`retro.py prose <dir>` routes the fields below through `codex-ask -m astra`.
`targets()` enumerates `meta.title` as `headline` and `meta.subtitle` as
`subtitle`; both are addressable with `--field`. `meta.tags` stays
operator-chosen data: tags are a controlled vocabulary for filtering, not
writing. `--list` prints the addresses available in the current record,
including empty fields whose containing objects exist.

| Fields | Addresses |
|---|---|
| Headline and subtitle | `meta.title` (`headline`), `meta.subtitle` (`subtitle`) |
| Section openers and takeaways | `meta.sections.<sectionId>.sub`, `meta.sections.<sectionId>.takeaway` |
| Short names | `<id>.h`, `timeline[<index>].h` when no id exists, `evidence.<kind>[<index>].h` for notebooks, monitors, builds, and PRs |
| Twinned blocks | `summary.text`, `summary.p`, and the same pair on `impact`, `resolution`, and `detection` |
| Windows and timeline text | `W1.text`, `T7.text`, or `timeline[<index>].text` |
| Causes | `C1.text`, `C1.p`, `C1.code.caption` when `code` exists |
| Impact per team | `impact.teams[<index>].text` |
| Actions and sub-incidents | `AI1.t`, `AI1.note`, `I1.t` |
| Decisions, hypotheses, unknowns | `D1.t`, `D1.why`, `D1.alternatives`, `H1.t`, `H1.exonerated`, `U1.q`, `U1.why` |
| Recognition and glossary | `recognize[<index>].signal`, `.means`, `.do`; `glossary[<index>].def` |
| Lessons | `lessons.<column>[<index>].text`, with column `well`, `wrong`, or `lucky` |
| Images, notes, footnotes | `evidence.images[<index>].caption`, `notes[<index>].md`, `footnotes[<n>].b` |
| Executive summary | `summary.html#<data-kind>`, the panel's inner HTML including its heading |

Array indexes start at zero; footnotes use their `n` value when present.
Register ids replace array paths, so a cause uses `C1.text`, not
`causes[0].text`. Cause and note titles in `t`, evidence labels, component
prose, revision notes, and `NOTES.md` are outside this command's field list
and provenance check.

The enumeration excludes `takeaway` addresses for
`REFERENCE_SECTIONS = ("evidence", "glossary", "notes")`. Their `sub` addresses
remain in the field list.
Slack snapshots and their mechanically derived row labels are outside the
prose field list.

The command builds work orders for one retro, split into batches. Each
points the model at this skill's writing contract and `SKILL.md`, plus the
cached `writing-docs` contract and references when available. The work order
also names `slop-cop-rules.json`, generated by `slop-cop rules --pretty`.
The command reuses that file when it exists.

The linter's full catalog contains 226 rules, each with a `description`, a
`tip`, and an `llmDirective`. The model must write to those rules in its first
draft. The command calls `codex-ask -m astra` as a subprocess with a JSON reply schema of
`{"fields": [{"id": "<address>", "text": "<wording>"}]}`. It writes accepted
text directly into `retro.json` and `summary.html`.

The fact freeze strips HTML tags and backticks before comparing tokens.
Existing fields other than the headline and subtitle must preserve
URLs, identifiers containing underscores, and numeric tokens, including
counts, dates, and times. ``FENCE = re.compile(r"`+")`` removes code-span
delimiters before tokenization, so 8 GiB and `` `8 GiB` `` compare equal.
The identifier `manifest_section` stays protected with or
without backticks. Dropping it or changing a number rejects the field.

The command also compares a set of capitalized names, excluding ordinary
sentence-initial words.
Changed tokens reject that field; accepted fields in the same batch still
land. An empty field may use tokens from its containing entry and selected
snapshot context.

The headline and subtitle use their grounding instead of their previous
text for the fact freeze. Grounding contains the other field, `summary.text`,
and `summary.p`.

`over_budget()` compares the field's current text with its word and
character limits. Text within both limits also joins the grounding
so a run that re-derives provenance can retain its facts. Text over either
limit is excluded. A shorter headline or subtitle may drop words and facts;
it may not invent them. The rule is "invent nothing," not "preserve everything."

The fact freeze checks recognized tokens, not all facts:
lowercase identifiers without underscores and URLs inside HTML attributes
are outside that token comparison. Backticks do not protect
arbitrary code-span contents.
Review the returned wording against the record.

| Flag | Effect |
|---|---|
| `--list` | Print each field as `empty`, `locked`, or `unlocked`; make no model call |
| `--field <address>` | Rewrite that field; repeat for several fields. An unknown address errors |
| `--note "[ADDR=]TEXT"` | Steer one field with `ADDR=text`, or every selected field with bare text; repeatable. A later note replaces an earlier note for the same field |
| `--stale` | Rewrite nonempty fields without a matching digest, empty headlines and subtitles, and required short names `h` that are missing or empty. Leave absent optional prose alone |
| `--quick` | Migrate a retro written before 0.3.0: rewrite newly required fields, run deterministic lint only, and pin remaining pre-existing prose as legacy provenance |
| `--batch <N>` | Fields per model call; `PROSE_BATCH = 36` |
| `--dry-run` | Prepare the rule catalog and print the first selected batch's work order without calling the model or changing the retro |
| `--timeout <seconds>` | Timeout per model call; `PROSE_TIMEOUT = 1800` |

Without a selection flag, the command selects every enumerated field.
`--field` takes selection precedence over `--quick`, which takes precedence
over `--stale`. Combining `--field` with `--quick` still skips model lint and
pins the other fields as legacy.

An operator note directs Astra without supplying the prose. The work order
marks it `REQUIRED` and says that returning the current text unchanged does
not answer it. Notes do not select fields; use `--field` to request the
rewrite. For example:

```bash
retro.py prose <dir> --field meta.title \
  --note "meta.title=lead with the disk filling up"
```

In a measured run, that note changed "Read failures and resource exhaustion"
to "A full disk, failed reads, and exhausted executors".

Each accepted reply field runs through the deterministic rules with
`slop-cop check --lang=markdown --llm-effort=off`. Without the explicit `off`,
slop-cop automatically enables its model pass when the Codex CLI is on
`PATH`. A full prose run also calls `slop-cop check --lang=markdown --llm`
once per batch of accepted reply fields, joined with field delimiters. Each
finding's character offset identifies its field and becomes a field-local
offset. `--quick` omits this batched model lint pass; the fact freeze still
checks every reply.

The command returns each violation's rule id, matched text, rule directive, and
suggested change to Astra for revision, with an explanation when present.
`SLOP_ROUNDS = 2` allows two rewrites after the first draft, stopping early
when no findings remain. Each revision asks only for the fields the lint
flagged. The same model writes every round within the
command's subprocess pipeline; no other model edits the returned text.
The command reports refused fields but still exits zero; inspect the report
and rerun those addresses with `--field`.

Work orders and reply schemas live under
`~/.cache/incident-retro/prose/<slug>/batch-<N>/round-<N>/`; the rule catalog
sits at the slug root. The slug comes from `meta.slug`, with the directory
name used when it is absent. Replies and logs remain in the run directory
returned by `codex-ask`. The retro directory is published as a static site,
so work orders, replies, and logs stay outside it. `prose.lock.json` remains
beside the record; `.prose.lock` and atomic-write scratch files are transient.

Before model calls and writes, `prose` takes an exclusive, nonblocking
`fcntl.flock` on `.prose.lock` beside `retro.json` and holds it until writing
ends. A competing claim exits nonzero and names the holder's pid. Two
concurrent runs used to lose each other's fields because each wrote back a
whole file it had read before the other's batch landed. The initial record
read still precedes the claim; the command rereads the record under the claim
before writing.
`.prose.lock` is removed when the writing run ends and is safe to delete
after a killed run if no other run holds it.

Each write to `retro.json` or `prose.lock.json` in this pipeline writes a
`<filename>.<pid>.part` sibling, then calls `Path.replace`, which uses
`os.replace`. Each replacement is atomic; the two files are separate writes.
Successful replacement leaves no scratch file.

`prose.lock.json` sits beside `retro.json`. Its top level records
`model: "gpt-6-astra"`, `command: "codex-ask -m astra"`, and the total `slop`
count. Each field written by Astra records `sha256`, the reply's run
directory in `run`, its `log` path, a UTC `at` timestamp, and a `slop` count
of findings left after revision. `check --strict` sums the per-field counts and fails above
`SLOP_BUDGET = 3`, naming up to five fields with the most findings. This gate
covers the prose fields recorded in the lock, not the whole rendered
document.

`check --strict` also errors when a required short name is absent or a
nonempty enumerated field lacks a matching digest. A hand edit or another
model's rewrite changes that digest; run
`retro.py prose <dir> --field <address>` to restore provenance. The check
compares text hashes only; it does not authenticate the model, run, or log.

### `--quick`: migrate a retro written before 0.3.0

Version 0.3.0 required a compact `meta.title`, but `targets()` did not
enumerate it. `prose --field meta.title` returned `not a prose field`.
Migration left the required title empty and `check --strict` failing.
Only Astra could write it, so the command provided no way to satisfy the
check. Version 0.3.1 adds the missing prose fields.

For a retro written before 0.3.0, move the old `meta.title` into
`meta.subtitle`, replacing the browser-title suffix, and clear `meta.title`.
Supply `meta.tags` yourself. Run `retro.py prose <dir> --quick` for the
migration. To write one field on demand, use `--field`, as in
`retro.py prose <dir> --field meta.title`.

`prose --field meta.title` wrote a 5-word, 37-character headline in
71 seconds with zero lint findings on a copy of a real retro whose title
the rollout had left empty.

`--quick` selects empty or over-budget headlines and subtitles,
missing short names, summary panels, narrative section takeaways, timeline
text over `ENTRY_WORDS = 25`, and cause text over `CAUSE_BODY_WORDS = 90`.
Use the full command for new work.

The two body counts exclude URLs and inline code. The selection uses 25,
not the non-strict error threshold `ENTRY_WORDS_MAX = 40`. Panels follow
`SUMMARY_PANEL_WORDS = 35` and `SUMMARY_BUDGET = 150`; takeaways follow
`TAKEAWAY_WORDS = 18`. The command skips selected fields whose hashes
already match. Prepare all five panel containers and the narrative section
objects first; the command enumerates only structures present in the files.

`grandfather` pins each remaining nonempty enumerated field with no entry
in `prose.lock.json`. The entry contains `sha256`,
`"kind": "legacy"` (`LEGACY = "legacy"`), a `grandfathered` value from
`plugin_version()`, and a UTC `at` timestamp. Existing entries retain their
provenance even when their hashes no longer match. A matching legacy digest
satisfies the strict provenance check for an eligible retro; every other
validation still applies.

`LEGACY_CUTOFF = "2026-09-19"` limits this migration path. `check` uses the
date portion of `timestamps.onset`, falling back to `meta.date`. If that
date is after the cutoff and any lock entry has legacy provenance, `check`
errors even without `--strict` and tells the author to run `prose` without
`--quick`. The cutoff date itself is allowed. This gate prevents legacy
provenance on later incidents; it does not establish when the retro was
written.

A later edit breaks the field's hash and fails `check --strict`. Send the
edited field through `prose --field` without `--quick`. Running `--quick`
again does not clear that failure: the migration pins a field only when the
lock has no entry for it, so an edited field keeps the hash it was pinned
with and stays failing until Astra rewrites it.

## `render-check`: the initial page

`retro.py render-check <dir> [--words N]` measures the settled page at
1440 by 900 pixels. It checks readiness, component mounting, notebook cells,
and chart loading, then reports visible words, document height, and open
disclosures. It measures the initial state; it does not close disclosures
configured with `open: true`.

The word count walks text nodes under `document.body` and splits each trimmed
node on whitespace. It asks the browser whether the parent element is
painted through `Element.checkVisibility({contentVisibilityAuto: true,
opacityProperty: true, visibilityProperty: true})` and requires a nonempty
`getClientRects()` result. Anything inside `[aria-hidden=true]` is excluded
as decorative. The same visibility test counts Slack lines and evidence
bodies. It can count content below the viewport; it does not check viewport
intersection or occlusion.

A closed `details` element can still paint its body when a CSS `display`
rule breaks the browser's disclosure layout. The painted-visibility check
counts that body. A structural check that excludes non-summary descendants
of closed disclosures misses this defect.

| Measurement | Gate |
|---|---|
| Visible words | Error above `--words`, default 1500 |
| Slack lines matching `.ir-msg, .ir-slack .sumline` | Error above 0 visible matches |
| Bodies matching `.ir-msg .msg, .ir-cell table, .ir-mon table, .ir-nb .nbbody` | Error on any visible match |
| Document `scrollHeight` and visible `details[open]` | Reported without a limit |

## Libraries

The page loads its libraries from jsdelivr at exact versions, pinned in one `LIBS` block at the top of the template's script and repeated here so `check` can compare the two:

| Library | Version | Files |
|---|---|---|
| `lucide-static` | `lucide-static@1.39.0` | `icons/<name>.svg`, as design-doc |
| `uplot` | `uplot@1.6.32` | `https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js` and `https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.min.css` |

Mermaid is not used. The template draws the retro's tiles and windows as SVG. It also draws the swimlane as SVG, while `uPlot` draws the notebook series on canvas. `check` warns when the template pins a version this table does not state.

## Artifacts

| File | Role |
|---|---|
| `retro.json` | canonical structured retro |
| `summary.html` | the executive summary, a body-level fragment whose panel prose is written by `prose` |
| `prose.lock.json` | model and command metadata, total `slop`, and each accepted field's digest, run directory, log path, timestamp, and remaining `slop` count; migrated legacy entries carry a digest, `kind`, `grandfathered` plugin version, and timestamp |
| `.prose.lock` | transient exclusive writer claim (`WRITE_LOCK = ".prose.lock"`); removed when the writing run ends and safe to delete after a killed run if no other run holds it |
| `<filename>.<pid>.part` | transient sibling used to replace `retro.json` or `prose.lock.json` atomically; removed by successful replacement |
| `~/.cache/incident-retro/prose/<slug>/slop-cop-rules.json` | the linter's full rule catalog, outside the published retro directory |
| `~/.cache/incident-retro/prose/<slug>/batch-<N>/round-<N>/` | generated work orders and JSON reply schemas |
| Run and log paths recorded in `prose.lock.json` | replies and logs returned by `codex-ask` |
| `NOTES.md` | prose that does not fit structure |
| `evidence/datadog/`, `evidence/slack/`, `evidence/images/` | snapshot files the page renders |
| `history/rev-<N>.json` | archived revisions, written by `snapshot` |
| `incident-retro.html` | the renderer, copied in by `scaffold`; must be served over HTTP |
| `incident-retro.pdf` | printed by `retro.py pdf`; generated, never checked in |
| `<slug>-incident-retro.md` | the Markdown export the page downloads; `retro.py text` prints the same document |

## What `retro.py check` enforces

Errors unless noted; `--strict` promotes the strict warnings.

1. `meta`: title, subtitle, tags, slug, date present; status, severity, authors without `@`, repo, ref, timezone, `homeLink`, `subIncidents`, sections, ai. Checks headline and subtitle limits, colons, and identifiers; tag count, shape, and uniqueness; and slug shape, length, and word count. Takeaways over 18 words and any string takeaway on `evidence`, `glossary`, or `notes` draw strict warnings.
2. Timestamps parse with an offset and stay in order; onset and resolved set unless draft, enforced in strict mode.
3. Windows: ids, kind, start before end, incident resolves; overlaps on one team warn.
4. Timeline: order, kinds, unique ids, phase outside the incident, refs are https, Slack refs are permalinks with snapshots, and deploys carry a change ref. Warns on missing snapshots or change refs, no key moment, or more than eight key moments. Text over 25 words draws a strict warning; over 40 is an error.
5. Impact metrics: `measured` boolean, cites resolve.
6. Causes: ids, kinds, evidence resolves, code shape, links without `closes`. Strict mode requires a root cause once reviewed. It limits the plain twin, or the first sentence of `text` when no twin exists, to 25 words and the cause body to 90.
7. Actions: ids, states, owner, source resolves, `closes` only on pull requests or issues, due dates; strict mode requires owners and rejects open actions on a resolved retro.
8. Evidence: files exist, snapshot schemas and ids match, images carry alt, prs carry a role; notebook windows, liveness, and monitors without files warn.
9. Citations and footnotes resolve.
10. Handles present across all short-named registers; missing handles draw strict warnings. Nonempty handles outside 2 to 6 words error without strict mode; register ids draw strict warnings and trailing periods warn. Twins must be present and within the rules; a twin left stale across a snapshot errors.
11. Prose stating a derived duration warns.
12. Capitalization over titles, handles and labels.
13. Component schemas and their hosts.
14. Forbidden terms: the check scans `retro.json`, `NOTES.md` and every text file under `evidence/`. It loads terms from `--forbidden-terms`, `FORBIDDEN_TERMS`, or the nearest `.customer-names`, and warns when none is configured.
15. Library pins against this file warn.
16. Template freshness against `plugins/_shared` when that source tree is present.
17. `ai.json` beside the retro or in its parent directory.
18. Revision history integrity.
19. Decisions, hypotheses, recognize rows, unknowns, and the glossary: ids, required fields, timestamps, citations, and the word limits above.
20. `summary.html`: the fragment rules, panel vocabulary and order, 35 words per panel, 150 total, and a first heading that does not restate the title.
21. Prose provenance: required short names are present and each nonempty enumerated field has a matching SHA-256 in `prose.lock.json`; a missing short name or missing or stale digest draws a strict warning. Legacy provenance errors without `--strict` when the onset date, falling back to `meta.date`, is after `LEGACY_CUTOFF = "2026-09-19"`. More than 3 recorded prose findings also draws a strict warning, naming the fields with the most findings. This count covers the locked fields, not the whole rendered document.
