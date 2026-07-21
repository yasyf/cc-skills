# Worked example: the captain-hook docs rebuild

One real rebuild, end to end, with the artifacts each stage produced. The subject is captain-hook, a Python hook framework for Claude Code whose docs had grown a page per feature. Every quote below is from the rebuild's own audits, work orders, and shipped pages.

## The starting state

A 22-page guide as a flat link directory, a four-page getting-started track, a four-page cheatsheet — individually plausible, collectively unnavigable. And quietly wrong in places: one page's transcript-query sections documented an API surface that no longer existed at all (~65 of 143 lines dead — `t.tool_uses`, `.where(name=...)`, `edits.list()`, none of them real), four example files taught a command surface slated for deletion, and a `pack list` sample showed hook counts from months earlier. Nothing failed when any of this drifted; that is the disease the process treats.

## Stage 1: parallel audits

Audit lanes fanned out per merge cluster (eleven digests total), each verifying its pages line by line against pinned source. The digests followed the DEFECT/GEM/OVERLAP grammar in `consolidation.md`, and their precision is what made everything downstream mechanical:

- Defects came with runtime proof: "confirmed via runtime repro: `Signal(r'retry', weight=2)` raises `TypeError` … at least 8 example calls in this file pass `pattern` positionally and would crash if run."
- Gems came with their own verification: "verified verbatim-accurate against captain_hook/signals/__init__.py's transcript_texts() docstring."
- Condition wasn't assumed uniform: one cluster's summary opens "llm-hooks.qmd is in excellent shape (every primitive signature, default, verdict-model field, and link verified against source with zero defects)" and, of its sibling, "roughly half the page … documents an API surface that no longer exists at all; this needs a from-source rewrite, not a copy-edit."

## Stage 2: the merge map

Every page got a verdict and a WHY (the full table pattern is in `consolidation.md`). The shape of the outcome:

- Three-page clusters merged into single task-framed pages: "LLM hooks, signals & the transcript" (`aliases: [signals.html, transcript.html]`), "Packs & configuration" (`aliases: [plugin-packs.html, configuration.html]`), "Under the hood" (`aliases: [philosophy.html, security.html, daemon.html]`).
- `guide/index.qmd` was killed and replaced by the section's generated card grid (`index: true`) — a generated index cannot drift.
- `limitations.qmd` stayed standalone against the audit's own merge suggestion, because "its value is precisely in being a blunt, standalone 'no' list." `troubleshooting.qmd` stayed too: operational rather than conceptual, with an inbound deep anchor to protect.
- Getting-started collapsed to one quickstart carrying `aliases: [index.html, installation.html, project-structure.html, skills.html]`; the cheatsheet's three subpages folded into one page the same way.

Result: a 22-page guide became 10 pages, every killed URL redirected.

## Stage 3: drafting

The lead writer rewrote every merged page as one piece in a drafts tree, off to the side of the live docs. Merged pages got authored leads ("Packs bundle hooks you install once and get everywhere. Exactly two providers exist …"), task-phrased headings ("Inspect and rewrite commands", "Auto-answer permission dialogs"), deduped See-also blocks, and the audits' gems placed byte-exact. The one mechanical assembly — the packs merge, whose three sources the audit had verified fresh — still got an authored lead plus four surgical fixes specified with exact replacement text.

## Stage 4: slices, gated

Delegated slices landed the drafts under work orders (`work-orders.md` dissects two of them): verbatim landing, file ops, surgical fixes, gates, ship. The gates caught real defects that had survived both drafting and auditing:

- **Reflection gate**: the draft documented `turn.user_text` / `turn.edited_files`; the real API was `turn.prompt` / `turn.edits`. The audit had verified the old names against the pinned dependency — the dependency then moved before landing. Verification has a timestamp; the gate runs at landing.
- **Regenerated output**: the `pack list` excerpt claimed "2 hooks" where the live run said 3, and "4 hooks" where it said 32.
- **Parity rows**: wiring the tutorial's demo widgets to the real engine disproved the plan's own claim — the parser descended into `sh -c "git stash"` and blocked it, where the plan had assumed the wrapper escaped. The page's behavior table was corrected to teach the engine's actual two-direction lesson.
- **Byte checks**: a Mermaid lifecycle diagram was diffed against the draft after landing, because formatters mangle what nobody re-reads.

Each slice ended with the link sweep over an explicit retarget map — spanning `docs/`, `README.md`, the landing page, and the bundled skills, where live hits turned up — and shipped only at zero killed-page references.

## Stage 5: the front door

With the guide coherent, the README and landing page were rebuilt on the conversion skeleton (`readme.md`):

- Opener: "**Stop repeating yourself to Claude.** captain-hook mines your transcripts for the corrections you keep giving and opens PRs that turn each one into a typed, tested Python hook." Pain named in the reader's words, zero hype adjectives.
- One get-started path (`uvx capt-hook init`) with a recorded demo of a real run directly beneath it, then the agent-paste block (plugin commands inline, the prompt alternative in `<details>`).
- Use cases as H3 goals — "Block force-push and rm -rf before they run", "Gate 'done' until the tests actually pass" — each showing real code and the observed behavior: "The next `git push --force` never executes: the agent sees `BLOCKED: Force-pushing rewrites shared history` plus the hint, and reaches for `--force-with-lease` instead."
- The landing page tells the same story with the same use cases, synced by hand; the README renders raw on GitHub, so it carries no shortcodes or embed markers.
- Site-wide command convention, stated once on the landing page: "Every command on this site is the full `uvx capt-hook ...` form — copy-paste from a clean machine and it runs."

## What to copy

The audit grammar, the merge map with mandatory WHYs, rewriting over concatenation, aliases plus a mapped link sweep, gates that execute claims instead of re-reading them, and work orders that keep every shipped sentence the lead writer's. The specific page set is captain-hook's; the process is not.
