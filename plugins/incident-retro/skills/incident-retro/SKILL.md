---
name: incident-retro
description: Build a blameless incident retrospective from structured facts and committed evidence snapshots, then render the same record as HTML, Markdown, and PDF. Use when asked to "write a retro", create an "incident retrospective" or "postmortem", "import this Google Doc postmortem", run a "blameless review", or turn an incident timeline and its evidence into a reviewable record.
allowed-tools: Bash(python3:*, ls:*, cat:*, pdftoppm:*, wrangler:*, npm:*, open:*, wlm:*, slop-cop:*, uvx:*, ssh:*, rsync:*, cc-present:*), Bash(aws:*), Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# incident-retro

While `meta.status` is `ongoing`, the live commands derive the record from
incident state without producing LLM-authored prose. After `live finalize`,
Claude Opus 5.5 (`claude-opus-5-5`) writes and revises all new prose,
including headlines, subtitles, summaries, plain twins, handles, revision
notes, and publication text. Use `retro.py prose` for its enumerated fields
in `retro.json` and `summary.html`; it calls `claude -p --model
claude-opus-5-5 --json-schema` and records their provenance in
`prose.lock.json`. `check` accepts every writer model a lock records, so a
lock written by an earlier model stays valid as history. For a retro written
before 0.3.0, use the `--quick` migration below to retain eligible existing
prose.

For prose outside that field list, a session on another model delegates the
writing to an Opus 5.5 author (`Agent` with `model: opus`); it collects
evidence and publishes the result itself. Tags remain operator-chosen data: they are a
controlled vocabulary for filtering, not writing. `meta.tags` is outside
the prose field list.

An incident retro pairs one canonical `retro.json` with committed evidence snapshots and an executive summary in `summary.html`. `incident-retro.html` renders that record, and `retro.py` derives every duration from timestamp fields. Write in a blameless voice that explains what the system allowed, not which person deserves blame.

The page is for readers who did not take part in the response. Beneath the title, subtitle, tags, and status, it opens with the status strip, executive summary, What's changed, timeline, and causes, then impact, resolution, lessons, recognition, Action items, Remediation, Prevention options, evidence, and the remaining sections. Narrative sections start with a `takeaway` of 18 words or fewer, set by `TAKEAWAY_WORDS = 18`, and keep the details behind a disclosure.

`REFERENCE_SECTIONS = ("evidence", "glossary", "notes")` open on their heading and collapsed structure, with no takeaway. Conclusions belong in the sections that argue them. Closed timeline, cause, decision, and unknown rows show a short name `h`; their sentences appear on expansion. Each short name and section opener must make sense on its own.

Use one driver for every mechanical step:

```bash
TOOL="python3 ${CLAUDE_PLUGIN_ROOT}/skills/incident-retro/scripts/retro.py"
```

The AWS Systems Manager Parameter Store paths come from the caller. The AWS profile and region do too; the plugin supplies no credential path or cloud default.

Read [reference/writing.md](reference/writing.md) before Draft, [reference/evidence.md](reference/evidence.md) before Gather, and [reference/schema.md](reference/schema.md) whenever a field is unclear.

## Fast path to the rendered retro

The target is a final retro in under 30 minutes. In the measured replay,
importing 84 timeline rows from three sources takes 4 seconds. One model call
(measured on GPT-6 Astra) wrote all 258 prose fields in 526 seconds (about 9 minutes), followed by a
137-second lint round for one field, with no refused fields. The `publish`
gates take 15 seconds, and the final ready retro takes 17 minutes from the
order. The replay uses a prepared facts script; authoring those facts live
adds time. These timings do not measure the merge and Pages deployment wait.

A retro PR is never draft. A PR link is never posted. The final output is
the rendered page URL from the successful `publish` command's `RENDERED:` line.

1. Create the retro branch's worktree and run `new` there:

   ```bash
   $TOOL new --incident "<source>" --docs "<design-docs-checkout>" \
     --title "<working headline>" --date YYYY-MM-DD \
     --tags "migration,release-pipeline" --team "CODENAME=alias,alias" \
     --since 8h --tz America/Los_Angeles
   ```

   Repeat `--incident` for each source and `--team` for each codename.
   A source is an incident directory, `cci:<regex>`, or a cc-notes id for
   a note, answer, investigation, or log. A directory's `state.json` is
   rebuilt as live sync does; each `*.md` file contributes time-led bullets
   such as `- 9:18 PM: ...`. `cci:` queries once per record kind because
   each reply is capped at 16000 bytes, newest first. A full page prints a
   warning; narrow the pattern or `--since` when records are missing.

   The command lifts links into `refs`, replaces aliases with codenames,
   stores times with the display zone's offset, and guesses event kinds
   from markers such as `FIX-LIVE`, `INCIDENT`, `MECHANISM`, `OPENED`, and
   release builds. It stamps detected, engaged, and mitigated times from
   the first matching rows, marks up to eight alert, mitigation, or deploy
   rows as key, and adds a Sources list to `NOTES.md`. Review those guesses.

2. Fill the facts from the records only. Prune the scaffolded timeline to
   the rows the story needs and correct their text. Fill timestamps,
   windows, causes, impact, resolution, detection, decisions, actions,
   lessons, `recognize`, `prevention`, section `takeaway` fields, and `summary.html`.
   Leave `h` and `p` empty; Opus writes them from the entry. Never grep
   transcripts or re-derive a fact a record already holds. Put a fact no
   record carries in `unknowns`.
3. Run `$TOOL board <dir> --out <board.json>`. The root presents the file
   to the owner through cc-present without rewriting it. Record the owner's
   choices in `prevention[].options[]` with `picked: true`, `owner`, and
   PR `links` or a `lane` named in `remediation.lanes`. Fill `remediation.done`
   with what stops the incident and `remediation.lanes` with the follow-up
   lanes carrying the picks. The initial retro PR includes this Remediation
   section and the owner's picks.
4. Finish every fact, then run one `$TOOL prose <dir> --detach` and await it with the printed
   `AWAIT:` command. The default sends every field in one call. Fields
   reach disk when the run ends. A later fact edit sends the affected fields
   back through Opus.
5. Run `$TOOL publish <dir>`. It runs the gates before pushing, refreshes
   both cards, opens or updates a ready PR, merges it once it is clean and green with the
   merge method the repository allows, and waits for a successful Pages
   deployment containing the merge commit. If it exits 75, resume with the printed `AWAIT:` command until
   it succeeds or reports a failure. Phase 5 gives the wait and exit rules.
6. Return only the URL from the final `RENDERED:` line. Every Slack or comms
   draft about the retro passes `$TOOL comms-check <draft-file|-> --url <rendered-url>`
   before posting. It rejects GitHub or Graphite PR links and a missing
   rendered URL. Comms receives only the rendered page URL.

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
directory and at the same URL. Opus writes all prose from this point on,
using `retro.py prose` for its enumerated fields. The published draft no
longer polls the live branch.

An unattended updater adds `--push` to finalize. Nobody reloads the merged
shell, so the draft goes to `live/<slug>` through the same gate as `sync`. A
page still open adopts the draft and stops polling.

The sticky time scrubber under the title block shows the page as of a chosen
instant. Drag its playhead, step between events with the left and right
arrows, or select Live/Now to reset. Link to an instant with `?at=<iso>`.
The existing `?since=` revision-diff parameter is separate and unchanged.

Actions and hypotheses replay only with `history`; causes appear at their
`identifiedAt`. Without those fields, entries show their current state with
a muted "no time data" mark. See [reference/components.md](reference/components.md#the-time-scrubber).

## What the agent writes after all-clear

`retro.py` scaffolds, writes prose, validates, renders, snapshots, and fetches Datadog evidence. The authoring agent assembles the incident record and Slack snapshots, then runs the prose command:

- Complete the scaffolded `retro.json` from the records. Do not infer a missing event, cause, owner, or outcome.
- Run `prose` to have Opus write `meta.title` as a headline within `DOC_TITLE_WORDS = 8` words and `DOC_TITLE_CHARS = 60` characters, with no final period. Opus writes `meta.subtitle` as a causal sentence within `SUBTITLE_WORDS = 20` words and `SUBTITLE_CHARS = 120` characters. Use no colon or identifier in either. Set `meta.slug` to the incident date plus three to six plain words. The rule and examples are in [reference/writing.md](reference/writing.md).
- Add 2 to 6 distinct topical `meta.tags`, such as `migration`, `release-pipeline`, and `paging`. Keep team codenames in `meta.teams`.
- For a retro written before 0.3.0, move the old `meta.title` into `meta.subtitle`, clear `meta.title`, and run `prose --quick` to write the newly required prose through Opus. Supply the tags yourself. Replace the old subtitle's browser-title suffix value.
- Once the causes and actions are settled, prepare one `summary.html` panel per question and run its wording through `prose`.
- Fetch only Slack snapshots missing from the records with the agent's own Slack tooling. Save the resulting `ir.slack/1` files under `evidence/slack/`, then register each file in `evidence.slack[]`.
- Write every timestamp as ISO 8601 with a UTC offset. `meta.timezone` controls display only.
- Leave each plain twin `p` empty for Opus to write in 30 words or fewer. The twin keeps every fact from the precise wording, with no register id or file path.
- Leave `h` empty for Opus on every window, timeline entry, cause, action, decision, hypothesis, prevention question, unknown, sub-incident, and notebook, monitor, build, or PR evidence entry. Each needs a distinct noun phrase of two to six words. A nonempty handle outside that range errors even without strict mode. Missing handles and register ids draw strict warnings; trailing periods warn. Evidence labels do not waive the handle requirement.
- Keep Slack snapshots as verbatim evidence; never pass them through a language model. The renderer derives their row labels mechanically.
- Use deployment or service codenames in public prose. Never publish the name of a customer, company, workspace, or account.

## Phase 1: Gather

After `live finalize`, use the records in the existing retro directory.
For a retro that did not start live, use `new` from the fast path.

For evidence absent from the records, collect permalinks to incident-channel messages, Datadog notebooks and monitors, pull requests, issues, builds, runs, and images. Prefer a permalink to a pasted claim because the rendered retro can connect the claim to its source.

Fetch missing Datadog snapshots. Repeat `--notebook` and `--monitor` as needed.

```bash
$TOOL evidence fetch <dir> --notebook <id-or-url> --monitor <id-or-url> \
  --from-ssm --ssm-api-key-path <api-key-path> \
  --ssm-app-key-path <app-key-path> --aws-profile <profile> \
  --aws-region <region>
```

For each missing Slack snapshot, generate a skeleton from its permalink, fill `messages[]` from the agent's Slack results, record any removals in `redactions[]`, register the file, and validate the set.

```bash
$TOOL evidence slack new --permalink <url> --channel-name <name> [--thread]
$TOOL evidence slack check <dir>
```

Copy publishable screenshots into `evidence/images/`. Give every image useful alt text and a caption backed by a cause or window.

## Phase 2: Draft

Complete `timestamps` from the records first. The opening tiles depend on `onset`, `detected`, `engaged`, `mitigated`, `resolved`, and `allClear`, and the renderer computes their durations.

Then draft in reading order:

1. Add `windows` for distinct periods of outage or degradation, including partial impact.
2. Prune the scaffolded `timeline` to the rows the story needs, keep timestamp order, and correct the event text within 25 words. Leave each `h` empty for Opus. Check actors, `refs`, and guessed kinds against the records. Keep eight or fewer entries needed to explain the incident marked `key: true`.
3. Separate the trigger and root cause from contributing causes. Attach the evidence that supports each claim.
4. State `impact` through observed effects and measured or estimated metrics.
5. Describe `resolution` and `detection`, including monitors that caught or missed the incident and monitors added afterward. Record the `decisions` made during the response, who made each, and why. Record the `hypotheses` ruled out and the evidence that cleared them.
6. Fill every applicable `lessons` group from the evidence, then write the `recognize` rows for the next responder.
7. Give every action an owner, source, state, and due date when one exists. Fill `prevention` with the questions the owner needs to settle, the facts that pose them, and two to four options per question. Record each option's pros, cons, buys, costs, losses, prerequisites, and alternatives before prose.
8. Run `board --out`, present the board to the owner, and record the picks before prose. Each picked option carries `picked: true`, an `owner`, and PR `links` or a `lane` matching a name in `remediation.lanes`.
9. Fill `remediation.done` with `{text, links?}` entries describing what stops the incident and `remediation.lanes` with `{name, text, links?}` entries for the follow-up lanes. Opus writes `remediation.done[i].text` and `remediation.lanes[i].text`. `check` treats `done`, `lanes`, `prevention`, and picks as optional at every status. Each pick that is present requires an owner and a PR link or a named lane present in `remediation.lanes`.
10. Record each question the sources leave unanswered in `unknowns`. Define terms with a meaning specific to this system in `glossary`.
11. Leave plain twins and handles empty for Opus to write from each entry. Fill one `takeaway` of 18 words or fewer per narrative section in `meta.sections`; omit it from `evidence`, `glossary`, and `notes`.
12. Prepare `summary.html` last with one panel per question, in the order `what-happened`, `impact`, `why`, `what-changed`, `still-open`. Each panel has one `h3.xs-head` of at most 14 words, a `ul.xs-points` with at most 3 `li` of at most 18 words each, and an optional `.xs-stats` block. Give the first heading an answer beyond the headline.
13. Finish every fact, then run `prose --list` to inspect the field addresses and one `prose --detach` to write them through Opus. The default sends all fields in one call. `--batch N` splits them into calls that run side by side.
14. Run the printed `AWAIT:` command in the foreground with `timeout: 600000`. If it exits 75 after 540 seconds, rerun it until it returns the run's exit status. Fields are written to disk after all calls finish.

Review refused fields and lint findings, and rerun affected addresses with
`--field`. A fact edited after prose also needs its affected fields rewritten.
Keep `prose.lock.json` beside the record.

> Never background `prose` or wait on a `Monitor`: neither notification wakes an idle in-process teammate.

Follow [reference/writing.md](reference/writing.md). Mark a required answer as not recorded when the sources do not provide it. Never invent connective events to make the story read more smoothly.

```bash
$TOOL prose <dir> --list
$TOOL prose <dir> --detach
$TOOL prose <dir> --await
$TOOL prose <dir> --field C1.text --field C1.p
$TOOL prose <dir> --field meta.title \
  --note "meta.title=lead with the disk filling up"
```

`--note "[ADDR=]TEXT"` is repeatable. `ADDR=text` steers one field; bare text
steers every selected field. A later note replaces an earlier note for the
same field. Notes direct the writing without supplying it and do not change
field selection. The work order marks each note `REQUIRED` and tells Opus
that returning the current text unchanged does not answer it. The disk
note changed "Read failures and resource exhaustion" to "A full disk,
failed reads, and exhausted executors" in a measured run.

`--stale` selects nonempty fields without matching provenance, empty
headlines and subtitles, and required short names `h` that are absent or
empty. It leaves absent optional prose alone. The field list includes
`meta.title` as `headline`, `meta.subtitle`
as `subtitle`, action titles and notes, decision titles and alternatives,
hypothesis titles, and sub-incident titles.

`PROSE_BATCH = 0` sends every selected field in one call by default.
`--batch N` splits the fields into calls that run side by side; the command
writes accepted fields to disk when all calls finish.

`--timeout` defaults to `PROSE_TIMEOUT = 1800` seconds per call.
`--dry-run` prints the first call's
work order without calling the model. The field list and token-preservation
limits are in
[reference/schema.md](reference/schema.md#prose-authored-fields-and-provenance).
The command writes accepted fields even when it refuses others, so inspect
the report before continuing. After a later edit, rerun the affected field
through `prose`; a changed hash fails `check --strict`.

The work order names the writing contract and the full rule catalog from
`slop-cop rules --pretty`, so Opus writes to the rules in the first draft.

The command runs deterministic lint per accepted reply field with
`--llm-effort=off`; omitting the flag lets slop-cop enable its model pass
when the Codex CLI is on `PATH`. Model lint runs once per batch over fields
joined with delimiters, and character offsets attribute each finding to
its field. The command returns the rule id, matched text, directive, and
suggested change to Opus. It allows two revision rounds per batch
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
Opus may drop facts to shorten these fields but may invent none.

Work orders, schemas, and the rule catalog live under
`~/.cache/incident-retro/prose/<slug>/`, keyed by `meta.slug`. Replies and
logs (`reply.jsonl`, the `claude -p` event stream) stay in each round's
directory under that lane; the lock records their paths. `prose.lock.json` stays beside `retro.json`; `.prose.lock` and
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
Astra, the writer then, could write the title, so the command provided no way to satisfy the
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

`--quick` asks Opus only for empty or over-budget headlines and subtitles,
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

`publish` runs `check --strict`, `render-check`, and a whole-page slop-cop
count with `--llm-effort=off` before refreshing the cards, committing, or
pushing. It prints the check timings and lint count. A failed check exits 1
and pushes nothing. Use `publish` for these checks once the owner's picks
and Remediation are recorded and the prose pass finishes.

Generate the PDF separately:

```bash
$TOOL pdf <dir>
```

Fix every structural error. Triage every prose finding against [reference/writing.md](reference/writing.md). Inspect the generated PDF; file existence does not prove that its layout and charts read correctly across page breaks.

`--strict` enforces the headline, subtitle, tags, slug, short names, twins, takeaways, and key-moment rules. Narrative takeaways have at most 18 words; `evidence`, `glossary`, and `notes` carry none. The word limits are 25 for timeline text, 90 for cause text, 60 for decision reasoning, and 45 for unknown reasoning. Each `summary.html` panel has one `h3.xs-head` of at most 14 words, a `ul.xs-points` with at most 3 `li` of at most 18 words each, and an optional `.xs-stats` block. Strict mode also requires a matching `prose.lock.json` digest for every nonempty enumerated prose field and rejects missing short names.

The lock records per-field `slop` counts and a total; strict mode sums those
counts and fails above `SLOP_BUDGET = 3`, naming the fields with the most
findings. The prose gate covers the landed prose fields, not the whole rendered
document. Revise failed prose through `prose --field`, then rerun
`publish`.

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

The initial retro PR includes what stops the incident, the owner's prevention
picks with owners and PR links or named follow-up lanes, and the follow-up
lanes themselves. Complete the board and Remediation in Phase 2 before
the prose pass and publication.

```bash
$TOOL publish <dir>
```

After the Phase 4 gates pass, `publish` refreshes both index cards from
`retro.json`, using `meta.title`, `meta.subtitle`, `meta.date`, and
`meta.status`. It commits only the retro directory and the two index pages
as `incident retros: 📝 <meta.title>`, then pushes. It opens a ready PR or
edits the existing PR and marks it ready.
The PR body contains the summary panels rendered as Markdown with Opus's
text verbatim, plus the page URL from `CNAME`.

`publish` merges the PR itself, with the merge method the repository allows,
as soon as it is clean and every check is green. It then waits for a
successful `github-pages` deployment to contain the merge commit. The site requires GitHub sign-in,
so the deployment record proves that the merged revision is served; an
anonymous fetch does not. On success, the last line is
`RENDERED: https://<CNAME>/incident-retros/<slug>/`, and the command exits 0.
For Forge-AI/design-docs, that page is on docs.poetic.design.

A failed check or a PR closed without merging exits 1. After `--seconds`
(default 540), an unfinished wait exits 75 with an `AWAIT:` command. Run
that command to resume the wait:

```bash
$TOOL publish <dir> --await <pr-url> --seconds 540
```

The PR URL in `AWAIT:` is only an input to the wait command. Return only
the URL from `RENDERED:` as the final output and hand that URL to comms.
Every Slack or comms draft about the retro passes this check before posting:

```bash
$TOOL comms-check <draft-file> --url <rendered-url>
```

Use `-` for a draft on stdin. `comms-check` exits 1 if the draft contains
any `github.com/<owner>/<repo>/pull/<n>` or Graphite PR link, or omits the
rendered URL. Fix the draft and rerun the check before posting.

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

Read the Import report in `NOTES.md`. Review every timestamp conversion, kind guess, actor, link classification, image, and unplaced block. The importer puts the document's own heading into `meta.subtitle` and leaves `meta.title` and `meta.tags` empty, recording that work in the import notes. Leave the title empty and run `prose --field meta.title --field meta.subtitle` to write both through Opus. Supply topical tags yourself. Handles and twins also need completion through `prose`; collect the referenced evidence before continuing at Draft.

## Reference files

[reference/schema.md](reference/schema.md) defines the `retro.json` fields and
validator behavior. [reference/writing.md](reference/writing.md) governs the
blameless voice, tense, numbers, citations, twins, and handles.
[reference/evidence.md](reference/evidence.md) documents the Datadog and Slack
snapshot formats and capture flow. [reference/import.md](reference/import.md)
covers Google Docs conversion and the manual finish checklist.
[reference/components.md](reference/components.md) defines component placement,
options, and Markdown flattening.
