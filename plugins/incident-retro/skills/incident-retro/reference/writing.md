# The writing contract

A useful retro explains the conditions that produced an incident and the
changes that reduce recurrence. It does not grade the people involved.

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

Never restate a derived duration in prose. Say what happened between the two
events and let the tiles show the elapsed time. `retro.py check` warns when
prose states a duration near detection, engagement, mitigation, resolution,
onset, a monitor firing, or the all-clear.

Put impact counts in `impact.metrics`. Set `measured: true` for observed counts
and `measured: false` for estimates. Describe the measurement boundary in
`delta`, then cite the cause or window that gives the number context.

## Citations and handles

A citation follows the sentence it supports: `(C1)` or `(C1, T7)`. The
renderer replaces each id with its handle. The exported Markdown does the
same, while the id remains available in the interactive card.

The sentence must stand on its own after deleting the citation. Write *The
empty override map sets the burst limit to zero (C1).* Do not write *The burst
limit comes from (C1).*

Every window, cause, action, and sub-incident carries an `h` handle. Write a
distinct noun phrase of two to five words that sounds natural in a sentence.
Name the thing, not its register type. "zero burst default" works; "root cause
decision" does not.

Timeline handles are derived from the event's local time, so add a `T#` id only
when another entry cites that event.

## Plain twins

`summary`, `impact`, every cause, `resolution`, and `detection` each carry a
precise `text` and a plain twin in `p`. Most readers see the twin first.

Write each twin in 30 words or fewer. Preserve every fact and number from the
precise wording, use everyday terms, and explain what the entry means to a
reader outside the team. Do not include ids or file paths. Omit issue numbers
and phrases such as `we decided`. A twin adds the consequence instead of
repeating its title.

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

Run the generated Markdown through the prose gate after the structural edit
and again after the final tone edit:

```bash
$TOOL text <dir> | slop-cop check - --lang=markdown --llm-effort=off
```

Fix genuine findings. Keep only constructions that carry necessary technical
meaning, such as a field-list colon. Parenthetical asides and ellipses do not
carry incident facts; rewrite them directly.
