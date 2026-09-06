# Import a Google Docs postmortem

`retro.py import-gdoc` turns the Markdown export of a Google Docs postmortem
into a draft `retro.json`, the document's images under `evidence/images/`,
and an "Import report" section in `NOTES.md`. The draft is where the Draft
pass in `SKILL.md` starts, not a finished retro. Handles and plain twins are
empty. The report lists every time
conversion and kind guess for review, and quotes every block the converter
could not place.

```
retro.py import-gdoc <exported.md> [<docs.json>] --out <dir> [--tz America/Los_Angeles] [--date YYYY-MM-DD]
```

- `exported.md` is the `text/markdown` export of the document.
- `docs.json` is the optional Docs API document (`documents.get`); it
  supplies the title when the export has no H1, the authors when the export
  names none, and the document and revision ids for the report.
- `--out` is the retro directory. In a scaffolded directory the converter
  overwrites `retro.json`, replaces or appends the `## Import report`
  section of `NOTES.md`, and writes images to `evidence/images/`. It creates
  a missing directory with the same layout.
- `--tz` is the zone for every time that carries no `Z`, `UTC`, `PT`, `PDT`,
  `PST`, `ET`, `EDT` or `EST` suffix. Every timestamp is written in this
  zone, including those with an explicit suffix.
- `--date` is used when the document states no date anywhere.

The same conversion runs standalone as `python3 retro_import.py import-gdoc …`,
and `python3 retro_import.py --selftest` converts the fixture in
`fixtures/gdoc-export.md` and diffs the result against `fixtures/expected/`.

## Headings

The converter normalises a heading before lookup. It removes and remembers
parentheticals, then lowercases the rest and strips it to words. `### Summary
(Multiple incidents)` looks up `summary`; `## Action Items (Linear tickets)`
looks up `action items`. Heading levels do not matter, and the order does
not matter either; each heading's content runs to the next heading of any
level.

| Normalised heading | Destination |
|---|---|
| summary, tl dr, tldr, overview, what happened, executive summary, description | `summary.text` |
| timeline, incident timeline, sequence of events, chronology | `timeline[]`, `windows[]` |
| impact, customer impact, blast radius, user impact | `impact.text` |
| root cause, root causes, confirmed root cause, causes, cause, the bug, root cause analysis, rca | `causes[]` kind `root` |
| contributing factors, contributing factor, contributing causes, contributing cause | `causes[]` kind `contributing` |
| trigger, triggers, what triggered it | `causes[]` kind `trigger` |
| resolution, remediation, recovery, fix, the fix, mitigation | `resolution.text`, `resolution.links` |
| detection, how we found out, how it was detected, how we detected it | `detection.text`, `detection.monitors[]` |
| action items, actions, follow ups, follow up, next steps, remediation items | `actions[]` |
| lessons learned, lessons, learnings, takeaways | `lessons.*` by bold lead-in; the rest to `notes[]` |
| what went well, went well, what worked | `lessons.well` |
| what went wrong, went wrong, what didnt go well, what did not go well, what could have gone better | `lessons.wrong` |
| where we got lucky, lucky, where we were lucky | `lessons.lucky` |
| supporting information, supporting info, references, links, appendix, evidence, resources, artifacts | `evidence.*` by host; text without links to `notes[]` |
| anything else | `notes[]` entry titled by the heading |

An empty heading (`### ` with no text) is skipped and its content is
reported. A document with no H1 keeps its opening paragraphs as the summary
when no summary heading exists.

## Header fields

The header is everything between the H1 and the first other heading.

| Field | Rule |
|---|---|
| `meta.title` | The H1, else the Docs API title. A leading `[YYYY-MM-DD]`, a trailing `(#N)`, `#N`, `(incident N)`, `(#)` or `RCA` is stripped. |
| `meta.date` | The first date in the `Date:` field; else the title's bracket date; else `--date`; else the first timeline entry's date. `Date: Date` is the template placeholder and counts as no date. |
| `meta.status` | The last of the `Date: … Status:` header line and any bold `**Status:**` line wins. `resolved` or `closed` gives `resolved`; `reviewed` gives `reviewed`; `in review` gives `in-review`; `in progress`, `investigating`, `triage`, `monitoring`, `open` and `draft` give `draft`; no field gives `draft`. |
| `meta.incident.number` | `(#N)`, `#N` or `(incident N)` in the title; else `#N`, `(incident N)` or `INC-N` in the header. |
| `meta.incident.severity` | `sev-?N` anywhere in the header, including inside a link such as a Linear issue URL; that link becomes `severityLink`. |
| `meta.authors` | The `mailto:` link texts in the `Authors:` field, deduplicated; else plain names there, where `Person` is the template placeholder and counts as none; else Docs API `person` mentions. Names only, never addresses. |
| `meta.attendees` | The `mailto:` link texts of the `Attendees:` paragraph. |
| `meta.repo` | Set when every pull request link names one `owner/repo`. |
| `meta.subIncidents` | A `Summary (Multiple incidents)` list: one `I#` per item, a leading `[Tag]` becoming `Tag: `. Elsewhere a `[N]` line prefix attaches `incident: "IN"` to a cause or window and becomes `(IN)` in prose. |

The report lists every header line that is not a field or a link as
unplaced. The converter replaces every `mailto:` link with its text.

## Timeline

The converter reads every table and list under a timeline heading row by row.

- A table's time column is the one headed `time`, `when`, `timestamp` or
  `ts`, else the first; the event column is headed `event`, `what`,
  `description`, `note` or `details`, else the second. A table headed
  `start` and `end` yields windows instead of entries.
- A bullet or paragraph line starts with the time: `10:29 text`,
  `- **04:52Z** — text`, `~3:20 text`, `8:10–8:13 AM — text`. A range
  contributes its start, and the end's `AM` or `PM` applies to a bare start.
- Times need minutes or an `am`/`pm` marker. A zone suffix overrides
  `--tz` for parsing; the converter still writes the result in `--tz`.
- The running date starts at `meta.date` and changes at a day header: a row
  or line that is only a date, such as `| 2026-07-10 | |` or `**July 6**`, a
  date in the time cell, or a zone hint such as `All times Pacific,
  2026-09-02`. A date inside an event's text never moves it.
- A time without `am` or `pm` that runs backwards reads as PM when that
  restores order, so `12:48` then `2:00` reads as 14:00; the report says so.
- A line reading `from 9:26am to 10:23am`, `between 10:12 and 10:53`,
  `[1] ~July 6 1:20pm to 6:30pm` or `24th, from 8:58am to 9:05am` becomes a
  `windows[]` entry of kind `outage`; the same pattern is also read under
  summary and impact headings when the line carries an outage word. A
  window that repeats an earlier one's span is skipped.
- `kind` comes from the first rule below that matches the event text, else
  `hypothesis`.

| Kind | Keywords |
|---|---|
| `allclear` | all-clear, all clear |
| `resolution` | resolved, recover, recovery, full health, healthy again, back to normal, stop, stopped |
| `mitigation` | rolled back, rollback, revert, disabled, scaled up/down/out, pinned, hotfix, restart, mitigation, freeze, froze, unblock |
| `deploy` | deploy, release, rolled onto, build N, merged, shipped |
| `alert` | alert, monitor … fired, paged, page, escalation, fires |
| `report` | report, noticed, customer, flags, flagged, observed, complaint, support ticket |
| `hypothesis` | suspect, hypothesis, likely, might, could be, theory, guess |
| `action` | acknowledged, approved, opened, investigates, identified, confirmed, begins, asks, posts, labeled, notes, checking, working, picks up, kicks off |

- `actor` is the leading name when a reporting verb follows it, as in `Alex
  reported`, `amb confirms` or `acknowledged by jrpoirier`. Common sentence
  openers such as `Investigation`, `Rollback` or `Monitor` are never actors.
- `refs` holds every link in the row; a pull request is typed
  `{"url", "kind": "pr"}`.
- The converter sorts entries by instant. An entry before
  `timestamps.onset` gets `phase: before`; one after `allClear`, or
  `resolved` when there is no all-clear, gets `phase: after`.
- The converter infers `timestamps` and reports each source. `onset` comes
  from the earliest window, else from the first `deploy` entry when it
  precedes the first alert or report; `detected` from the first `alert` or
  `report`; `resolved` from the first `resolution`; `allClear` from the
  first `allclear`. It drops a value that runs backwards and leaves
  `engaged` and `mitigated` as `null`.

Lines with no leading time are reported verbatim; a pasted Slack transcript
under the timeline heading lands there whole.

## Action items

Tables and lists under an action heading both become `actions[]`, each with
`source: "review"` and an empty `h`.

- A table's item column is headed `item`, `action`, `task`, `what` or
  `description`, else the first; `state`, `status` or `progress` names the
  state column; `owner`, `who`, `assignee` or `dri` names the owner column.
  A `source` column is who raised the item and is not the owner.
- `state` is `done` for `[x]`, `~~struck~~`, `✅`, `:white_check_mark:` or a
  state cell reading done, completed, merged, created, shipped, resolved or
  fixed. It is `in-progress` for in progress, work in progress, ongoing, in
  review, raised or started, `dropped` for dropped or canceled, and `todo`
  otherwise.
- `owner` is the first of: a `[Name](mailto:)` link in the row, the owner
  cell, `Owner: Name`, `@Name`, a trailing `(Name)`, a trailing `[Name]`.
  Nested `- Owner:` and `- Deadline:` bullets fill `owner` and `due`; other
  nested checklist items become their own actions.
- A `[p0]`-style priority prefix is dropped from the title and noted in the
  report. An item that is only a link takes its title from the Linear
  slug.
- Links carry pull requests as `kind: pr`, Linear issues as `kind: doc`
  labeled with the issue key, and Buildkite and other pages as `kind: doc`.

Paragraphs under an action heading are reported as unplaced.

## Causes and lessons

- A cause section that is a list yields one cause per item; a bold lead-in
  such as `**No canary:**` is the title and the rest is the text. Paragraphs
  yield one cause whose title is the first sentence; retitle it. A `[N]`
  prefix yields one cause per line with `incident: "IN"`.
- A fenced code block under a cause heading becomes that cause's `code`,
  with `lang` defaulting to `text`; a second fence, or a fence under any other
  heading, goes to `notes[]`. Block quotes and tables stay as Markdown in
  the cause text.
- Notebook, monitor, Slack, and Sentry links in a cause fill its `evidence`;
  pull requests and other pages fill its `links`.
- Lessons come from list items and paragraph lines, one lesson each. A bold
  lead-in matching a lessons heading, such as `**What went wrong:**`,
  switches the bucket; lessons under no bucket go to a `notes[]` entry.

## Links and images

The converter classifies every `https://` link in the document by host and
registers it once in `evidence.*`; the report lists each occurrence.

| URL | Register | Notes |
|---|---|---|
| `github.com/<o>/<r>/pull/<n>` | `prs` | `role` by section: `cause` under causes, `fix` under resolution and actions, `monitor` under detection, else `followup`; a label starting with fix, cause or monitor overrides; the strongest role wins when a PR recurs. |
| `app.graphite.com/github/pr/<o>/<r>/<n>` | `prs` | Rewritten to the GitHub URL; the Graphite URL is kept as `alt`. |
| `app.datadoghq.com/notebook/<id>` | `notebooks` | `file` is left for `evidence fetch`. |
| `app.datadoghq.com/monitors/<id>` | `monitors` | Under detection also `detection.monitors[]` with `role: caught`. |
| `<ws>.slack.com/archives/<C>/p<ts>` | `slack` | No snapshot yet; `check` warns until `evidence slack new` runs. |
| `*.sentry.io/…` | `sentry` | |
| `linear.app/<ws>/issue/<KEY>-<n>/…` | `linear` | `key` from the URL. |
| `buildkite.com/…` | `builds` | |
| everything else, including Datadog dashboards | `docs` | |

A link's label is its link text when that text is not the URL itself, else
the list item's text around it, as in `Investigation notebook: <url>`. The
report lists links that are not `https://` and the converter skips them.

Images arrive as `![alt][imageN]` references with `[imageN]: <data:…>`
definitions at the end of the export. Each referenced image is decoded to
`evidence/images/image-N.<ext>` and registered in `evidence.images[]` with
`alt` from its own alt text, else the text around it, else the section
heading; `caption` and `cites` are empty. An image whose alt is an emoji
name such as `:white_check_mark:` is a pasted Slack reaction: it stays as
text and is not extracted.

## The import report

`NOTES.md` gains a `## Import report` section, replaced on re-import. The
section carries these parts.

- Header. Where the title, date, status, incident number, severity,
  authors, and attendees came from; sub-incidents; repaired encoding; the
  zone every bare time was read in.
- Headings. Every heading in document order with its level and
  destination.
- Timeline. Every row with its source text, the timestamp it became,
  whether the zone was assumed or explicit, the kind and the keyword that
  chose it, and the actor.
- Windows and Timestamps. Each inferred value and its source.
- Actions. Each action with the reason for its state and owner.
- Links. Every occurrence with its class, section, and label or role.
- Images. Each file and where its alt came from; skipped emoji.
- Notes. Each `notes[]` entry and why it exists.
- Unplaced. Every block the converter could not place, quoted verbatim
  with its section.
- Finish by hand. The checklist below.

## Finish by hand

1. Write an `h` handle of two to five words on every cause, action, window,
   and sub-incident; `check` errors until they exist.
2. Write a `p` plain twin for `summary`, `impact`, every cause, `resolution`
   and `detection`.
3. Confirm the zone of every converted time in the report, and correct
   `timestamps`; set `engaged` and `mitigated` from the timeline.
4. Retitle every cause whose title is its first sentence and check every
   kind and actor guess.
5. Replace customer names with deployment codenames in prose, fill
   `impact.teams` and `meta.teams`, and run `retro.py check` with a
   `.customer-names` file or `--forbidden-terms`.
6. Run `retro.py evidence fetch` for every notebook and monitor the report
   lists, and `retro.py evidence slack new` for every Slack permalink.
7. Run `retro.py check` and clear what it reports, then continue at the
   Draft phase in `SKILL.md`.
