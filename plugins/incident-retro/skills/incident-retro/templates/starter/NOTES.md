# PROJECT_TITLE — Notes

The prose companion to the structured retro. Everything with a stable ID lives
in `retro.json` (canonical; rendered by `incident-retro.html`); the evidence
the page renders lives as snapshot files under `evidence/`. This file holds
what doesn't fit structure: the working timeline before it was cleaned up,
the hypotheses that turned out wrong, and anything homeless.

## Where things live

- `retro.json` — timestamps (the tiles derive every duration from them),
  outage windows, the timeline, impact, causes, resolution and detection,
  action items, lessons, the evidence register, and notes. Every cause
  carries its exact wording and a plain twin `p`; causes, actions and
  windows carry a handle `h` that citations show. Edit this to change the
  retro.
- `evidence/datadog/` — notebook and monitor snapshots written by
  `retro.py evidence fetch`; the page draws the charts from them, so it
  never calls Datadog.
- `evidence/slack/` — thread snapshots written from the incident channel;
  the page quotes them, so it never calls Slack.
- `evidence/images/` — screenshots, each registered with alt text and a
  caption that cites the window or cause it shows.
- `incident-retro.html` — the interactive retro. Renders from retro.json, so
  it must be served over HTTP (`python3 -m http.server 8641` in this
  folder); opened as a bare file it shows instructions instead.
- `incident-retro.pdf` — the served page printed through its own print
  stylesheet by `retro.py pdf`. Generated, never checked in.
- This file — prose.

## Working timeline

(Paste the raw channel timeline here before it is cleaned into `timeline[]`;
keep the entries that were dropped and why.)

## Hypotheses that did not hold

(One line each: what was suspected, what ruled it out, and the timeline
entry that did.)

## Open questions for the retro meeting

- (What the room still has to settle.)

## Changelog

- PROJECT_DATE: Retro scaffolded.

## Loose notes

- (Anything homeless.)
