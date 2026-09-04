# The method

Assumptions first, then decisions, each with a stable ID and a paper trail. The register is the design; the document is a rendering of it.

## Registers and lifecycle

`registers.json` is canonical: if a fact has a stable ID, it lives there and nowhere else. The companion files each have one job: `qa-log.json` holds the verbatim question rounds, `NOTES.md` holds prose that doesn't fit structure, `summary.html` is the executive summary written for people, `design-doc.html` renders all of it, `design.py pdf` prints it. The system diagram is Mermaid source in the `diagram` register key, so its nodes can name the cards and the request-path steps that belong to them; a hand-drawn `sysd.svg` is the one alternative, and the doc can only display it. Editing the JSON updates the doc, its Markdown exports, and (after a rerun) the PDF, so there is exactly one place to change a fact.

An entry is never deleted and never edited into a different claim. It is revised in place only for wording; when the *substance* changes, the old entry is superseded:

- A decision gets a successor: new `DQ#`, and the old one gets `s: "superseded"` plus `by: "<successor>"`. `check` enforces the pair in both directions.
- An assumption that gets revised keeps its ID and records the history in its `n` field ("Revised once, 2026-07-21: the original 8 KB bound fell at the first consumer survey").

The point of supersession is that a reader can watch the design change its mind. A register that only shows final answers hides exactly the reasoning a reviewer needs.

Supersession is the semantic history inside the register; `design.py snapshot` adds the mechanical history between publishes. Each publish archives the registers as `history/rev-<N>.json`, and the doc diffs the live registers against any archived revision, so a returning reviewer sees what changed since they last read it without replaying the whole record. The snapshot hashes `summary.html` along with the registers, so an edit to the deck records a revision of its own, marked `changed`, and the doc flags the section for that reviewer.

Every rendered entry carries its wording twice: the precise form the register reasons in, and a plain twin in `p` that says what the entry means to someone who will not read it. The twin is part of the entry, written by the same hand at the same time, and it moves when the entry moves. `check` compares both against the last snapshot, so a twin that still describes an older decision is caught before the next one. Each decision and assumption also files under one of the design's themes, named in the Phase 0 diagnosis, and the few that carry `key: true` are the ones the doc opens its section on.

## The executive summary

`summary.html` is a rendering, not a register: a deck of full-screen panels that renders the diagnosis and the register together. The Phase 0 diagnosis supplies the thesis panel's claim and the before column of the compare panel (or, for a net-new design, the requirements); the resolved decisions supply the after column. The numbers panel is `numbers` cells and path segments, each marked estimated with its spike when nobody has measured it. The cost panel is constraints, assumptions, and open items: what stays fixed, and where a reader's objection lands. A poster, when the design is one idea and one figure, renders the same sources into one composed figure. Nothing in it may lack a register entry behind it: when the summary wants to say something the registers don't, the register gets the entry first. It's written in Phase 5, once the decisions it summarises exist; the contract for its content and voice is in [writing.md](writing.md), the panel kinds and their budgets in [schema.md](schema.md).

The deck is one argument in five steps, the problem to the rollout, and every panel headline is a claim in that argument; the contract is in [writing.md](writing.md) under One argument.

## Components

A component is a rendering of register data, the way the deck is: a `dd.whatif` moves a number the `numbers` table already states, a `dd.steps` walks a path the `paths` register already decomposes, a `dd.timeline` gates phases on open items that already have IDs. The declaration in the `components` map names what it draws and which entries it cites, and nothing in it is a fact the registers lack. When a widget needs a number the registers do not carry, the register gets the number first, marked estimated and gated on a spike like any other. That is what lets `check` validate the props, the Markdown export flatten the widget to its values, and the PDF print its first state without a second source of truth appearing anywhere. An author-written TSX component under `components/` is held to the same rule, with the registers handed to it as a prop.

## The round protocol

Every design fork goes through a question round. The round surface is a live `cc-present` board: at the start of the interview, look for `cc-present:present` (the `/cc-present` command) in the available-skills list and load it with the Skill tool, then compose each round per its instructions; the clicks stream back while you keep working. This plugin ships a `design-doc` block pack — `cc-present pack list` prints its reference fragment — and a round board is built from it: a `design-doc.registers` block showing where the design stands, then a `design-doc.fork` block per fork, or one `design-doc.claims` block when the round is sweeping claims rather than deciding. A missing pack degrades to a `choice` block per card and a `triage` block per sweep, carrying the same shape by hand; AskUserQuestion is the surface of last resort, used only when `present` is not in the skill list. The shape is the same on all three:

- Ask the question a person can answer cold: "How should workers receive jobs?", never "DQ4: How should workers receive jobs?". The register ID the round settles goes in the fork block's `decides` field, where the board's meta line shows it (`decidesTitle` puts the decision's title there instead), or trails the `header` on the AskUserQuestion fallback ("Worker transport (DQ5)"); it never leads the question text. Options are plain labels. When context is needed, name the earlier decision by its title ("this replaces the long-poll transport from round 2"), not its ID.
- When a review finding drives the round, the question states what the reviewer found, in words: "The reviewer pointed out that a lease can expire during archival and deliver the job twice. How do we close that?" The finding number stays in the `findings` register.
- Each option carries a consequence description — what choosing it costs and buys — not just a label. A consequence says what happens ("dispatch latency floors at the poll timeout"), never which entry it supersedes. Exactly one option ends with "(Recommended)".
- Every fork carries an escape from deciding. On `design-doc.fork` it is the block's own control: the human names the open question and the payload arrives as `{defer: {title, why}}`, ready to enqueue as a `Q#` under the right owner group. On a plain `choice` block it is a last option, **"Add to open list"**, described like "I don't know yet — record it as an open question", and you ask for the title in the next round. Either way, actually enqueue the entry: the escape exists so the user is never forced to decide with insufficient information, and one that silently drops the question would teach them to stop using it.
- One round can carry several related questions; keep unrelated forks in separate rounds so the log stays legible.

The rounds are an interview, not a survey. Bring an opinion to every question: point out flaws in the current draft (including the user's own proposal), suggest alternatives they didn't name, and say what you'd pick and why — then let them pick. An answer that opens new questions spawns the next round; it never becomes a silent decision.

Log every round to `qa-log.json` verbatim: the question, every option with its description, and the answer as given. Two rules keep the log honest — clean up spelling and grammar in answers but change nothing of substance, and skip explain-only exchanges entirely (it is a decision log, not a transcript). A custom free-text answer is recorded as-is; `check` flags it as a warning only so you can confirm it was intended. The `topic`, `header`, and `question` strings obey the same rule as the board: plain words, no leading register ID, no "finding 27"; `check` warns on either.

After the round, distill it: the decision's `round` field points at a condensed `{q, a, n}` entry in the registers `rounds` dict, which the doc shows inline under the decision. `q` is the question as the person read it and `a` the answer as they gave it, so neither leads with an ID either.

## Open items

Open questions group by **owner** — the person or team who can actually answer, plus a group for spikes. An open list without owners is a wish list; with owners it is an agenda. The `openGroups` map defines the groups and their display order. The pull request that retires an item goes on its `links[]` with `closes: true`, and a decision carries the pull requests that landed it the same way, so the register records what shipped as well as what was decided.

## The adversarial review

Run it against the middle draft, after the shape is set but before polish: flaws found late cost a rewrite of prose that was written around them. The reviewer needs no context on why decisions were made, only what they are; a fresh perspective is the point.

- Preferred reviewer: the `codex` plugin skill (an independent model attacking the design). Fallback: a fresh-context subagent with the same brief.
- The brief, roughly: "Adversarial review of a design proposal. Read the registers in this directory. Attack it as a skeptical senior engineer: find correctness bugs, missing failure modes, unjustified numbers, and decisions that don't survive their own stated assumptions. Number your findings and rate severity."
- Save the raw output verbatim as `<reviewer>-review-<date>.md` with a one-line provenance header (tool, model, date, scope).
- Index each finding in the `findings` register as `[n, severity, title, decisionRef]`. This register is data only — the doc never renders it. The review's value ships as the decisions it drove; a "review findings" section in the doc reads as showing off and gives the reader nothing actionable.
- Disposition every finding: a new or superseding `DQ#`, an open item, or a recorded rejection (a sentence in the relevant decision's `x` field saying why the finding doesn't bite). A disposition that needs the user's call goes through a round asked in words, per the round protocol. A finding with no disposition is an open bug in the design.
- Iterate. After the dispositions land, run the reviewer again on the updated registers: dispositions change the design, and a changed design grows new flaws. Each pass gets its own dated artifact. Stop when a pass yields nothing that changes a decision: one pass is the floor, not the norm.

## The quantitative story

The doc carries a small library of quantitative components, and the design picks the ones that fit: request paths (with the scale strip) when the story is latency, load ceilings when it is load, and `numbers` tables for any other axis (a throughput budget, storage growth, a cost model, freshness windows), each table bringing its own columns. Latency is one member of the library, not a default; a batch pipeline with no user-facing request may need only a throughput table, and a design with no quantitative story uses none of them (empty registers hide their sections).

Whichever components are in play, estimates are honest when they are labeled and falsifiable:

- Every number that hasn't been measured is marked `(E)`, and every `(E)` is gated on a named spike `V#` in the open list. The pairing is what separates an estimate from a guess.
- Request paths decompose into segments (`[step, p50, p95, description]`); the doc sums them and animates a trace, so a reader can see where the budget goes.
- Each load ceiling is a row of four: the resource, the ceiling, the first observable symptom, and the guard in front of it. Naming the first symptom is what makes the ceiling operational: it tells the on-call what they'll see.
- A `numbers` table earns its place by being checkable: put the unit in the column header, and point a surprising cell at its derivation with a footnote token.
- A `numbers` table opens with its claim. `claim` is one or two sentences saying what the table shows and which decision it changes or which risk it sizes, with the decision cited in parentheses ("The tenant's Restate worker serves one live cache and one dead counter, so the cluster can run no Restate (DQ7) once the cache moves to memory (DQ12)."); then the table, then `source` as the source line, then `note`. A table whose claim names no decision is research, and research lives in NOTES.md.
- The degradation rule: under overload the system backpressures, rejects, or serves stale. If any proposed mechanism has a corruption-shaped failure mode, the mechanism is wrong, not the load.

## NOTES.md

The prose overflow, with a fixed skeleton: **Where things live** (the artifact map, `summary.html` included), **Method** (one paragraph), **The diagnosis** (root causes of the current system's problems — written in Phase 0, before any design, and closing with whether the design is a change to a system that exists or net-new, which sets the shape of the compare panel, and with the three to five themes the decisions file under), **The summary** (deck or poster, and why; the gates' findings and what was done with each), **Figure briefs** (one five-line block per figure: the point it makes, the decision it serves by ID, the smallest view that makes the point, what it leaves out, and the real labels it uses), **The critique** (one row per panel, figure, table, and stat: the decision it serves, the claim it makes, the question a reader is left with, and fixed or cut), **Derivations too long for a register field** (bold lead-in paragraphs, one per argument), **Changelog** (dated bullets for milestones), **Loose notes**. When a register field wants three paragraphs of argument, the field gets the conclusion and NOTES.md gets the argument.
