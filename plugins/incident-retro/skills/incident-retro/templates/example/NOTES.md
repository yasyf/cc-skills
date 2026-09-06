# Checkout errors after a rate-limit config push — Notes

The prose companion to the structured retro. Everything with a stable ID lives
in `retro.json` (canonical; rendered by `incident-retro.html`); the evidence
the page renders lives as snapshot files under `evidence/`. This file holds
what doesn't fit structure.

## Where things live

- `retro.json` — timestamps, windows, timeline, impact, causes, resolution
  and detection, actions, lessons, the evidence register, notes.
- `evidence/datadog/` — the notebook and monitor snapshots.
- `evidence/slack/` — the incident channel thread.
- `evidence/images/` — the annotated error-rate screenshot.
- `incident-retro.html` — the interactive retro; serve this folder over HTTP.
- This file — prose.

## Working timeline

The channel timeline as pasted on the day, before it was cleaned into
`timeline[]`. Two entries were dropped: a duplicate page acknowledgement and
a question about the status page that nobody answered.

## Hypotheses that did not hold

- The payment provider: its status page was green and its latency flat; the
  `429 upstream` error bodies ruled it out at 10:02.
- A Redis eviction on the limiter's counter store: eviction metrics were
  flat for the whole window.

## Open questions for the retro meeting

- Whether the burst floor of one is the right floor, or whether a missing
  override should fail the config validation outright.

## Changelog

- 2026-08-21: Retro written from the channel timeline and the notebook.
- 2026-08-18: Retro meeting held; action items assigned.

## Loose notes

- The `Tenants affected` count comes from the distinct `tenant` tag on the
  429 series over the outage window, so it counts tenants with at least one
  refused request, not tenants who noticed.
