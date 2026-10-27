# incident-retro

`incident-retro` turns an incident timeline and its evidence into an interactive, blameless retrospective page rendered from one canonical `retro.json` beside committed evidence. The page explains what the system allowed for readers who did not take part in the response, and derives every duration from timestamps so no prose states a duration by hand.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install incident-retro@skills
```

Prerequisites:

- `python3`
- The `gemini` CLI, where `gemini-4-argon` writes the prose
- Chrome or Chromium for render checks and the PDF
- `DD_API_KEY` and `DD_APP_KEY` for Datadog snapshots
- A `design-docs` checkout, passed as `--docs`, that publishes through GitHub Pages

## Use it

When an incident is over and you need a reviewable record instead of scattered Slack threads and dashboards, ask:

```text
Write a retro for yesterday's checkout outage
```

The skill scaffolds a draft from your incident directory, `cci:<regex>`, or `cc-notes` record ids: it lifts links, guesses event kinds, stamps detected, engaged, and mitigated times, and marks key rows for you to review. It fetches Datadog notebooks and monitors with `evidence fetch` and keeps Slack threads verbatim, committing the snapshots beside `retro.json` to draw them directly in the page. After all-clear, `gemini-4-argon` writes headlines, summaries, plain twins, and handles through `prose`, while `slop-cop` lints hand edits. The rendered page opens on an executive summary with time-to-detect, time-to-mitigate, and time-to-resolve tiles, outage windows, and a source-linked swimlane timeline. It also includes causes typed as trigger, root, or contributing, action items with live GitHub state, a time scrubber, reader comments, an Ask bar, and a PDF. When ready, `publish` runs the strict check, the render check, and a `slop-cop` pass, opens and merges a PR in your `design-docs` checkout, waits for the GitHub Pages deploy, and prints the rendered page URL on a `RENDERED:` line.

While the response is still running and you want the retro page to follow it, ask:

```text
Start a live retro for the ongoing incident
```

Live mode reads the incident directory's `state.json` and `slack-log.jsonl`, using `live init` and `live sync` to derive the timeline, outage windows, causes, action items, and Slack snapshots, and pushes them to a live branch that the page polls every 30 seconds. No model runs while the incident is ongoing. Once resolved, `live finalize` turns the live page into a draft at the same URL.

If your team has an existing retrospective in Google Docs that you want to move into `retro.json`, ask:

```text
Import this Google Doc postmortem
```

The skill runs `import-gdoc` on a Google Docs Markdown export, converts the postmortem into a draft, and produces an import report for you to review.

## What's inside

| Component | Kind | What it does |
| --- | --- | --- |
| `incident-retro:incident-retro` | Skill | Turns an incident timeline and its evidence into a blameless retrospective page from one canonical `retro.json`. |
| `retro.py` | CLI driver | Scaffolds drafts with `new` and `import-gdoc`, runs live updates with `live init`, `live sync`, and `live finalize`, fetches evidence with `evidence fetch`, generates prose with `prose`, checks quality with `check` and `comms-check`, and publishes with `pdf` and `publish`. |

Part of [cc-skills](../../README.md).
