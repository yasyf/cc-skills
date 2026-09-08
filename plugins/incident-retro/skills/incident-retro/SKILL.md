---
name: incident-retro
description: Build a blameless incident retrospective from structured facts and committed evidence snapshots, then render the same record as HTML, Markdown, and PDF. Use when asked to "write a retro", create an "incident retrospective" or "postmortem", "import this Google Doc postmortem", run a "blameless review", or turn an incident timeline and its evidence into a reviewable record.
allowed-tools: Bash(python3:*, ls:*, cat:*, pdftoppm:*, wrangler:*, npm:*, open:*, wlm:*, slop-cop:*, uvx:*, ssh:*, rsync:*, cc-present:*), Bash(aws:*), Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# incident-retro

GPT-6 Astra (`gpt-6-astra`) at `xhigh` writes and revises all prose,
including summaries, plain twins, handles, revision notes, and publication
text. Delegated authors use the same model and effort. In Codex, set these
explicitly when spawning an author. From Claude, load the `codex` skill and
use its `codex-ask -m astra` route, which pins both settings; Claude may
collect evidence and publish the result, but must delegate the writing.
Prose helpers try Astra first and may use Claude only when Astra fails.

An incident retro is one canonical `retro.json` beside committed snapshots of the evidence behind it. `incident-retro.html` renders that record, and `retro.py` derives every duration from timestamp fields. Write in a blameless voice that explains what the system allowed, not which person deserves blame.

Use one driver for every mechanical step:

```bash
TOOL="python3 ${CLAUDE_PLUGIN_ROOT}/skills/incident-retro/scripts/retro.py"
```

The AWS Systems Manager Parameter Store paths come from the caller. The AWS profile and region do too; the plugin supplies no credential path or cloud default.

Read [reference/writing.md](reference/writing.md) before Draft, [reference/evidence.md](reference/evidence.md) before Gather, and [reference/schema.md](reference/schema.md) whenever a field is unclear.

## What the agent writes

`retro.py` scaffolds, validates, renders, snapshots, and fetches Datadog evidence. The authoring agent writes the incident itself and the Slack snapshots:

- Fill `retro.json` from inspected evidence. Do not infer a missing event, cause, owner, or outcome.
- Fetch Slack messages with the agent's own Slack tooling. Save the resulting `ir.slack/1` files under `evidence/slack/`, then register each file in `evidence.slack[]`.
- Write every timestamp as ISO 8601 with a UTC offset. `meta.timezone` controls display only.
- Write each plain twin `p` in 30 words or fewer. Keep every fact from the precise wording, but include no register id or file path.
- Write each handle `h` as a distinct two-to-five-word noun phrase. Windows, causes, actions, and sub-incidents all need handles.
- Use deployment or service codenames in public prose. Never publish the name of a customer, company, workspace, or account.

## Phase 1: Gather

Create a fresh directory, then collect the source material before drafting.

```bash
$TOOL scaffold <dir> --title "<title>" --date YYYY-MM-DD [--incident N]
```

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
2. Build the timestamp-sorted `timeline`. Name each actor by role or first name and attach a source in `refs` wherever one exists.
3. State `impact` through observed effects and measured or estimated metrics.
4. Separate the trigger and root cause from contributing causes. Attach the evidence that supports each claim.
5. Describe `resolution` and `detection`, including monitors that caught or missed the incident and monitors added afterward.
6. Give every action an owner, source, state, and due date when one exists.
7. Fill every applicable `lessons` column from the evidence.
8. Write the plain twins and handles in the same pass as their precise text.

Follow [reference/writing.md](reference/writing.md). Mark a required answer as not recorded when the sources do not provide it. Never invent connective events to make the story read more smoothly.

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

## Phase 5: Publish

Record the revision readers need to review, then resolve linked change state:

```bash
$TOOL snapshot <dir> --note "<headline>" --item "<change>"
$TOOL links <dir> --fetch
```

Write the snapshot note for a returning reader. Name what changed and why it matters without register ids. Add another `--item` for each distinct change.

Move `meta.status` through this lifecycle:

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

Read the Import report in `NOTES.md`. Review every timestamp conversion, kind guess, actor, link classification, image, and unplaced block. The importer leaves handles and twins empty on purpose. Finish them and collect the referenced evidence before continuing at Draft.

## Reference files

[reference/schema.md](reference/schema.md) defines the `retro.json` fields and
validator behavior. [reference/writing.md](reference/writing.md) governs the
blameless voice, tense, numbers, citations, twins, and handles.
[reference/evidence.md](reference/evidence.md) documents the Datadog and Slack
snapshot formats and capture flow. [reference/import.md](reference/import.md)
covers Google Docs conversion and the manual finish checklist.
[reference/components.md](reference/components.md) defines component placement,
options, and Markdown flattening.
