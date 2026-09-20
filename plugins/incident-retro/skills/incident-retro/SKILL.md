---
name: incident-retro
description: Build a blameless incident retrospective from structured facts and committed evidence snapshots, then render the same record as HTML, Markdown, and PDF. Use when asked to "write a retro", create an "incident retrospective" or "postmortem", "import this Google Doc postmortem", run a "blameless review", or turn an incident timeline and its evidence into a reviewable record.
allowed-tools: Bash(python3:*, ls:*, cat:*, pdftoppm:*, wrangler:*, npm:*, open:*, wlm:*, slop-cop:*, uvx:*, ssh:*, rsync:*, cc-present:*), Bash(aws:*), Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# incident-retro

While `meta.status` is `ongoing`, the live commands derive the record from
incident state without producing LLM-authored prose. After `live finalize`,
GPT-6 Astra (`gpt-6-astra`) at `xhigh` writes and revises all new prose,
including headlines, subtitles, summaries, plain twins, handles, revision
notes, and publication text. Use `retro.py prose` for its enumerated fields
in `retro.json` and `summary.html`; it calls `codex-ask -m astra` and records their provenance in
`prose.lock.json`. It has no fallback writer. For a retro written before
0.3.0, use the `--quick` migration below to retain eligible existing prose.

For prose outside that field list, delegated authors use the same model and
effort. In Codex, set the model and effort explicitly when spawning an author. From Claude, load the `codex` skill and
use `codex-ask -m astra`; Claude collects evidence and publishes the result,
but delegates the writing. Tags remain operator-chosen data: they are a
controlled vocabulary for filtering, not writing. `meta.tags` is outside
the prose field list.

An incident retro pairs one canonical `retro.json` with committed evidence snapshots and an executive summary in `summary.html`. `incident-retro.html` renders that record, and `retro.py` derives every duration from timestamp fields. Write in a blameless voice that explains what the system allowed, not which person deserves blame.

The page is for readers who did not take part in the response. Beneath the title, subtitle, tags, and status, it opens with the status strip, executive summary, What's changed, timeline, and causes, then impact, resolution, lessons, actions, evidence, and the remaining sections. Narrative sections start with a takeaway of 18 words or fewer (`TAKEAWAY_WORDS = 18`) and keep the details behind a disclosure.

`REFERENCE_SECTIONS = ("evidence", "glossary", "notes")` open on their heading and collapsed structure, with no takeaway. Conclusions belong in the sections that argue them. Closed timeline, cause, decision, and unknown rows show a short name `h`; their sentences appear on expansion. Each short name and section opener must make sense on its own.

Use one driver for every mechanical step:

```bash
TOOL="python3 ${CLAUDE_PLUGIN_ROOT}/skills/incident-retro/scripts/retro.py"
```

The AWS Systems Manager Parameter Store paths come from the caller. The AWS profile and region do too; the plugin supplies no credential path or cloud default.

Read [reference/writing.md](reference/writing.md) before Draft, [reference/evidence.md](reference/evidence.md) before Gather, and [reference/schema.md](reference/schema.md) whenever a field is unclear.

## Live incident

At intake, use the incident skill's `state.json` and `slack-log.jsonl` under
`<incident-dir>`. Set `state.retro_slug` to the incident date plus three to
six plain words and use codenames in the title and slug before scaffolding.
Configure `--forbidden-terms`, `FORBIDDEN_TERMS`, or `.customer-names` in the
design-docs checkout before publishing.

```bash
$TOOL live init <incident-dir> --docs <design-docs-checkout>
```

`init` creates `incident-retros/<slug>/` with `meta.status: "ongoing"`,
records the slug in state, and adds cards to both index pages. It makes no
commit or pull request. Open and merge the shell PR once, with the retro
and both cards in its first commit. Share the resulting page URL as the
incident's status link. Pages deploys only from `main`, about 10 minutes
after merge; that delay applies to the shell, not to each live update.

Run sync on every change to `state.json` or `slack-log.jsonl`:

```bash
$TOOL live sync <incident-dir> --docs <design-docs-checkout>
```

`sync` derives the timeline, windows, causes, actions, and Slack snapshots,
updates `live.updatedAt`, and force-pushes `retro.json` and `evidence/slack/`
to `live/<slug>`. The checkout's HEAD and index stay in place. Use
`--no-push` to write and check locally. No live command calls a model;
do not run `prose` or draft narrative while the incident is ongoing.

Every live push must pass the codename scrub and forbidden-terms check.
`sync` replaces names through `teams[].aliases`, then runs `check`; an
error blocks the push. Keep the aliases and forbidden-terms source complete.
No CI guards `live/<slug>`, so a missing terms source is a publishing block
even though `check` only warns about it.

While status is `ongoing` and `live.source` is set, the page polls that
branch through the GitHub contents API every 30 seconds. It uses the token
from `ai.json`, redraws on a changed SHA, and preserves scroll position and
open disclosures. A pulsing Live pill marks the page; five minutes without
an update shows a staleness warning. A failed fetch uses the merged record.

At all-clear, sync the final state and stop the updater before finalizing:

```bash
$TOOL live finalize <incident-dir> --docs <design-docs-checkout> \
  --tags "migration,release-pipeline"
```

`finalize` removes `live.source`, sets `meta.status` to `draft`, and fills an
unset `timestamps.resolved` from `state.all_clear_at`. Supply two to six topical
tags; the draft requires them. It changes local files and runs `check`.
Continue through Gather, Draft, Evidence, Check, and Publish in the same
directory and at the same URL. Astra writes all prose from this point on,
using `retro.py prose` for its enumerated fields. The published draft no
longer polls the live branch.

The sticky time scrubber under the title block shows the page as of a chosen
instant. Drag its playhead, step between events with the left and right
arrows, or select Live/Now to reset. Link to an instant with `?at=<iso>`.
The existing `?since=` revision-diff parameter is separate and unchanged.

Actions and hypotheses replay only with `history`; causes appear at their
`identifiedAt`. Without those fields, entries show their current state with
a muted "no time data" mark. See [reference/components.md](reference/components.md#the-time-scrubber).

## What the agent writes after all-clear

`retro.py` scaffolds, writes prose, validates, renders, snapshots, and fetches Datadog evidence. The authoring agent assembles the incident record and Slack snapshots, then runs the prose command:

- Fill `retro.json` from inspected evidence. Do not infer a missing event, cause, owner, or outcome.
- Run `prose` to have Astra write `meta.title` as a headline within `DOC_TITLE_WORDS = 8` words and `DOC_TITLE_CHARS = 60` characters, with no final period. Astra writes `meta.subtitle` as a causal sentence within `SUBTITLE_WORDS = 20` words and `SUBTITLE_CHARS = 120` characters. Use no colon or identifier in either. Set `meta.slug` to the incident date plus three to six plain words. The rule and examples are in [reference/writing.md](reference/writing.md).
- Add 2 to 6 distinct topical `meta.tags`, such as `migration`, `release-pipeline`, and `paging`. Keep team codenames in `meta.teams`.
- For a retro written before 0.3.0, move the old `meta.title` into `meta.subtitle`, clear `meta.title`, and run `prose --quick` to write the newly required prose through Astra. Supply the tags yourself. Replace the old subtitle's browser-title suffix value.
- Once the causes and actions are settled, prepare one `summary.html` panel per question and run its wording through `prose`.
- Fetch Slack messages with the agent's own Slack tooling. Save the resulting `ir.slack/1` files under `evidence/slack/`, then register each file in `evidence.slack[]`.
- Write every timestamp as ISO 8601 with a UTC offset. `meta.timezone` controls display only.
- Write each plain twin `p` in 30 words or fewer. Keep every fact from the precise wording, but include no register id or file path.
- Give every window, timeline entry, cause, action, decision, hypothesis, unknown, sub-incident, and notebook, monitor, build, or PR evidence entry a distinct noun-phrase `h` of 2 to 6 words. A nonempty handle outside that range errors even without strict mode. Missing handles and register ids draw strict warnings; trailing periods warn. Evidence labels do not waive the handle requirement. Slack snapshots are verbatim evidence; never pass them through a language model. The renderer derives their row labels mechanically.
- Use deployment or service codenames in public prose. Never publish the name of a customer, company, workspace, or account.

## Phase 1: Gather

After `live finalize`, collect the source material in the existing retro
directory. For a retro that did not start live, create a fresh directory:

```bash
$TOOL scaffold <dir> --title "<headline>" --subtitle "<causal sentence>" \
  --tags "migration,release-pipeline" --date YYYY-MM-DD [--incident N]
```

Without `--subtitle`, scaffold copies the title into the subtitle. Without `--tags`, it leaves an empty list that fails `check`.

Collect incident-channel permalinks, Datadog notebook and monitor ids, pull requests, issue-tracker links, builds, Sentry issues, run ids, and images. Prefer a permalink to a pasted claim because the rendered retro can connect the claim to its source.

Fetch the Datadog snapshots. Repeat `--notebook` and `--monitor` as needed.

```bash
$TOOL evidence fetch <dir> --notebook <id-or-url> --monitor <id-or-url> \
  --from-ssm --ssm-api-key-path <api-key-path> \
  --ssm-app-key-path <app-key-path> --aws-profile <profile> \
  --aws-region <region>
```

For each Slack permalink, generate a skeleton, fill `messages[]` from the agent's Slack results, record any removals in `redactions[]`, register the file, and validate the set.

```bash
$TOOL evidence slack new --permalink <url> --channel-name <name> [--thread]
$TOOL evidence slack check <dir>
```

Copy publishable screenshots into `evidence/images/`. Give every image useful alt text and a caption backed by a cause or window.

## Phase 2: Draft

Fill `timestamps` first. The opening tiles depend on `onset`, `detected`, `engaged`, `mitigated`, `resolved`, and `allClear`, and the renderer computes their durations.

Then draft in reading order:

1. Add `windows` for distinct periods of outage or degradation, including partial impact.
2. Build the timestamp-sorted `timeline`. Give each entry a short name `h` and event text within 25 words. Name each actor by role or first name and attach a source in `refs` wherever one exists. Mark eight or fewer entries needed to explain the incident with `key: true`.
3. Separate the trigger and root cause from contributing causes. Attach the evidence that supports each claim.
4. State `impact` through observed effects and measured or estimated metrics.
5. Describe `resolution` and `detection`, including monitors that caught or missed the incident and monitors added afterward. Record the `decisions` made during the response, who made each, and why. Record the `hypotheses` ruled out and the evidence that cleared them.
6. Fill every applicable `lessons` group from the evidence, then write the `recognize` rows for the next responder.
7. Give every action an owner, source, state, and due date when one exists.
8. Record each question the sources leave unanswered in `unknowns`. Define terms with a meaning specific to this system in `glossary`.
9. Write the plain twins and handles alongside their precise text. Write one `takeaway` of 18 words or fewer per narrative section in `meta.sections`; omit it from `evidence`, `glossary`, and `notes`.
10. Prepare `summary.html` last: one panel per question, in the order `what-happened`, `impact`, `why`, `what-changed`, `still-open`. Each panel has one `h3.xs-head` of at most 14 words, a `ul.xs-points` with at most 3 `li` of at most 18 words each, and an optional `.xs-stats` block. Give the first heading an answer beyond the headline.
11. Run `prose --list` to inspect the field addresses, then `prose` to write them through Astra.
12. Review refused fields and lint findings, and rerun affected addresses with `--field`. Keep `prose.lock.json` beside the record.

Follow [reference/writing.md](reference/writing.md). Mark a required answer as not recorded when the sources do not provide it. Never invent connective events to make the story read more smoothly.

```bash
$TOOL prose <dir> --list
$TOOL prose <dir>
$TOOL prose <dir> --field C1.text --field C1.p
$TOOL prose <dir> --field meta.title \
  --note "meta.title=lead with the disk filling up"
```

`--note "[ADDR=]TEXT"` is repeatable. `ADDR=text` steers one field; bare text
steers every selected field. A later note replaces an earlier note for the
same field. Notes direct the writing without supplying it and do not change
field selection. The work order marks each note `REQUIRED` and tells Astra
that returning the current text unchanged does not answer it. The disk
note changed "Read failures and resource exhaustion" to "A full disk,
failed reads, and exhausted executors" in a measured run.

`--stale` selects nonempty fields without matching provenance, empty
headlines and subtitles, and required short names `h` that are absent or
empty. It leaves absent optional prose alone. The field list includes
`meta.title` as `headline`, `meta.subtitle`
as `subtitle`, action titles and notes, decision titles and alternatives,
hypothesis titles, and sub-incident titles.

`--batch` defaults to `PROSE_BATCH = 36` fields per call, and `--timeout` to
`PROSE_TIMEOUT = 1800` seconds per call. `--dry-run` prints the first batch's
work order without calling the model. The field list and token-preservation
limits are in
[reference/schema.md](reference/schema.md#prose-authored-fields-and-provenance).
The command writes accepted fields even when it refuses others, so inspect
the report before continuing. After a later edit, rerun the affected field
through `prose`; a changed hash fails `check --strict`.

The work order names the writing contract and the full rule catalog from
`slop-cop rules --pretty`, so Astra writes to the rules in the first draft.

The command runs deterministic lint per accepted reply field with
`--llm-effort=off`; omitting the flag lets slop-cop enable its model pass
when the Codex CLI is on `PATH`. Model lint runs once per batch over fields
joined with delimiters, and character offsets attribute each finding to
its field. The command returns the rule id, matched text, directive, and
suggested change to Astra. It allows two revision rounds per batch
(`SLOP_ROUNDS = 2`), asking only for flagged fields. No other model edits
the text.

A measured 12-field batch took 0.26 seconds for deterministic lint
and 4.32 seconds for model lint, against roughly 40 seconds before, with
identical findings.

The fact freeze strips backticks before tokenizing. Adding a code span
around 8 GiB preserves the same facts; `manifest_section` stays protected
with or without backticks. The command rejects rewrites that drop
identifiers or change numbers in fields other than the headline and subtitle.

For the headline and subtitle, the fact freeze checks the other field plus
`summary.text` and `summary.p`. `over_budget()` decides whether the current
text also belongs in that grounding. Text within both budgets stays so a
run can re-derive provenance. The command excludes text over either budget.
Astra may drop facts to shorten these fields but may invent none.

Work orders, schemas, and the rule catalog live under
`~/.cache/incident-retro/prose/<slug>/`, keyed by `meta.slug`. Replies and
logs stay in the run directory returned by `codex-ask`; the lock records
their paths. `prose.lock.json` stays beside `retro.json`; `.prose.lock` and
the atomic-write scratch files are transient. The retro directory is
published as a static site.

Before model calls and writes, the command takes an exclusive, nonblocking
`fcntl.flock` on `.prose.lock` beside `retro.json` and holds it until writing
ends. A competing claim exits nonzero and names the holder's pid. Two
concurrent runs used to lose each other's fields because each wrote back a
whole file it had read before the other's batch landed. The initial record
read still precedes the claim; the command rereads the record under the
claim. `.prose.lock` is removed when the writing run
ends and is safe to delete after a killed run if no other run holds it.
Writes to `retro.json` and `prose.lock.json` each use a
`<filename>.<pid>.part` sibling followed by `Path.replace`, which uses
`os.replace`; successful replacement leaves no scratch file.

### Migrate a retro written before 0.3.0

Version 0.3.0 required a compact title but `targets()` omitted `meta.title`.
`prose --field meta.title` returned `not a prose field`, leaving migrated
retros with an empty required title and a permanent strict error. Only
Astra may write the title, so the command provided no way to satisfy the
check. Version 0.3.1 makes the headline and subtitle prose fields.

Move the old `meta.title` into `meta.subtitle`, replacing the browser-title
suffix, clear `meta.title`, and supply `meta.tags`. Run `--quick` for the
initial migration:

```bash
$TOOL prose <dir> --quick
```

To write one field on demand, use `--field`, as in `prose --field meta.title`.

`prose --field meta.title` wrote a 5-word, 37-character headline in
71 seconds with zero lint findings on a copy of a real retro whose title
the rollout had left empty.

`--quick` asks Astra only for empty or over-budget headlines and subtitles,
missing short names, the five summary panels, narrative section takeaways within
`TAKEAWAY_WORDS = 18`, and timeline or cause text over `ENTRY_WORDS = 25`
or `CAUSE_BODY_WORDS = 90`. Prepare the
panel containers and narrative section objects first. The command skips
fields whose hashes already match and runs deterministic lint only. The
fact freeze stays on.

`grandfather` records other nonempty pre-existing fields with no entry in
`prose.lock.json` with `"kind": "legacy"`, their `sha256`,
and a `grandfathered` stamp naming the plugin version. Matching legacy
hashes satisfy the strict provenance gate for eligible retros. A later
edit breaks its hash; run `prose --field` without `--quick` for that field.

`LEGACY_CUTOFF = "2026-09-19"` prevents legacy provenance on later
incidents. `check` compares the onset date, falling back to `meta.date`,
and errors after that date even without `--strict`. Its error directs the
author to run `prose` without `--quick`. The gate checks the incident date,
not when the retro was written.

Another `--quick` run preserves existing lock entries, so it cannot re-pin
a field after a hand edit. Use this path
only for the initial migration. New writing goes through the full command;
`--quick` is not a general escape hatch.

A measured 0.3.0 migration of an older retro with 15 timeline entries and
4 causes asked Astra for 61 fields against 138 in a full run. It pinned
77 fields as legacy, made 6 model calls, and took about 16 minutes.

## Phase 3: Evidence

Keep the evidence beside the claims it supports. The default renderer already places tiles, windows, the timeline, causes, actions, notebooks, monitors, and Slack threads. Declare a component only when another placement or a narrower view helps the reader.

Place a notebook cell subset beside the cause it proves. Add a fix-rollout timeline to show order and gates. Reserve a before-and-after figure for a contrast that prose cannot carry.

Run the Slack validator again after editing a snapshot:

```bash
$TOOL evidence slack check <dir>
```

## Phase 4: Check

Run the mechanical gates before review:

```bash
$TOOL check <dir> --strict
$TOOL render-check <dir>
$TOOL text <dir> | slop-cop check - --lang=markdown --llm-effort=off
$TOOL pdf <dir>
```

Fix every structural error. Triage every prose finding against [reference/writing.md](reference/writing.md). Inspect the generated PDF; file existence does not prove that its layout and charts read correctly across page breaks.

`--strict` enforces the headline, subtitle, tags, slug, short names, twins, takeaways, and key-moment rules. Narrative takeaways have at most 18 words; `evidence`, `glossary`, and `notes` carry none. The word limits are 25 for timeline text, 90 for cause text, 60 for decision reasoning, and 45 for unknown reasoning. Each `summary.html` panel has one `h3.xs-head` of at most 14 words, a `ul.xs-points` with at most 3 `li` of at most 18 words each, and an optional `.xs-stats` block. Strict mode also requires a matching `prose.lock.json` digest for every nonempty enumerated prose field and rejects missing short names.

The lock records per-field `slop` counts and a total; strict mode sums those
counts and fails above `SLOP_BUDGET = 3`, naming the fields with the most
findings. The prose gate covers the landed prose fields, not the whole rendered
document. Revise failed prose through `prose --field`, then rerun the checks.

`render-check` measures the initial page, with disclosures closed by default. Its visible-word budget is 1500 unless `--words` overrides it. It rejects visible Slack messages and the notebook, monitor, and transcript body selectors documented in the schema. Height and open-disclosure count are reported without a limit. It does not force `open: true` components closed.

Visibility follows what the browser paints, using `Element.checkVisibility()`
and `getClientRects()` and excluding `[aria-hidden=true]` content as
decorative. A closed disclosure whose body paints because of a CSS `display`
rule is counted and can fail these gates.

Open the served page as a reader who did not take part in the response. Confirm that the initial view explains the failure and its cause, with enough evidence to assess the impact.

When changing the prose pipeline, run its 26 tests from this skill
directory:

```bash
python3 scripts/test_retro_prose.py
```

The suite checks the exclusive claim and its release, recovery after a
process exits while holding the lock, and the record read under the claim.
It checks atomic replacement, scratch cleanup, the stale-field rule,
attribution of batched lint findings, and legacy provenance that cannot
re-pin hand edits.
It also checks headline budgets and grounding, addressed and global notes,
unknown note addresses, and notes surviving the record reread. The
fact-freeze cases cover code-span markup and identifiers with underscores
as well as changed numbers, invented facts, and headline compression.

## Phase 5: Publish

Record the revision readers need to review, then resolve linked change state:

```bash
$TOOL snapshot <dir> --note "<headline>" --item "<change>"
$TOOL links <dir> --fetch
```

Write the snapshot note for a returning reader. Name what changed and why it matters without register ids. Add another `--item` for each distinct change.

For the hand-maintained design-docs index, make each retro card read
`meta.title`, `meta.subtitle`, and `meta.tags` from that retro's `retro.json`.
The retro page also exposes its space-separated tags as `html[data-tags]`.

Move `meta.status` through this lifecycle:

- `ongoing` from intake through the live response, before prose drafting.
- `draft` while evidence and prose are incomplete.
- `in-review` when the strict checks pass and the retro is ready for comments.
- `reviewed` after the retro meeting confirms the record and action items.
- `resolved` after every action is `done` or `dropped`.

As actions land, add their pull requests or issues to `links[]`, mark the link that completes an action with `closes: true`, and rerun `links --fetch` before the next snapshot.

## Import mode

Import an existing Google Docs Markdown export into a draft:

```bash
$TOOL import-gdoc <md> [<docs.json>] --out <dir> [--tz <zone>]
```

Read the Import report in `NOTES.md`. Review every timestamp conversion, kind guess, actor, link classification, image, and unplaced block. The importer puts the document's own heading into `meta.subtitle` and leaves `meta.title` and `meta.tags` empty, recording that work in the import notes. Leave the title empty and run `prose --field meta.title --field meta.subtitle` to write both through Astra. Supply topical tags yourself. Handles and twins also need completion through `prose`; collect the referenced evidence before continuing at Draft.

## Reference files

[reference/schema.md](reference/schema.md) defines the `retro.json` fields and
validator behavior. [reference/writing.md](reference/writing.md) governs the
blameless voice, tense, numbers, citations, twins, and handles.
[reference/evidence.md](reference/evidence.md) documents the Datadog and Slack
snapshot formats and capture flow. [reference/import.md](reference/import.md)
covers Google Docs conversion and the manual finish checklist.
[reference/components.md](reference/components.md) defines component placement,
options, and Markdown flattening.
