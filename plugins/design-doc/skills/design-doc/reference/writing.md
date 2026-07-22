# The writing contract

The doc is a proposal asking for feedback, not a launch page. The reader should finish it knowing what is being proposed, what it costs, what could be wrong, and where their judgment is wanted. Every sentence that exists to impress rather than explain is a sentence between the reader and that outcome.

## Stance

Write to explain and to ask, never to convince. The strongest sentence in a humble proposal names its own weak point: "If A1 falls, the answer is probably a managed Postgres, not this document." Confidence lives in the specificity of the numbers and the honesty of the open list, not in adjectives.

<examples>
<example label="selling">
"A blazingly fast, rock-solid commit protocol delivers bulletproof durability."
Adjectives making claims the reader can't check.
</example>
<example label="explaining">
"The ack returns after the redo fsync and the SQLite commit both land — ~2–3ms p50 (E), gated on spike V9."
A number, its conditions, and the experiment that will check it.
</example>
</examples>

## Structure rules

- Open with a tl;dr: a handful of bullets a reader can absorb in thirty seconds, with links for every technology named. A mission-statement paragraph makes the reader work for what the bullets hand over.
- Definitions before use. Ground rules and a terms glossary come before the architecture; internal shorthand gets a plain-language name at first appearance ("stale-ok read" first, the internal enum name in parentheses if at all). A term the reader has to reverse-engineer is a small tax charged on every later sentence.
- Plain section names that say what the section is: "Timings", "Request paths", "Open items". A clever name costs a beat of decoding on every visit to the nav.
- Deep mechanics go in numbered footnotes (`[^n]`). The body stays readable at a walking pace; the footnotes reward the reader who wants the commit-ordering argument.
- Cut captions that restate what the eye already sees. Under a list visibly grouped by owner, "Grouped by who can answer them" says nothing.
- Counts describe the system, not the effort. "27 decisions" and "9 spikes pending" are progress-report numbers; a design doc reader needs neither.

## The two passes

Write the content in two separate passes, in order; combining them produces prose that is half-fixed on both axes:

1. **Structure and de-jargoning.** Everything in its right section, every term defined before use, every claim traceable to a register entry.
2. **Tone.** Reread every sentence asking "is this explaining, or performing?" Kill superlatives, hedge-stacks, and any sentence whose subject is the work rather than the system.

Run `slop-cop check <file>` after each pass and triage: fix the genuine tells, keep deliberate constructions (range dashes in "1–2ms", glossary dashes) with a clear conscience.

## Voice

When the `wlm` CLI and a voice profile for the author exist (check `~/.wlm/profiles/`), read the profile's style card and write against it: the doc should sound like the person proposing, not like a model. When there is no profile, this fallback contract applies:

- Contractions everywhere they'd be spoken. Short declaratives over subordinate-clause towers.
- Numbers over adjectives; when there's no number, say what was observed instead of grading it.
- Em-dashes rarely, and never as a comma substitute mid-list; semicolons only to pair two genuinely contrasted clauses.
- Backtick tool and file names (`build-pdf.py`, `wrangler`).
- No throat-clearing openers ("It's worth noting that…"), no summary paragraphs restating the section above, none of the LLM tells slop-cop exists to catch.

## The interface is part of the voice

- Theme follows the system (`prefers-color-scheme`), with both palettes tuned. A theme toggle is a control asking for attention the content should have.
- Controls are minimal and literal: the Markdown export is a bare `↓`, the PDF button says "PDF" and opens the generated file. `window.print()` produces a cut-off page-print and is not a PDF.
- The PDF is a separate linear rendering (`build-pdf.py`) of the same JSON — a real document with the diagram and page-break discipline, because that's the artifact people forward.
