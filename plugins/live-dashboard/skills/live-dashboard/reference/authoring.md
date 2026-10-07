# Write a component

A component is a Python function that returns one typed payload. The engine owns the
cadence, the timeout, the cache, the secret scan, and the page; the function only
reads its sources and shapes the answer. Write one when the owner's question needs a
join no built-in makes.

## Scaffold, edit, check

```bash
live-dashboard new rulings --dir D --payload Matrix
live-dashboard check --dir D --only local.rulings
```

`new` writes `D/components/rulings.py` returning `Matrix.example()` and appends a
`local.rulings` card in a new section at the end of `layout.yaml`. Edit both, then run
`check` until it reports every card clean. The running server reloads
`components/*.py`, `layout.yaml`, and `context.json` within five seconds of a change.

## The shape of a component

```python
from pathlib import Path

from livedash import Context, Gate, Checklist, component


@component("plan-steps", "Plan steps", question="Which steps of the plan are done?", reads=["ccn plan show"], every="5m", timeout="30s")
def plan_steps(ctx: Context, *, plan: str, done: list[str] = []) -> Checklist:
    """Each step of a cc-notes plan as a gate; a step whose id is in `done` passes."""
    text = ctx.ccn("plan", "show", plan)
    steps = [line.removeprefix("- ").split(" ", 1) for line in text.splitlines() if line.startswith("- S")]
    return Checklist([Gate(ident, title, "pass" if ident in done else "open") for ident, title in steps])
```

- `question` is the one owner question the card answers, one line ending in `?`. The
  page shows it under the card's title, and a layout card's `question:` key replaces it.
  `check` refuses the scaffold's `TODO` question.
- `reads` names each source the function reads, such as `ccn ledger row list` or
  `pup metrics query`. The catalog lists them.
- The first parameter is the `Context`, positional. Every other parameter is
  keyword-only, after `*`, typed `int`, `float`, `str`, `bool`, `Path`, `list[int]`,
  `list[str]`, `list[int | str]`, `dict[str, str]`, or one of those `| None`. A relative
  `Path` resolves against the dashboard dir.
- The return annotation names the payload, and the engine rejects any other type the
  function returns.
- The id is lowercase words joined by hyphens. `every` is one of `15s`, `30s`, `1m`,
  `2m`, `5m`, `15m`, `manual`; `timeout` looks like `30s` or `2m`.
- The docstring is the component's catalog entry. Name what it reads and what each
  parameter changes.
- A component prints nothing; `check` flags any stdout. It raises on a failed read and
  lets the engine keep the last good payload; it never returns an empty one instead.

## Context

| Member | Does |
|---|---|
| `ctx.dir` | The dashboard dir |
| `ctx.facts` | `context.json` as a dict |
| `ctx.now` | The run's UTC time |
| `ctx.run(argv, timeout=None, cwd=None, input=None)` | Runs a command, returns stdout, raises on a non-zero exit; a 429 or rate-limit message raises `RateLimited` |
| `ctx.json(argv, timeout=None, cwd=None)` | `run`, parsed as JSON |
| `ctx.gh_graphql(query, **variables)` | One `gh api graphql` call; refuses while the last known quota is under 500 |
| `ctx.ccn(*args)` | `ccn -R <checkout> <args>`, returns stdout |
| `ctx.cci(path, **params)` | GETs the cci daemon for the dashboard's `cci_drive`; list values repeat their key |
| `ctx.glob(pattern)` | Matching files, newest first; relative patterns resolve against the dir |
| `ctx.latest(card_id)` | Another card's last good payload, or `None` |
| `ctx.cards()` | Every card's status, age, run time, and error |
| `ctx.prior` | This card's previous payload, or `None` |

Raise `RateLimited` yourself when a source reports its own limit; the card then waits
five minutes instead of backing off on its cadence.

## Payloads

Import every payload from `livedash`. Tones are `ok`, `warn`, `bad`, and `muted`.

| Payload | Fields | Renders |
|---|---|---|
| `Table` | `cols: list[Col]`, `rows: list[dict]`, `group_by`, `footer: dict`, `note` | Sortable table; a row's `key`, `cite`, and `tone` mark it |
| `Col` | `key`, `label`, `kind`: `text`, `num`, `age`, `due`, `link`, `badge`, `delta`, or `bar`; `phone` | A `link` cell reads its URL from `<key>_url`, a `badge` its tone from `<key>_tone`; `due` counts down to an ISO time and reads overdue past it; `phone=False` folds the column behind a tap on a phone |
| `Matrix` | `rows: list[str]`, `cols: list[str]`, `cells: list[list[Cell]]`, `title`, `pick` | Grid of toned cells, at least two columns |
| `Cell` | `text`, `tone`, `title`, `link` | `title` is the hover detail, `link` the page the cell opens |
| `Checklist` | `items: list[Gate]`, `note` | Gate list |
| `Gate` | `id`, `title`, `status`, `blocker`, `owner`, `closes`, `link` | `status` is `open`, `pass`, `fail`, `blocked`, `waived`, `not-created`, or `not-run` |
| `Tiles` | `tiles: list[Tile]`, `note` | Big numbers |
| `Tile` | `label`, `value`, `unit`, `tone`, `hint`, `link` | |
| `Series` | `lines: list[Line]`, `unit`, `thresholds: dict[str, float]`, `note` | Line chart with labeled rules |
| `Line` | `label`, `points: list[[at, value]]` | |
| `Percentiles` | `dists: list[Dist]`, `history: Series`, `note` | p50, p95, p99, and max tracks against a budget |
| `Dist` | `label`, `n`, `p50`, `p95`, `p99`, `max`, `unit`, `budget`, `failures` | `Dist.of(label, samples, unit, budget, failures)` computes nearest-rank percentiles |
| `Graph` | `nodes: list[Node]`, `edges: list[[parent, child]]`, `highlight: list[str]` | Tree from each root, highlighted nodes marked |
| `Node` | `id`, `label`, `tone`, `link` | |
| `Feed` | `entries: list[Entry]`, `note` | Newest-first stream |
| `Entry` | `at`, `actor`, `text`, `link`, `tone`, `latency_ms`, `key`, `cite` | `latency_ms` shows as a pill |
| `Kv` | `pairs: dict` | Definition list |
| `Markdown` | `text` | Rendered markdown |
| `Svg` | `svg` | A sanitized inline drawing |

A payload over 256 KB is dropped with an error. Give every row that names a record a
`cite` such as `pr:101`, `cci:42`, or `task:7`: the page's Ask chat searches and quotes
rows by cite, and `live-dashboard card` prints them.

## Actions

An action is a button on each row of a card. Declare it on the decorator:

```python
from livedash import Col, Context, Table, component


def rerun(ctx: Context, row: dict, text: str) -> str:
    ctx.run(["gh", "run", "rerun", row["key"], "--repo", ctx.facts["repo"], "--failed"])
    return f"re-ran the failed jobs of run {row['key']}"


@component("red-runs", "Red runs", question="Which runs on the branch failed?", reads=["gh run list"], every="2m", actions={"rerun": rerun})
def red_runs(ctx: Context, *, branch: str = "main") -> Table:
    runs = ctx.json(["gh", "run", "list", "--repo", ctx.facts["repo"], "--branch", branch, "--status", "failure", "--json", "databaseId,displayTitle,url"])
    rows = [{"key": str(run["databaseId"]), "title": run["displayTitle"], "title_url": run["url"]} for run in runs]
    return Table([Col("title", "Run", "link")], rows)
```

The page posts the row's `key` and the note the owner typed. The function gets the row
from the card's last payload, does the one thing, and returns the line the page shows.
A `ValueError` shows as the owner's mistake; any other failure shows as the source's.
The card reruns right after.

## Packs

A pack is a directory of component modules shared across dashboards. Name it in
`context.json`:

```json
{"id": "release-v3", "title": "Release v3", "packs": {"lr": "/path/to/long-running/dashboard"}}
```

Its components register as `lr.<id>`, and modules in the directory import each other
relatively, as `from . import inboxlines`. Changing a pack's path retires the running
server on the next `start`, so the new code serves.
