# The writing contract

All drafting and revision in this reference belongs to `gpt-6-astra` at
`xhigh`, including delegated writing passes. Follow the prose routing in
`../SKILL.md`.

A useful retro explains the conditions that produced an incident and the
changes that reduce recurrence. It does not grade the people involved.

## The headline names the failure and the subtitle states the mechanism

Astra writes `meta.title` and `meta.subtitle` through `retro.py prose`.
The headline names the failure. The subtitle states what changed, what
that change caused, and what broke in one sentence. Both must make sense
to someone who was not in the response.

- Bad title: "Invocations stay Pending and schedules stop: restate-worker
  on old image inserts into renamed `workflow_runs` column"
- Good title: "Run creation blocked by a partial deploy"
- Good subtitle: "A migration changed the schema before every reader was
  deployed, so run creation failed."
- Good title: "Checkouts blocked for 94 minutes"
- Good subtitle: "A config gave tenants without a custom limit a burst of
  zero, so checkouts failed for 94 minutes."

The title has at most `DOC_TITLE_WORDS = 8` words and
`DOC_TITLE_CHARS = 60` characters; either excess is an error. It takes no
final period. The subtitle has at most `SUBTITLE_WORDS = 20` words and
`SUBTITLE_CHARS = 120` characters; excess characters are an error and excess
words draw a strict warning. Use no colon or identifier in either field.
Those shapes draw strict warnings too. Put service, image, table, and column
names in a cause, where readers look for that detail.

Use the repeatable `--note "[ADDR=]TEXT"` flag to steer the writing without
writing it. `ADDR=text` directs one field; bare text directs every field
selected for the run. A later note replaces an earlier note for the same
field. The work order marks the note `REQUIRED` and tells Astra that
returning the current wording unchanged does not answer it. Select the
field explicitly when revising it:

```bash
$TOOL prose <dir> --field meta.title \
  --note "meta.title=lead with the disk filling up"
```

In a measured run, this changed "Read failures and resource exhaustion" to
"A full disk, failed reads, and exhausted executors".

For a retro written before 0.3.0, move its old `meta.title` into
`meta.subtitle`, clear `meta.title`, and run
`retro.py prose <dir> --quick`. Astra writes the newly required prose.
The old subtitle's browser-title suffix meaning is gone.
Include a duration only when it helps explain the failure and follows
the recorded endpoints.

Write 2 to 6 distinct lower-case topical tags in `meta.tags`, joining words
with hyphens. Name the system, failure class, and surface, as in `migration`,
`release-pipeline`, and `paging`. Keep team codenames in `meta.teams`; the page
already shows their chips and warns when a tag repeats one. Tags are
operator-chosen data from a controlled vocabulary for filtering, not
writing. `meta.tags` is not a prose field and `retro.py prose` does not
select it.

The slug has the form `<incident date>-<three to six plain words>`, at most 60
characters. Use the words colleagues use to name the incident aloud:
`2026-09-18-schema-ahead-of-deploy`. Do not form it by replacing the title's
spaces.

## Blameless voice

Write about systems, controls, interfaces, defaults, incentives, and missing
feedback loops. Name what the system allowed and why that behavior made sense
at the time.

Prefer statements a reader can test:

- "The deploy pipeline had no canary stage, so the config reached every region
  in one step."
- "The runbook did not identify the rollback command."
- "The monitor averaged over a long window and fired after support received a
  report."

Do not grade a person's care, speed, skill, judgment, or intent. Cut adjectives
such as careless, slow, obvious, negligent, and heroic when they describe a
person. Replace them with the system condition or control and the signal it
produced.

An actor can still matter. State who performed an event so the sequence is
clear, but do not turn that role into the cause. "On-call rolled back the
config" records an action. "On-call failed to notice the obvious problem"
assigns blame and hides the missing signal.

## Tense and actors

Write timeline entries in past tense. Begin with the actor when one is known,
using a role or first name:

- "Support posted the first report."
- "Priya compared the configs."
- "The release bot deployed the new profile."
- "On-call rolled back the config."

Use a stable role when the person's identity adds nothing. Use a first name
when several people shared the same role and the distinction keeps the
timeline clear.

Write causes in present tense because they describe system behavior that
exists until a change removes it: "A missing override falls through to zero."
Describe a retired cause in past tense only when the cause no longer exists
and that distinction matters.

## Numbers come from data

Put incident instants in `timestamps`, timeline events in `timeline[].ts`, and
impact periods in `windows[].start` and `windows[].end`. The renderer derives
time to detect, engage, mitigate, and resolve from those fields. It derives
window durations and monitor latency as well. Action state drives the progress
bar.

Titles, subtitles, and section takeaways are the only exceptions to the body-prose rule
below. Include a duration there only when elapsed time is part of the
conclusion a reader needs without the tiles. Compute it from recorded
endpoints and name the interval. Recheck the value when either endpoint
changes.

Never restate a derived duration in prose. Say what happened between the two
events and let the tiles show the elapsed time. `retro.py check` warns when
prose states a duration near detection, engagement, mitigation, resolution,
onset, a monitor firing, or the all-clear.

Put impact counts in `impact.metrics`. Set `measured: true` for observed counts
and `measured: false` for estimates. Describe the measurement boundary in
`delta`, then cite the cause or window that gives the number context.

## Citations and handles

A citation follows the sentence it supports: `(C1)` or `(C1, T7)`. The
page replaces each id with its handle, while the id remains available in
the interactive card. `retro.py text` still uses local time for timeline
citations.

The sentence must stand on its own after deleting the citation. Write *The
empty override map sets the burst limit to zero (C1).* Do not write *The burst
limit comes from (C1).*

Every window, timeline entry, cause, action, decision, hypothesis, unknown,
and sub-incident carries an `h` short name. So does every notebook, monitor,
build, and PR evidence entry, even one with a `label`. Write a distinct noun
phrase that names the event or mechanism without repeating its sentence.

- Short name: "zero burst default"
- Full wording: "The empty override map sets the burst limit to zero."
- Short name: "first support report"
- Full wording: "Support posted the first checkout failure report."

Use 2 to 6 words, with no trailing period or register id. A nonempty handle
outside that range is an error even without strict mode. Missing handles and
register ids draw strict warnings; trailing periods warn. Name the thing,
not its register type: "root cause decision" does not tell the reader what
happened.

The short name is what closed timeline, cause, decision, and unknown rows
show in place of the sentence. The action table also shows the short name;
Full wording reveals its title and note. Notes use `t` for their closed
heading. Evidence cards still use snapshot names and labels. A short name
must stand on its own before the reader expands it.

Slack rows use participants and mechanically extracted opening words. A
Slack snapshot is verbatim evidence, not authored prose; never pass it
through a language model.

A timeline entry needs `h` whether it has an id. Add a `T#` id only
when another entry cites that event.

## Plain twins

`summary`, `impact`, every cause, `resolution`, and `detection` each carry a
precise `text` and a plain twin in `p`. Most readers see the twin first.

Write each twin in 30 words or fewer. Preserve every fact and number from the
precise wording, use everyday terms, and explain what the entry means to a
reader outside the team. Do not include ids or file paths. Omit issue numbers
and phrases such as `we decided`. A twin adds the consequence instead of
repeating its title.

## Each opening line stands alone

The page shows one line per entry and keeps the details behind a disclosure.
Each opening line must make sense on its own.

A narrative section's `takeaway` states its conclusion in 18 words or fewer
(`TAKEAWAY_WORDS = 18`). It must answer the reader who stops at that sentence.
Write "Rolling back the config stopped checkout errors within a minute,"
not "This section covers the response." The duration follows the title,
subtitle, and takeaway exception in
[Numbers come from data](#numbers-come-from-data).

`REFERENCE_SECTIONS = ("evidence", "glossary", "notes")` carry no takeaway.
Each opens on its heading and collapsed structure. Put conclusions in the
narrative sections that argue them; a reference takeaway draws a strict
warning.

A cause-tree node shows only the short name `h`. State the mechanism in the
plain twin in 25 words or fewer. Its detail row also opens with `h`;
expansion shows the selected plain or full wording, evidence, and code.

Keep a timeline entry's `text` within 25 words. Above 25 draws a strict
warning; above 40 is an error without strict mode. A cause's `text` has at
most 90 words, a decision's `why` 60, and an unknown's `why` 45. These three
limits draw strict warnings; all four body counts exclude URLs and inline
code. Move supporting detail into cited evidence.

Each panel heading in `summary.html` answers its question in words a reader
outside the response understands. The body gives evidence for that answer.
Keep the heading and body together within 35 words (`SUMMARY_PANEL_WORDS = 35`)
and all five panels within 150 (`SUMMARY_BUDGET = 150`). The first heading
must advance the explanation beyond `meta.title`; repeating its wording
draws a strict warning. Once the causes and actions are settled, write the
panels from the completed record.

Mark eight timeline entries or fewer with `key: true`. Choose the events
needed to explain the incident: what changed, when impact began, when anyone
knew, what changed the investigation, the mitigation, the fix. Everything else
stays in the full list, which is one click away and searchable.

Write each `recognize` row for a responder at three in the morning with no
context. Describe the signal visible on screen before explaining what it
means.

Write an `unknown` as a question. Use "Why does the canary region take the
same config push as production?" instead of a topic label such as "canary
investigation."

## Unrecorded facts

Never invent a cause, timestamp, owner, impact, or outcome to complete the
shape. Write "Not recorded" in both `text` and `p` when the sources do not
answer a required narrative. Leave optional arrays empty; the renderer
hides empty lesson columns and unused evidence groups.

Keep uncertainty explicit. Mark a metric as estimated, name the evidence that
supports a likely cause, and leave the status at `draft` while a required fact
remains unresolved.

## Public names

Use codenames in published prose and evidence. Apply them to services and
deployments as well as teams. Remove private names before writing a snapshot,
not only from the rendered sentence. Private names include customers,
companies, workspaces, tenants, and accounts. Notebook titles, log messages,
monitor messages, Slack text, and image captions are all published with the
retro.

Configure `--forbidden-terms`, `FORBIDDEN_TERMS`, or the nearest
`.customer-names` file. `retro.py check` scans `retro.json`, `NOTES.md`, and
text evidence; treat a match as a publishing block.

## Prose gate

Run `retro.py prose <dir>` after assembling the record. It sends the authored
fields listed in [reference/schema.md](schema.md#prose-authored-fields-and-provenance)
through `codex-ask -m astra`, writes accepted wording directly, and records
its hashes in `prose.lock.json`. These fields include the headline and
subtitle, action titles and notes, decision titles and alternatives,
hypothesis titles, and sub-incident titles. `--stale` selects empty headlines
and subtitles, required short names that are absent or empty, and nonempty
fields without matching provenance. It leaves absent
optional prose alone.

The work order names this writing contract and the full rule catalog from
`slop-cop rules --pretty`. Read each rule's description, tip, and
`llmDirective` before the first draft.

The command runs deterministic rules
per accepted field with `slop-cop check --llm-effort=off`. The explicit flag
matters: without it, slop-cop enables its model pass when the Codex CLI is
on `PATH`. The model lint pass runs once per batch over the fields joined
with delimiters. Findings return to their fields by character offset.

The command sends violations to Astra with the rule id, matched text, the
rule's directive, and the suggested change.
`SLOP_ROUNDS = 2` allows two revision rounds per batch. The same model writes
and revises the text within the subprocess pipeline, re-asking only the
fields the lint flagged.

In a measured 12-field batch, deterministic lint took 0.26 seconds and the
batched model pass took 4.32 seconds, with identical findings to the earlier
roughly 40-second run. Per-field lint with the automatically enabled model
pass took 3.77 seconds; deterministic lint alone took 0.02 seconds.

The lock records each field's remaining `slop` count and their total.
`check --strict` fails above `SLOP_BUDGET = 3`, naming the fields with the most
findings.
This gate covers the landed prose fields; the rendered document still needs
the separate check below. Inspect the refused-field and lint reports.
Do not hand-edit accepted text: `check --strict` rejects a changed field
until `retro.py prose <dir> --field <address>` writes it again.

Preserve numbers, times, identifiers, URLs, code-span contents, names,
citations, and footnotes in fields other than the headline and subtitle.
Backticks are markup: the fact
freeze strips them before tokenizing, so adding a code span around 8 GiB
does not change its facts. The fact freeze protects identifiers containing
underscores, such as `manifest_section`, with or without backticks. The
command still refuses a reply that drops the identifier or changes a number.
It checks the tokens it recognizes; review the meaning too. Fields outside its enumeration
still follow this writing contract and the Astra routing in `SKILL.md`.

For the headline and subtitle, the fact freeze checks grounding instead of
requiring every fact from the previous text to survive compression.
Grounding is the other field plus `summary.text` and `summary.p`.
`over_budget()` decides whether the current text also belongs: within both
budgets, it stays so a run can re-derive provenance; over either budget, it
is excluded. Astra may omit facts when shortening these fields but may
invent none. Review the meaning as well as the protected tokens.

Run the generated Markdown through the prose gate after the structural edit
and again after the final tone edit:

```bash
$TOOL text <dir> | slop-cop check - --lang=markdown --llm-effort=off
```

Fix genuine findings. Keep only constructions that carry necessary technical
meaning, such as a field-list colon. Parenthetical asides and ellipses do not
carry incident facts; rewrite them directly.

### Migrate prose written before 0.3.0

Version 0.3.0 required a compact title but gave Astra no way to write it:
`targets()` omitted `meta.title`, so `--field meta.title` returned
`not a prose field`. Migration left an empty required title and a strict error
that an operator could not legitimately fix by hand. Version 0.3.1 makes
the headline and subtitle prose fields.

Move the old `meta.title` into `meta.subtitle`, replacing the browser-title
suffix, then clear `meta.title` and supply `meta.tags`. Run
`retro.py prose <dir> --quick` for the initial migration. To write one field
on demand, use `--field`, as in `prose --field meta.title`.

`prose --field meta.title` wrote a 5-word, 37-character headline in
71 seconds with zero lint findings on a copy of a real retro whose title
the rollout had left empty.

`--quick` asks Astra for empty or over-budget headlines and subtitles,
missing short names, the five summary panels, narrative section takeaways at
`TAKEAWAY_WORDS = 18`, and timeline or cause text over 25 or 90 words.
Fields with matching hashes stay as they are. The fact freeze remains on;
only deterministic lint runs. Prepare the panel containers and section
objects before running the command.

Other nonempty pre-existing prose without a lock entry is pinned
with `"kind": "legacy"`, its `sha256`, and a `grandfathered` stamp naming
the plugin version. These hashes satisfy the strict provenance gate for
eligible retros. A later edit breaks the hash; send that field through
`prose --field` without `--quick`. Use the full command for new writing.

`check` refuses any legacy provenance when the onset date, falling back to
`meta.date`, is after `LEGACY_CUTOFF = "2026-09-19"`. The restriction applies
without `--strict` and directs the author to run `prose` without `--quick`.
The date gate keeps the migration path from skipping Astra on later incidents.
The gate checks the incident date, not the writing date. Another `--quick`
run preserves existing lock entries, including stale hashes; it cannot
re-pin an edited field. The [migration reference](schema.md#--quick-migrate-a-retro-written-before-030)
states those enforcement limits; `--quick` is not a general escape hatch.
