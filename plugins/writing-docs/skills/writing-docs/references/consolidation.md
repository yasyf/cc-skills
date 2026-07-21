# Consolidation: rebuilding a docs set by rewriting

When a docs set has grown one page per feature — every page individually true-ish, the set collectively unnavigable — the fix is a rebuild: audit everything, decide every page's fate explicitly, and rewrite each cluster into one coherent page. Concatenation is not consolidation: gluing three pages under one title preserves three leads, three See-also blocks, and three voices, and the reader still reads three pages. The unit of work is the rewritten page; the audits and the merge map exist so the rewrite loses nothing while it cuts.

The examples below come from the captain-hook docs rebuild, which collapsed a 22-page guide to 10 task-titled pages; `worked-example.md` walks that rebuild end to end.

## Stage 1: parallel per-page audits

Fan out one audit lane per planned merge cluster — pages that might merge get audited together, because their overlap is part of the finding. Each lane reads its pages line by line against the pinned source tree (never against memory, never against the docs' own claims) and returns a digest in this shape:

```markdown
# Audit digest — merge target: <cluster>

## Cluster summary
<how the pages relate, their overall condition, the one-line consolidation move>

## docs/guide/<page>.qmd — verdict: merge -> <target> | keep | kill
- DEFECT: <file:line — what is wrong, and how you verified it>
- GEM: <file:line — what must survive the rewrite, byte-exact>
- OVERLAP: <sibling page — what the two state twice>
```

Three line types, each with a job:

- **DEFECT** carries the evidence, not just the claim. A real one, from `_audit/llm-signals.md` in the captain-hook rebuild: "`Signal` is declared `@dataclass(frozen=True, kw_only=True, slots=True)` in captain_hook/types.py:691 … confirmed via runtime repro: `Signal(r'retry', weight=2)` raises `TypeError` … Yet at least 8 example calls in this file pass `pattern` positionally and would crash if run." The auditor ran the failing call; the digest names every affected line. A defect line without a verification method is a suspicion, not an audit finding.
- **GEM** marks prose that must survive the rewrite byte-exact, cited by file:line so the rewriter can lift it rather than re-derive it. Gems are usually hard-won operational knowledge: "Callout: 'An un-stamped UserPromptSubmit hook is silently dead' — a sharp, hard-won footgun warning (window=0 + default origin='assistant' on UserPromptSubmit yields zero candidates, never fires, never errors) that must survive verbatim." Verify gems too — the strongest audits annotate each gem with how it was checked ("verified verbatim-accurate against captain_hook/signals/__init__.py").
- **OVERLAP** names what the cluster states twice, so the merged page can state it once.

An audit that only lists defects has done half the job. The gems are what stop a rewrite from flattening ten pages of accumulated knowledge into competent generic prose — the failure mode of every "clean up the docs" pass that starts from a blank buffer.

## Stage 2: the merge map

Every page gets exactly one verdict — merge into a named target, kill, or stay standalone — and every verdict gets a WHY. The map is the decision record: audits propose, the map disposes, and a slice executor never re-litigates a verdict mid-slice. Real rows from the captain-hook map:

| Pages | Verdict | Why |
|---|---|---|
| llm-hooks + signals + transcript | merge → "LLM hooks, signals & the transcript" | Each already links the other two as "see also"; the merge is reorganization, not synthesis |
| packs + plugin-packs + configuration | merge → "Packs & configuration" | Three facets of one system: use → author → configure |
| how-it-works + philosophy + security + daemon | merge → "Under the hood" | The guide's conceptual layer, one page |
| guide/index.qmd | kill | A flat 22-link directory; the section's `index: true` generates the card grid from surviving pages, and a generated index cannot drift |
| limitations.qmd | stays standalone | Its value IS the blunt no-list — "this entire page should survive close to verbatim rather than be diluted across a merged page, since its value is precisely in being a blunt, standalone 'no' list" |
| troubleshooting.qmd | stays standalone | Operational, not conceptual; carries an inbound deep anchor other pages link into |

The limitations row is the one to study: the audit's own verdict line said "merge -> Under the hood", but its gem note argued the opposite, and the map ruled standalone. Verdicts are judgment calls the map owner makes with the audit evidence in hand — which is why the WHY column is mandatory. A map without reasons can't be reviewed, and six months later nobody can tell a deliberate keep from an oversight.

## Stage 3: rewrite the merged page as one piece

A merged page is written, not assembled. The rules:

- Author a new lead that frames the merged whole. The captain-hook packs merge opens: "Packs bundle hooks you install once and get everywhere. Exactly two providers exist … This page covers using the packs you have, shipping your own in a plugin, and tuning hook settings." No source page could open the merged page; the lead is new prose.
- Demote source-page headings a level and re-title them as tasks where they aren't already (`diataxis.md`, Findability).
- Dedupe the See-also blocks into one; cross-references between the merged sources become same-page anchors.
- Fix every audit defect in flight — the work order lists each one as a surgical fix with the replacement text (`work-orders.md`).

Mechanical concatenation is the bounded exception, allowed only when the audit verified every source fresh — and even then the lead is authored, the fixes are applied, and the result is re-read as one page. It is a floor for audit-clean material, never the default.

Every merged page carries a **gems-survival checklist** in its work order — the audit's gems, restated as landing criteria. From the captain-hook packs merge: "Gems that must survive byte-exact: builtin roster/activation policy + gitignore-pruning detail; local-vs-pack precedence rule; discovery-cache invalidation section incl. MDM/--plugin-dir bypass warning; scope-precedence/dedup rule; fatal-vs-skipped distinction; pack test exit-condition list; misfire routing …" At landing, search the merged page for each gem. A gem the rewrite dropped is a stop-and-report, not a shrug.

## Stage 4: aliases cover every killed URL

Every killed or absorbed page's URL must land somewhere that answers the same question. On Quarto/Great Docs that is `aliases:` frontmatter on the surviving page:

```yaml
title: "LLM hooks, signals & the transcript"
aliases: [signals.html, transcript.html]
```

Deep inbound links need anchors, not just a page: the merged page above carries explicit `{#score-patterns-with-signals}` and `{#query-the-transcript}` anchors so old section-level links keep resolving. Enumerate the killed URLs from the merge map — the map is the checklist — and give each one a home before the sweep.

## Stage 5: the link sweep

Retargeting runs off an explicit map, not per-hit judgment. The work order states both the search and the destinations:

```
git grep -nE "signals\.qmd|transcript\.qmd|workflows\.qmd|plugin-packs\.qmd|configuration\.qmd" \
  -- docs/ README.md index.qmd captain_hook/skills/
```

with the retarget map alongside: "signals/transcript → llm-hooks.qmd (+#score-patterns-with-signals / #query-the-transcript anchors), workflows → state.qmd (landing page index.qmd links workflows.qmd!), plugin-packs/configuration → packs.qmd (+anchors)."

Two rules make the sweep complete:

- Sweep beyond `docs/`. The README, the landing page, and any bundled skill or agent references link into the docs too — the captain-hook sweep covered `README.md`, `index.qmd`, and `captain_hook/skills/`, and found live hits in pages landed by the previous slice.
- The ship gate is zero: the final grep for killed page names returns nothing. "Zero killed-page references" is a countable claim, so count it.

## The degenerate case: rewriting one page

A single-page rewrite is the same process without the map: set a line-count reduction target, pick the page's one job, relocate everything else, and gate the result on a completeness diff against the prior version — a fact may move, it never vanishes. The gem discipline still applies: read the old page for what must survive before you cut, not after.
