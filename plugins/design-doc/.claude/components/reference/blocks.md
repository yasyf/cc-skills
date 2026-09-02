# design-doc pack blocks

Three block types under the `design-doc` pack, one per phase of the interview.
Reference them by dotted wire type inside any `Doc.blocks` array or a card's
`children`. All three are interactive; every click streams back as a
`pack.interaction` event carrying the payloads described below.

A round's board normally carries `design-doc.registers` first — the human reads
where the design stands — then whatever the round is actually asking.

The field names on `registers` mirror `registers.json` entry-for-entry, so a
register entry goes onto the board unchanged.

Ids have to be unique within a block. Every one of these blocks keys its answers
by row id, so two rows sharing an id share one answer.

## design-doc.registers

The live design state: assumptions, decisions, and the open list. Every section
is optional, and an empty section renders nothing. With `challengeable`, each
assumption and decision row carries Confirm and Challenge, and a challenge opens
a note field.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique block id. |
| `type` | `"design-doc.registers"` | yes | The dotted wire type. |
| `title` | string | yes | Panel heading. |
| `phase` | string | no | Right-aligned caps label, e.g. `2 — design rounds`. |
| `challengeable` | boolean | no | Show the per-row Confirm/Challenge controls. |
| `assumptions` | array | no | `{ id: "A#", t, s: "working"\|"validate", b?, n?, star? }`. |
| `decisions` | array | no | `{ id: "DQ#", t, s: "resolved"\|"superseded"\|"open", r?, x?, by?: "DQ#", round? }`. |
| `open` | array | no | `{ id, t, g? }` — `g` groups the item. |
| `openGroups` | object | no | Maps a `g` key to its display label. |

`star` marks the load-bearing assumption; `n` is the "if this falls" line, `b`
the basis. Every row leads with its title and carries its id as a trailing chip;
a decision with `by` reads `Worker transport: long-poll HTTP` `superseded`
`DQ3` `→ replaced by Worker transport: long-poll HTTP, renewals TBD` `DQ4`.

```json
{
  "id": "state",
  "type": "design-doc.registers",
  "title": "Where the design stands",
  "phase": "2 — design rounds",
  "challengeable": true,
  "assumptions": [
    { "id": "A1", "t": "At-least-once delivery is acceptable", "s": "validate", "star": true,
      "b": "Every current consumer dedups on an idempotency key.",
      "n": "If this falls, the design gains an exactly-once ledger in the hot path." }
  ],
  "decisions": [
    { "id": "DQ3", "t": "Worker transport: long-poll HTTP", "s": "superseded", "by": "DQ4",
      "r": "Long-poll HTTP chosen for dispatch.", "round": 2 }
  ],
  "open": [{ "g": "spikes", "id": "V1", "t": "Measure dispatch latency under 1k producers." }],
  "openGroups": { "spikes": "Spikes & benchmarks" }
}
```

The interaction payload is a merged map keyed by register id:

```json
{ "entries": { "A1": { "action": "challenge", "note": "the archiver consumer is not idempotent" } } }
```

`action` is `confirm` or `challenge`. A challenge without a note means the human
disputed the entry and left the reason unsaid. Ask for it.

## design-doc.claims

A list of claims the human confirms, corrects, or rejects in one pass. Use it
for the Phase 0 diagnosis and the Phase 1 assumption sweep: one block instead of
one question per claim. Anything other than the first verdict opens a correction
field, and the human's wording there goes into `qa-log.json` verbatim.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique block id. |
| `type` | `"design-doc.claims"` | yes | The dotted wire type. |
| `prompt` | string | yes | Panel heading — what the sweep is asking. |
| `verdicts` | array of exactly 3 strings | no | Verdict labels; defaults to `["holds", "partly", "wrong"]`. The first is the "no correction needed" one. |
| `claims` | array, ≥1 | yes | `{ id, label, because?, ifFalse? }`. |

`because` is the evidence for the claim; `ifFalse` is what it costs to be wrong,
rendered as the "If this is wrong" line.

```json
{
  "id": "diagnosis",
  "type": "design-doc.claims",
  "prompt": "Is this what today's queue actually does?",
  "claims": [
    { "id": "c1", "label": "Retries are unbounded",
      "because": "The worker re-enqueues on any exception and nothing counts attempts.",
      "ifFalse": "The backlog spikes are somebody else's bug and this redesign starts from the wrong root cause." }
  ]
}
```

The interaction payload is a merged map keyed by claim id:

```json
{ "verdicts": { "c1": { "verdict": "partly", "correction": "attempts are counted, but the cap is never read" } } }
```

## design-doc.fork

One design fork, whose options carry their consequence inline and their pros
and cons behind a Detail affordance. The escape hatch is a real control: instead of
picking, the human names an open question, and the payload carries the title, so
the `Q#` lands without a follow-up round.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | yes | Unique block id. |
| `type` | `"design-doc.fork"` | yes | The dotted wire type. |
| `question` | string | yes | The fork, as a plain question a person can answer cold; the register id belongs in `decides`, never in the text. |
| `round` | integer | no | Round number, shown in the caps meta line. |
| `decides` | string | no | The `DQ#` this round will write. |
| `decidesTitle` | string | no | That decision's title. The meta line reads `settles "Worker transport"` when it is set and falls back to `decides DQ5` when it is not. |
| `options` | array, ≥2 | yes | `{ id, label, consequence, recommended?, pros?, cons? }`. |
| `escape` | object | no | `{ label?, placeholder? }` — defaults to "Add to open list". |

`consequence` is what the option costs or buys, in one line — not a restatement
of the label. Mark at most one option `recommended`.

```json
{
  "id": "r3-transport",
  "type": "design-doc.fork",
  "round": 3,
  "question": "How do workers receive dispatched jobs?",
  "decides": "DQ5",
  "decidesTitle": "Worker transport",
  "options": [
    { "id": "long-poll", "label": "Long-poll HTTP", "recommended": true,
      "consequence": "Every worker runtime speaks it today; a dispatch costs one held connection per idle worker.",
      "pros": ["No new dependency"], "cons": ["Dispatch latency floors at the poll timeout"] },
    { "id": "websocket", "label": "WebSocket push",
      "consequence": "Sub-millisecond dispatch, and the broker now owns connection state for every worker." }
  ],
  "escape": { "label": "Add to open list", "placeholder": "What has to be true before this can be decided?" }
}
```

The interaction payload is one of two shapes:

```json
{ "choice": "long-poll" }
{ "defer": { "title": "Lease renewal transport", "why": "Needs the broker restart numbers from V2." } }
```

A `defer` payload is an instruction to enqueue a `Q#` titled `title`, not a
decision. `why` is what would unblock it.
