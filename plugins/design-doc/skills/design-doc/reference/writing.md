# The writing contract

The doc is a proposal asking for feedback, not a launch page. The reader should finish it knowing what is being proposed, what it costs, what could be wrong, and where their judgment is wanted. Every sentence that exists to impress rather than explain is a sentence between the reader and that outcome.

## Stance

The document exists to solicit feedback; every sentence serves that or gets cut. Write to explain and to ask, never to convince: a bold claim closes a conversation, a small checkable one opens it. Stay dispassionate: report what the design does and what it costs the way a lab notebook would, with no stake in the reader being impressed. The strongest sentence in a humble proposal names its own weak point: "If the one-file-per-tenant assumption falls (A1), the answer is probably a managed Postgres, not this document." Confidence lives in the specificity of the numbers and the honesty of the open list, not in adjectives. And the doc never talks about itself: no stance lines ("a draft for feedback, written to be corrected, not defended"), no process talk ("an adversarial review shaped this draft"), no announcements of its own humility. The posture shows in sentences that are easy to check and easy to disagree with; a doc that declares it wants correction is selling its modesty the same way a launch page sells its product.

<examples>
<example label="selling">
"A blazingly fast, rock-solid commit protocol delivers bulletproof durability."
Adjectives making claims the reader can't check.
</example>
<example label="explaining">
"The ack returns after the redo fsync and the SQLite commit both land — ~2–3ms p50 (E), gated on spike V9."
A number, its conditions, and the experiment that will check it.
</example>
<example label="bold claim">
"SQLite is the right storage engine here."
A verdict; the reader can only agree or fight.
</example>
<example label="soliciting">
"SQLite fits because the working set is one small file per tenant (A2); if that's wrong, the storage decision (DQ1) falls with it — that's the review we want."
The reasoning, its dependency, and the feedback wanted, all checkable.
</example>
</examples>

## One argument

The deck is one line of argument, and the doc is the same argument at full length. The summary reads in order: the problem, the ideas that answer it, what each idea closes, what it costs, and how it rolls out. A reader who reads the panel headlines alone should have the argument. So every panel headline is a claim with a verb in it ("Only Cloudflare decrypts a tenant request"), never a label ("The numbers", "What changes"). Every figure and every table carries a caption that states the claim it makes and names the decision it serves, as a parenthesised citation, and a figure that cannot name a decision is cut. A number sits in the deck only where it changes a decision or sizes a risk, and its label says which: "93 tables get a row policy (DQ9)" earns its place, "37 pods" does not.

The test for anything on a panel is the question a colleague asks across the desk: which decision does this serve, and what does it claim? A part with no answer is research residue, and it goes to NOTES.md or nowhere. `check` warns on a headline from the label list or under three words, on a figure caption that cites nothing, and on a stat label that cites nothing; `--strict` makes each an error.

## Structure rules

- The doc opens on the executive summary deck (its own section below), which fills the screen until the reader scrolls past it. Then comes a tl;dr of three to five plain twins, each at most 20 words (`check` warns above that), with links for every technology named. A mission-statement paragraph makes the reader work for what the bullets hand over.
- Definitions before use. Ground rules and a terms glossary come before the architecture; internal shorthand gets a plain-language name at first appearance ("stale-ok read" first, the internal enum name in parentheses if at all). A term the reader has to reverse-engineer is a small tax charged on every later sentence.
- Plain section names that say what the section is: "Request paths", "What stays the same", "Where we want pushback". A clever name costs a beat of decoding on every visit to the nav.
- Deep mechanics go in numbered footnotes (`[^n]`). The body stays readable at a walking pace; the footnotes reward the reader who wants the commit-ordering argument.
- Cut captions that restate what the eye already sees. Under a list visibly grouped by owner, "Grouped by who can answer them" says nothing.
- Section headers stand alone. The template renders no sub-copy under a header unless `meta.sections` authors it, and the bar for authoring one is that it carries design content ("Only the archival window is still open (DQ15)"), not an explainer of what the section is ("The main path through the system, then each part").
- Counts describe the system, not the effort. "27 decisions" and "9 spikes pending" are progress-report numbers; a design doc reader needs neither.

## Write for the person, not the register

The registers are for agents and `check`; the doc is for a colleague deciding whether to object. Register IDs are citations, never subjects: "the redo log (DQ1) absorbs the write" reads, "DQ1 puts the write in the redo log" is a ledger. The renderer leads every entry with its title and keeps the ID for the hover card, and a trailing citation like "(A2)" renders as the entry's handle with a dotted underline: the reader sees "one file per tenant", never "A2". Prose that cites in parentheses gets linked for free; prose that leads with an ID reads as bookkeeping on every surface, the PDF included. `check` bans an ID inside `p`, `t`, and `h` alike. Statuses in prose use the rendered words (decided, replaced, still open; assumed, needs someone to confirm), never the JSON values. The same rule runs backwards into the interview: a question is a plain question, and the ID it settles rides in the block's `decides` field ([method.md](method.md)).

A citation is a parenthesis. It follows a whole sentence and never stands in for a noun the sentence needs. The renderer swaps the ID for the entry's handle and a glyph, so "The hostname decides (DQ52)." renders as "The hostname decides (◆ x-team-id removed)." The test is to delete the citation and read what is left; it has to be a complete sentence that says the same thing. When the sentence would run over the panel's budget, drop a row, never clip the sentence. `check` warns on a citation whose preceding word is an article, a preposition, or a verb that leans on it ("decides", "does", "is", "replaces"), and on a sentence left under three words once its citations are removed; `--strict` makes both errors.

<examples>
<example label="cite as object">
"The hostname decides (DQ52)."
Delete the citation and the hostname decides nothing the reader can name.
</example>
<example label="cite as parenthesis">
"The hostname picks the cluster and the token's team claim authorizes (DQ52)."
</example>
<example label="cite as object">
"Postgres does, per cluster role (DQ9)."
</example>
<example label="cite as parenthesis">
"Postgres hides other teams' rows from each cluster's database role (DQ9)."
</example>
<example label="cite as object">
"None (DQ7)."
</example>
<example label="cite as parenthesis">
"The store and the worker go; the cache moves to memory over S3 (DQ7, DQ12)."
</example>
</examples>

## Plain twins

Every rendered entry carries two wordings: the precise one in `t`, `r`, `b`, or `md`, and a plain twin in `p`. The doc shows the twin by default and the exact wording one toggle away, so the twin is what most readers read. The twin says what the decision means to someone who will not read the decision: one or two short sentences, at most 30 words, everyday words, every fact and number kept, the decision named by its title. No register IDs, no file paths, no finding numbers, no "we decided". A twin restates nothing the title already says; it answers "so what".

<examples>
<example label="precise wording">
"Dispatch moves from a 500 ms table scan per worker to a long-poll that returns a lease (DQ4); the scan is retired once every worker is on the new client."
</example>
<example label="twin">
"Workers stop polling. Each one waits on an open request and gets a job handed to it, so dispatch cost no longer grows with the fleet."
What it means, in words a reader outside the team can check.
</example>
<example label="not a twin">
"DQ4 replaces polling with long-poll leases per the round-3 decision."
An ID, a round number, and nothing about what changes for anyone.
</example>
</examples>

`check` lints length and the banned tokens, and under `--strict` warns when an entry's wording changed since the last snapshot but its twin did not. `design.py plainify` drafts twins for entries that lack one; a draft is a draft, and the lane reads every one against its original before the snapshot.

## Handles

Every decision, assumption, open item, arch card, and numbers table carries a handle in `h`: what a reader would call the entry across a desk, in two to five words. The handle is what a citation shows. `(DQ26)` in prose renders as "24-hour tokens" with a dotted underline, a chip row reads "rests on 24-hour tokens, workload tokens", and the ID appears only in the hover card. So a handle has to read inside a sentence: the thing decided, assumed, or asked, never the fact that it was.

<examples>
<example label="handles">
DQ26 "24-hour tokens", V5 "edge rejection spike", A3 "Cloudflare terminates TLS", Q4 "archive window", c-log "the job log", n-cost "cost per tenant".
</example>
<example label="not handles">
"Token lifetime decision" names the register, not the thing. "Tokens expire after 24 hours so a removed user keeps access" is the twin, not the handle. "DQ26 tokens" carries an ID.
</example>
</examples>

Three rules. No register IDs, paths, or finding numbers, the same ban `p` carries. Capitalise only what the capitalisation rules below capitalise, because the handle sits mid-sentence. Keep handles distinct: two entries that read the same in a chip row are indistinguishable to the reader, so "token lifetime" and "token lifetimes" is a collision, not a pair.

`design.py plainify` drafts an `h` beside each `p` under a five-word budget and prints both in the review table; edit every draft against its entry. `check` warns on a cited entry with no `h`, and `--strict` makes it an error. The renderer's fallback is the first four words of `t` plus an ellipsis, which reads as a truncation; a doc never ships on it.

## Pull request links

A decision, an open item, and a finding row each carry `links[]`: the pull requests, issues, commits, or docs that land them. One link per entry, on the pull request that lands it, added the moment it opens. Never a list of everything that touched the area: a reader wants to know where the decision became code, not the history of the directory. `closes: true` goes only on an open item, and only on the one link that retires it; a decision's links say where it landed and close nothing, and a finding's link is the fix, so it needs no flag. A bare URL is enough for a link into `meta.repo`, the doc's default repository: the chip reads `#17567`, and the hover card fetches the title. A link anywhere else carries a `label`, a chip's worth of words that follows the capitalisation rules below and reads inside the row ("the edge rejection PR" reads, "PR" does not).

The handle and the twin stay as they were. A link is evidence, not wording: an item whose pull request merged keeps the handle every citation shows for it, and its twin still says what the item asked, not that it shipped. The row says "Closed" on its own, from `s: "closed"` or from a merged `closes` link, whichever comes first. Set `s` when the pull request merges anyway, because the register is read offline too; `design.py links --fetch` reports the two disagreeing.

## Titles are answers

A decision's `t` is the decision as a noun phrase of at most ten words, never the question it settled. The spotlight headlines `t`, so a reader who sees a question has to open the card to learn the answer. The question lives in `rounds[].q`, where the record wants it. `check` warns on a `t` ending in `?` or running past twelve words.

<examples>
<example label="question">
"Is Cloudflare inside the threat model for tenant traffic?"
</example>
<example label="answer">
"Cloudflare trusted for TLS, distrusted for identity"
The reader has the decision before reading a word of the body.
</example>
</examples>

Assumption titles are statements already ("Every consumer tolerates a duplicate"); open-item titles name what is unknown ("Which archive window the compliance team accepts").

## Evidence

An assumption's `b`, a finding's evidence line in the review file, and a spike's description share one shape, in this order: the `path:line` (or the query, or the person and round) that shows it; what that line shows, in plain words; the one check that would confirm or refute it, as a sentence that starts "Check:"; and the research note or reviewer round in parentheses, last. The path comes first because the renderer turns it into a link with a preview, and a reader who follows it wants the sentence that tells them what they are looking at. The check is one action with an owner-shaped verb ("run istioctl proxy-config on a live plat pod"), never "verify". An assumption that needs someone to confirm has to name its check; one that is assumed may stop after what the line shows. `check` warns on a path that is not first, on a round that is not last, and on a `validate` assumption with no "Check:" sentence.

<examples>
<example label="evidence as a note">
"dashboard/src/lib/relayEnvironment.ts:35 calls getApiClient() with no options (R2 §4). Confirm against plat Envoy access logs for the tenant team."
The round sits mid-sentence, the line is never explained, and the check has no owner-shaped verb.
</example>
<example label="evidence in shape">
"dashboard/src/lib/relayEnvironment.ts:35: the dashboard's main client is built with no options, so no team header is set. Check: filter plat's Envoy access logs to the tenant team's users; a hit there confirms it (R2 §4)."
</example>
</examples>

## Capitalisation

The renderer never rewrites a label, so the data carries the case. Acronyms are upper-case everywhere outside code spans: API, TLS, JWT, RLS, NLB, EBS, CLI, SSO, IAM, RDS, CI, S3, R2, SNI, WAF, DDoS, DPoP, GraphQL, JSON, SQL, HTTP. Product names are written as their owners write them: Cloudflare, Envoy, Postgres, Restate, Valkey, WorkOS, Datadog. A literal identifier (`api-runtime`, `x-team-id`, `tnt-usw2-0ddq7rb`) stays as it is, inside backticks. Every other label is sentence case, first letter upper and the rest lower: "Tenant API", "Per-cluster public edge", "Where it breaks".

A label is a Mermaid node's first line, an `.xs-node` name, an `.xs-card` heading, a chip, a table head, or the name of a theme or open group. Running prose follows ordinary English and the same acronym and product rules. `check` warns on a word that matches the acronym list case-insensitively but not exactly ("api", "Jwt") and on a label whose first character is lower-case outside backticks. The list lives in `design.py` beside the pinned lucide set, and `meta.acronyms` extends it for a doc's own initialisms.

## Terms

`terms` is the glossary the doc links from. The first occurrence of a term's `k` or one of its `aliases` in each paragraph, list item, or cell gets a dotted underline and the definition on hover; the Terms section shows the first six as cards with the rest behind "All N terms".

Write `k` as the prose writes it and list every other spelling in `aliases`, so "RLS" and "row-level security" resolve to one card. Keep `v` under twenty words and the glossary under twelve terms; past that the section is a second document. A term shorter than five characters links only when it is all-caps or two words, so `k: "log"` never links and `k: "job log"` does. `check` warns on a dead term, one no prose field uses. `design.py glossary` prints the candidates the prose uses and the glossary lacks, the author-side twin of the reader's "Find undefined terms".

## The overview diagram

`diagram.source` is the whole system, and the Overview card no longer draws it. It draws `diagram.overview`, a second Mermaid block of at most ten nodes whose ids are a subset of the full source's, with "View full size" opening the full graph in the modal. Each arch card draws its own node and its direct edges from the full source, so the overview owes the reader the shape, not the parts.

Leave out rollout and control edges (the dotted ones the full graph hides by default), per-cluster and per-region duplicates, observability, and any node an arch card explains on its own. Label first lines follow the capitalisation rules. `check` errors on an overview id the full graph lacks. `diagram.caption` becomes the SVG's `aria-label`, so write it as the sentence a reader who cannot see the figure needs, and end it on the decision that shaped the diagram, cited in parentheses; `check` errors on a caption that cites no decision.

The system diagram draws the proposed system, by role. A node is a role in the design ("Tenant edge", "Shared Postgres"), and its label is that role and, on a second line, the one thing about it the design decides ("aud check, DPoP proof"). An edge is labelled by what flows along it ("verified token forwarded", "tenant role"). Today's state lives only in a compare figure, beside the proposal, where the contrast is the point. So a node label never carries a status word ("unproven", "observed", "pending"), a count ("37 pods", "3 replicas"), or a finding from the research; those belong to an assumption's evidence, a numbers table, or a `dd.lanes` box, where they serve a decision. The same rule holds for every Mermaid block in the deck. `check` warns on a label that breaks it: a first line over four words, a number, a status word, or a `?`, on a node or an edge, in `source`, `overview`, and the deck alike.

## The executive summary

`summary.html` is the page for the reader who reads nothing else, and the one place to spend the strongest sentences: it fills their first screen, and for most reviewers it is the only one. It is a deck of full-screen panels, each `<section class="xs-panel" data-kind="…">`, four by default and five at most, `thesis` first. Each panel carries one idea and one figure. The word budgets, which `check` enforces, count everything but the figures and code:

| Panel | Carries | Budget |
|---|---|---|
| `thesis` | a headline, one sentence on what this is and why now, and the hero figure | 40 words |
| `compare` | for a change, before and after topic by topic; for a net-new design, each requirement and its answer | 120 words |
| `numbers` | two to four stats, each measured or tagged estimated with the spike that will measure it | 60 words |
| `cost` | two short columns, "What this costs" and "Where to push back" | 90 words |
| the deck | | 350 words |

The diagnosis in NOTES.md settles the `compare` panel. A change to a system that exists today puts today's behaviour and the proposal's side by side as parallel sentences ("Every worker scans the table every 500 ms" / "A worker holds a long-poll and receives a lease"). A net-new design, with nothing behind it but requirements, lists each requirement and, beside it, the answer and its trade-off.

The compare panel stacks: the rows run the full panel width, three columns each (topic, today, proposed), and the figure sits under them, scaled to fit. Past seven rows the panel runs longer than one screen, which the layout allows, so the count is the author's call: keep the topics that changed and fold the rest into the figure. Under 900px each row stacks its two clauses with "Today" and "Proposed" eyebrows, so a clause has to read on its own.

When the design is one idea with one figure, write a single `poster` panel instead of a deck: one composed figure of at most 180 words, with prose only as box labels, sublabels, and legend cells. State the choice in NOTES.md.

Four rules hold on every panel. Every sentence has a verb. No sentence starts with a register ID; IDs trail as citations, and the finding and pass vocabulary ("finding 53", "pass-5") never appears. Every number is measured or tagged estimated. Nothing appears that the registers don't back ([method.md](method.md)). The panel kinds and the kit classes in [schema.md](schema.md) are the whole vocabulary; beyond them, any HTML the panel needs, drawn for this design, never decorative. `design.py summary-text <dir>` prints the deck as Markdown with one `##` section per panel, which is what the gates read.

## Figures

Every panel has a figure, and every figure is drawn for this design: a diagram of these parts, a chart of these numbers, never a stock shape and never a screenshot of text. Pick the form by what the figure shows:

- Mermaid (`<pre class="mermaid">`) for flows and request paths.
- `dd.flow` or `dd.lanes`, from the Components table, for the compare panel.
- The poster primitives (`.xs-lane`, `.xs-node`, `.xs-group`, `.xs-connect`, `.xs-legend`) for object hierarchies and today-versus-next structure on a poster.
- Inline SVG when none fits.

No figure is drawn before its brief. The brief is five lines in NOTES.md under "Figure briefs", one block per figure: the point the figure makes, in one sentence; the decision it serves, by ID; the smallest view that makes the point (a diff of the path when the surrounding shape already exists, one lane when the change is one part, the whole system only for the thesis); what it leaves out, named, so the omission is a choice; and the real labels it will use, from the registers, never placeholders. The brief's first two lines become the figure's `<figcaption>`. A figure whose brief cannot name a decision is not drawn. Keep only the parts, boundaries, and flows the point needs; a figure that shows everything the research found answers no question.

The compare panel's figure is a kit component, declared in `components` and placed with `<div data-component="…">` inside the panel's `<figure>`. `dd.flow` draws stages as columns with callouts hanging off them; it fits a change whose story is the path and where it breaks. A callout's `title` names the thing that leaks or breaks at that stage, in plain words a reader outside the team can check ("The dashboard sends no team header", "Plat re-proxies over port 80"). Its `status` is `fixed` when this design closes it and `later` when it stays for a follow-up, and `by` is a register id, shown as its handle, or a short phrase naming what fixes it. The renderer writes the legend from the two statuses; the declaration carries none, and no callout text starts with "closes" or "stays open".

`dd.lanes` draws today beside proposed as two framed stacks of boxes; it fits a change whose story is which parts become which. Both measure their labels in the page and lay themselves out, so a column widens to its callouts and a lane to its boxes, and nothing overlaps at 390 px or 1440 px. A hand-drawn SVG is for a shape neither kind draws; `check` warns on a compare figure drawn by hand with eight or more boxes and names the kind it should be.

Read `reference/gallery/` before drafting: `deck.html` is a four-panel deck whose numbers panel hosts a `dd.whatif` component, and `poster.html` a complete poster, both rendered by the template with no doc-specific data. Copy structure, never content. Every figure carries both: an `aria-label` that says what is drawn, for the reader who cannot see it, and a `<figcaption>` that states the claim the figure makes and cites the decision it serves. `check` warns on a deck figure with no caption and on a caption that cites nothing.

## Components

A figure moves when a number or a sequence earns it: a number the reader would want to change ("what if the token lived four hours") or a sequence the reader would want to step through (login, exchange, cluster request). Everything else stays a figure; a widget that animates a fact the reader can read is decoration, and one or two components per doc is the ceiling before the page reads as a demo. Each component is a rendering of register data ([method.md](method.md)), declared once in the `components` map and placed by id:

| Need | Kind | Where it belongs |
|---|---|---|
| one card body with two or three views (request path, rollout, failure modes) | `dd.tabs` | an arch card; a compare panel under 900px |
| a path that changes shape | `dd.before-after` | the thesis panel; an arch card whose path changes |
| a number the reader should move | `dd.whatif` | Numbers; a ceiling row's guard |
| a sequence the reader should step through on the diagram | `dd.steps` | How it works; drives the system diagram's highlight |
| phases with gates | `dd.timeline` | the rollout card; the top of Still open |
| options against criteria | `dd.matrix` | a decision's rejected alternatives; a compare with more than two lanes |
| stages in columns, with what each one leaks or fixes hanging off it | `dd.flow` | the compare panel of a change whose story is where the path breaks |
| today beside proposed, as two stacks of boxes | `dd.lanes` | the compare panel of a change whose story is which parts change |

`dd.flow` and `dd.lanes` are figures that lay themselves out, not widgets, so they sit outside the one-or-two ceiling above. A doc with a compare panel has one of them, plus one or two of the others where a number or a sequence earns it.

The declaration sits in `registers.json`; the deck places it with `<div data-component="id"></div>` inside a panel's `<figure>`, and the registers place it through `arch[].component`, `numbers[].component`, or `meta.ceilingsComponent`. A `dd.whatif` for the gallery deck's numbers panel:

```json
"components": {
  "backlog-cost": {
    "kind": "dd.whatif",
    "title": "What a backlog costs per second",
    "inputs": [
      {"id": "backlog", "label": "Jobs waiting", "min": 100, "max": 100000, "step": 100, "value": 10000, "unit": "jobs"},
      {"id": "workers", "label": "Workers", "min": 1, "max": 200, "step": 1, "value": 40}
    ],
    "outputs": [
      {"label": "Rows scanned per second today", "expr": "backlog * workers / 5", "unit": "rows/s", "tone": "warn"},
      {"label": "Open connections proposed", "expr": "workers", "tone": "ok"}
    ],
    "cites": ["DQ4"]
  }
}
```

`expr` is arithmetic over the input ids (`+ - * / ( ) min max`), never code. `check` validates every declaration against `reference/components/<kind>.json`, errors on a `data-component` that names no entry, and evaluates a `dd.whatif` at its sample inputs. Each kind flattens to Markdown for the export and prints as its first state, so a kit component needs no fallback of its own. The gallery keeps a static SVG beside the host so the figure reads with the component removed, and the page hides that SVG once the component mounts.

When the kit cannot express the figure, write a Preact component in `components/<name>.tsx`. `design.py build` compiles the directory into `components.js` beside `index.html`, and `check` refuses a source that calls `fetch`, imports from another origin, uses `eval`, or writes `innerHTML`. An author-written component takes the registers as a prop and must carry a `<figure>` fallback in the host, which print and the Markdown export use in its place; place that host beside a deck panel's `<figure>`, never inside it, so neither figure loses its label. It is the escape hatch, not a second kit: anything bound to a register value goes through a kit kind so `check` can validate it.

## Suggested prompts

`meta.ai.suggest` maps a section id to two prompts the assistant panel offers while that section is on screen, after its own "TL;DR" of the section. Write each as the question a cold reader asks at that point, answerable from the document, under ten words, with no IDs: `"ceilings": ["What breaks first at ten times today's load?", "Which guard is still an estimate?"]`. The built-in set already asks the open list what has shipped, so a doc with links needs no prompt for that. The doc answers from the document and says when it cannot, so a prompt that needs knowledge the document lacks produces a shrug, not an answer; cut it.

The follow-ups the assistant offers under an answer obey the same rule: each names something the document contains, by the words the document uses, and is answerable from it. The renderer hands the model the section list and every handle and tells it to pick from those, and it drops a follow-up whose handles resolve to nothing. A follow-up about a thing the document never says ("the two-year rollout", when an assumption says only that the design serves 10 to 50 clusters over two years) is extrapolation, and the prompt forbids it.

## The two passes

Write the content in two separate passes, in order; combining them produces prose that is half-fixed on both axes:

1. **Structure and de-jargoning.** Everything in its right section, every term defined before use, every claim traceable to a register entry.
2. **Tone.** Reread every sentence asking "is this explaining, or performing?" Kill superlatives, hedge-stacks, and any sentence whose subject is the work rather than the system.

The critique pass runs between them: one NOTES.md row per panel, figure, table, and stat, naming the decision it serves, the claim it makes, the question a reader is left with, and whether it was fixed or cut. A row with no decision is cut, and the tone pass reads only what survives.

Run `slop-cop check <file> --llm-effort=off` after each pass and triage: fix the genuine tells, keep deliberate constructions (range dashes in "1–2ms", glossary dashes) with a clear conscience.

## Voice

Five gates run over the summary, in this order, and each is a required pass. The doc's other prose and the revision notes take gates 2 and 3.

**Gate 1, the edit passes.** Load the `writing-docs` skill by path, `~/.claude/plugins/cache/skills/writing-docs/0.7.0/skills/writing-docs/SKILL.md`, and run its edit passes over the deck in its order: completeness, then accuracy, then structure, then clarity, then brevity. The accuracy pass checks every number and citation against the registers.

**Gate 2, the voice gate**, run for the doc's prose and for revision notes alike, when it runs. `wlm adversary critique` needs machine negatives before it completes; when it exits without a verdict, the fallback contract below applies, and NOTES.md records which of the two graded the doc.

1. Run `wlm profile list`, always (the `wlm` CLI ships with the write-like-me plugin; profiles live in `~/.wlm/profiles/`). When it lists no profile, apply the fallback contract below.
2. With a profile, read the style card before drafting: `wlm -p <profile> stylecard show` (`-p` is a global option and comes before the subcommand). Write against it — the doc should sound like the person proposing, not like a model.
3. During the tone pass, run `design.py summary-text <dir> > summary.md` and `wlm -p <profile> adversary critique summary.md` for a discriminator-panel critique against the author's real writing; the summary is where most readers stop, so it goes first. Then export the doc's Markdown and run the same critique over it. Fold in each flag or reject it with a reason; a critique nobody reads is a skipped gate.
4. Revision notes ride the same rail: write the drafted `--note` headline and `--item` bullets to a scratch file, critique that file, and only then stamp the snapshot.

**Gate 3, slop-cop.** `slop-cop check summary.html --lang=html --llm-effort=off` over the fragment, then `slop-cop check summary.md --lang=markdown --llm-effort=off` over the `summary-text` output; the HTML pass masks tags, so the Markdown pass is the one that reads the sentences. Triage every finding in NOTES.md, fixed or rejected with a reason.

**Gate 4, `check --strict`.** `design.py check --strict <dir>` turns every publish-blocking warning into an error: a cited entry with no handle, a question-form decision title, a capitalisation slip, a field over its word cap, component props that fail their kind's schema, an overview node the full graph lacks.

**Gate 5, `render-check`.** `design.py render-check <dir>` proves every Mermaid block and every component draws in headless Chrome. A doc ships only when both mechanical gates exit 0.

When there is no profile, this fallback contract applies:

- Contractions everywhere they'd be spoken. Short declaratives over subordinate-clause towers.
- Numbers over adjectives; when there's no number, say what was observed instead of grading it.
- Em-dashes rarely, and never as a comma substitute mid-list; semicolons only to pair two genuinely contrasted clauses.
- Backtick tool and file names (`design-doc.html`, `wrangler`).
- No throat-clearing openers ("It's worth noting that…"), no summary paragraphs restating the section above, none of the LLM tells slop-cop exists to catch.

## The interface is part of the voice

- Theme follows the system (`prefers-color-scheme`), with both palettes designed: dark is its own surface, not the light palette with swapped tokens. A theme toggle is a control asking for attention the content should have.
- Controls are minimal and literal: the Markdown export is a download glyph labelled "Markdown", the "Full wording" toggle swaps twins for exact wording, and the PDF button says "PDF" and prints the page through the template's print stylesheet.
- The PDF is the same page, printed: the deck flattened to headed sections in panel order, every entry open, the diagrams rendered, page breaks kept out of cards and tables. `design.py pdf` prints it through the same stylesheet, so the file people forward matches what the button gives them.
