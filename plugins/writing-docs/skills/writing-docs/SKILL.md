---
name: writing-docs
description: Use when writing or revising project documentation of any kind, including a README, docs site, tutorial, quickstart, how-to guide, reference page, API doc, cheat sheet, examples catalog, limitations or FAQ page, or changelog. Applies the Diataxis framework (one mode per page), a technical-builder voice (first-person, confident, hands-on), runnable code-sample rules, quickstart and README anatomy, standalone-README design, cheat-sheet and catalog page formats, accessibility, and a required slop-cop prose-lint pass before you finish. Triggers on "write/rewrite/improve the docs", "write a README", "write a tutorial", "document this", "getting-started page", "write a cheat sheet", "build an examples catalog", or any natural-language documentation task.
---

# Writing Docs

This skill is opinionated and checklist-driven. Documentation is not one thing. It is four kinds of page that serve four different reader needs, and the largest source of bad docs is letting those kinds bleed into each other. Pick the mode first, write to its rules, then run the pre-merge checklist at the end. A wrong doc is worse than a missing one, because readers trust and act on it.

Run `slop-cop check <file> --lang=markdown` on every page you touch and triage every finding: fix the genuine tells, keep deliberate voice moves. This is not optional.

## The process, five gates in order

1. **Understand the reader.** Name the persona, their goal, and what they already know. Do not draft until you can.
2. **Plan.** Pick the Diataxis mode below. Outline the headings before the prose.
3. **Draft.** Front-load with the inverted pyramid. Lead every page, section, and paragraph with the most important point. Include at least one runnable example.
4. **Edit in focused passes.** Completeness, then accuracy, then structure, then clarity, then brevity. Split the technical-correctness edit from the language edit.
5. **Maintain.** Stale docs are a bug. Keep them current with the code.

Hold every draft to the Write the Docs guardrails. The docs should be skimmable, exemplary so they always show an example, consistent, current, nearby so the source sits next to the code, unique so no two sources overlap, cumulative so prerequisites come first, and complete. Prefer ARID over strict DRY. Let a task page restate a key fact instead of forcing the reader to chase a link, but never duplicate whole sections. Link instead.

## Diataxis, one mode per page, never mixed

Classify the page before writing a line. The mode dictates the allowed content.

| Mode | Serves | Contains | Never contains |
|------|--------|----------|----------------|
| **Tutorial** | Learning by doing | One guaranteed-to-work path from zero to a first result | Alternatives, options, "you could also", deep why |
| **How-to** | Doing one real task | The shortest correct path for a competent reader | Concept teaching, exhaustive option lists |
| **Reference** | Looking a fact up | Accurate, complete, neutral tables and signatures | Narrative, opinion, step-by-step |
| **Explanation** | Understanding | Concepts, tradeoffs, history, the why | Step-by-step instructions, first statement of a fact |

Decide by the reader's goal. Learning-by-doing with guaranteed success is a tutorial; accomplishing one real task is a how-to; a fact to look up is reference; understanding a concept is explanation. If a tutorial makes you want to explain a concept, link out to an explanation page instead of inlining it. That is the top tutorial smell.

Map repo directories to modes. `getting-started/` is the single tutorial plus thin onboarding, `guide/` is how-to plus a few labelled concept pages, `examples/` is runnable how-to specimens, and `reference/` is generated and curated facts. Do not build empty four-folder scaffolding; assign each existing page one mode.

See `references/diataxis.md` for the required sections per page type. A cheat sheet and an examples catalog entry are formats of reference and how-to, not new modes — `references/reference-genres.md` gives each a skeleton and the rule that keeps a cheat sheet from blurring into a reference.

## Voice and style

Write as a technical builder: someone who has already built the thing, explains it clearly, and has opinions about what's good and what's a mess. The voice governs narrative prose — the README pitch and why-sections, explanation pages, and the framing around tutorial and how-to steps. Procedure steps stay imperative, and reference pages stay neutral facts (see the Diataxis table above). slop-cop flags some of these devices deliberately — see the slop-cop section for how to triage.

- Use "I" freely: "I built", "I found", "I wasn't satisfied with this." Address the reader as "you" when walking them through tradeoffs. Use "we" only for a shared technical reality, never as editorial we.
- State the point, then elaborate; never build toward a conclusion that could have led. Open sentences with the subject acting, and front-load every level.
- Vary sentence length aggressively: a 3-5 word fragment lands a verdict, a 30-40 word technical explanation follows when precision demands it. Anchor paragraphs with a short declarative, expand with mechanics, close with judgment or implication.
- Default to confident assertion, not hedging; scope any real uncertainty narrowly. Criticize bluntly, grounded in a specific technical failure. Enthusiasm comes through word choice, not exclamation points.
- Reach for hands-on vocabulary ("wrapping", "porting", "bolt on", "swap out") and casual intensifiers without apology ("pretty solid", "neat"). "YMMV," "n.b.," and "AFAIK" are natural register markers. Humor is dry and brief, in parentheticals or sentence-final fragments.
- Em-dashes carry interruptions and sharp asides; colons introduce technical specifics; rhetorical questions voice the skeptic before answering.
- Use active voice with an explicit actor and present tense; ban "will" and "would" for general behavior. Write procedure steps in the imperative: "Run the command."
- Use exactly one term per concept everywhere, with identical capitalization. Backtick any library, method, or tool name inline, plus filenames, paths, commands, and types — but not product names or browsable URLs.
- Use sentence-case headings with no end punctuation, the Oxford comma, conditions before instructions, and inclusive, bias-free language (no generic he or she, people-first phrasing, non-biased technical terms).
- Open on substance. Cut self-referential intros such as "This page explains" or "What to learn", structure pre-announcements such as "three properties follow", and pre-emptive admonishment. State behavior as fact, and keep internals, packaging, and history out of task and reference pages. Cut "simply", "just", and "easy" from procedure steps.
- Bridge an abstract concept to a familiar domain before the mechanics ("rules are CSS selectors for code"), and name where the analogy breaks. The inverse of the admonishment ban: a genuine footgun earns a warning callout, stated flat, one per real edge.

See `references/voice-and-style.md` for the full list.

## Code samples

- Make each sample complete and copy-pasteable. Include imports and setup, and never paste a fragment that errors.
- One intro sentence before each sample saying what it does.
- Keep it minimal. Mark an omission with a real language comment, never a bare ellipsis.
- Show real, verified output. Normalize variable values such as timestamps and addresses; never fake them.
- Contrast success against error when behavior differs on bad input.
- Use safe fictional placeholders such as example.com, RFC-reserved IPs, and USER_ID. Never real PII, secrets, or credentials.
- No pseudocode in user-facing docs. Write real code in the target language.

## Runnable and tested docs

Keep example code in real source files that are under test, and embed it into the page instead of retyping. One source means the rendered page and the tested code cannot diverge. Make the examples run in CI so a stale example fails the build. If the page shows output, generate it by running the code.

State the run convention once and use it verbatim everywhere. For a tool distributed through uvx or pipx, every command is the full ephemeral form such as `uvx <tool> ...`, never the bare binary, except where a config file must hold it. Leading with the ephemeral invocation is the uv and Ruff house style. For such a tool the ephemeral invocation *is* the install section: a single line such as `uvx <tool> --help` or the first real command. Drop the ceremony around it — no "no install needed" preamble, no gloss on what the runner does ("fetches it into a throwaway environment and runs it"), and no "to add it to a project instead, `uv add <tool>`" alternative unless the tool is genuinely consumed as a library. A reader running `uvx` already knows what it does.

Close the gap between reading and running. Give every substantial example a one-command way to run it, a copy button, or a prefilled playground link, so the reader executes it without reconstructing setup.

## Quickstart and tutorial design

A quickstart is a Diataxis tutorial with one language, one use case, and zero branches. State the destination and a time budget up front, as in "Write your first hook in under five minutes". End in one tangible, verifiable outcome the reader can see. Aim for first success under five minutes and at most around ten steps. Give complete copy-paste snippets that say exactly where the code goes, surface every needed value inline, and show the expected output so the reader self-verifies. Defer concepts; give the most minimal inline reason and link to depth. Test the quickstart on a clean machine each release.

## README anatomy

Order the README broad to specific, by cognitive funnel. Lead with the title, badges, and a one-line pitch under 120 characters, then what and why, install, the smallest working example with its output, deeper usage, a documentation link, contributing, and the license as SPDX. The README is the front door, not the house. Link to the docs site for full usage and API reference; never duplicate them, so there is one source of truth. The minimum viable docs set is README, LICENSE, CONTRIBUTING, and CHANGELOG. Use relative links within the repo and badges for live status instead of prose claims. When the README is the only doc — no docs site behind the front door — the "link to the docs site" rule has no referent; see `references/standalone-readme.md` for the standalone case, where the README carries its reference tables in-file and defers the exhaustive flag list to the tool's own `--help`.

## Maintenance, accessibility, and the changelog

- Keep a CHANGELOG by hand in Keep a Changelog format, latest first, grouped Added, Changed, Deprecated, Removed, Fixed, Security, with an Unreleased section on top. Never dump a git log.
- Deprecate with an in-docs notice, at least one minor release of overlap, and a migration path.
- Make accessibility lint-able. Use exactly one h1, no skipped heading levels, descriptive link text and never "click here", alt text on every image, a text equivalent for every diagram, tables introduced in prose with header cells, no color-only signaling, and acronyms expanded on first use. Avoid directional words like "above".
- Run slop-cop in CI as a report (the voice keeps some flagged devices on purpose, so it's not a hard gate), run Vale where configured, and check links.

## Run slop-cop (required)

Before you call any doc done, run it through slop-cop and triage every finding.

```bash
slop-cop check path/to/page.md --lang=markdown
```

slop-cop is a Go binary (the `slop-cop` plugin, GitHub Releases, or `go install`), not a PyPI package — never `uvx slop-cop`. The line above about `uvx <tool>` is for *documenting* tools distributed that way, not for running slop-cop. If `slop-cop` isn't on PATH, run the `slop-cop:slop-cop-check` skill, which bootstraps the binary.

Use `--lang=markdown` for `.md`, `.mdx`, and `.qmd` so code blocks, links, headings, and front matter are masked, since `.qmd` is not auto-detected. Cut the genuine tells: throat-clearing, hedge stacks, negation pivots, filler adverbs in procedure steps, unicode arrows (replace with words), and the leading bold from bold-first-bullet walls. The builder voice deliberately uses em-dashes, casual intensifiers, and short fragments — when slop-cop flags one, keep it if it's doing voice work and rewrite it if it's reflex. The tell isn't the device; it's the device used formulaically. A colon before a code block is fine, and table-cell and Quarto-div false positives are acceptable to leave. Run `slop-cop` in CI as a report, not a hard gate. When you edit an existing doc, fix tells only in the lines you're already changing — never reflow pre-existing untouched lines to satisfy the linter, which is scope creep over the author's deliberate voice.

## Rewriting existing docs

A rewrite is a different job from a first draft: the bar is cutting, not adding. Set a rough line-count reduction target before you start and check it after — a front-door README that has drifted into a marketing pitch, a quickstart, a full command reference, and an architecture deep-dive at once wants a real cut (one cc-pool README went 327 to 165). Pick the one job the page should do, relocate the rest to a sibling page, and link.

Gate a large rewrite with three independent checks, not one read-through:

- **Accuracy** — every command, flag, path, and claim verified against the source, especially anything the rewrite introduced or changed.
- **Completeness** — diff against the prior version and confirm no reader-relevant fact was silently dropped. A fact may move to another page or section; it never vanishes.
- **Prose and links** — links and anchors resolve, one h1 with no skipped levels, and the voice and cut-slop rules actually landed.

## Anti-patterns to forbid

Mode-mixing; a branchy or wall-of-text quickstart; pseudocode or paste-breaking fragments; hand-typed or stale output; bare-ellipsis omissions; retyped instead of embedded examples; untested illustrative-only code; real secrets or domains in samples; hidden credentials in a quickstart; a README that duplicates the docs site; a standalone README that triplicates the command surface across a Why list, the quickstart, and the command table, or enumerates every flag in prose instead of deferring to `--help`; a uvx or pipx tool whose install section narrates the runner ("no install needed — uvx fetches it into a throwaway environment") or pads with a `uv add` alternative instead of just showing `uvx <tool>`; a git-log changelog; future tense or passive that hides the actor; editorial we with no shared technical reality; hedged, buildup-first prose that buries the verdict; fillers like "simply" and "just" in procedure steps; "click here" links; missing alt text; skipped heading levels; generic he or she; biased technical terms; self-referential page or section intros such as "This page explains" or "What to learn"; pre-announced structure such as "three properties follow"; pre-emptive nannying such as "be careful", "make sure", or "don't forget" without real stakes; internal, packaging, or docs-generation detail on task or reference pages; gratuitous history or legacy references outside explanation pages and the changelog; a cheat sheet shipped as a screenshot or PDF instead of searchable text; a catalog entry whose code is a non-running fragment or whose input and output are hidden; a footgun buried in prose instead of a callout; an analogy stretched past where it holds; and a page that is half cheat sheet and half reference, labeled as one but built like the other.

## Pre-merge docs checklist

Run this before merging any docs change. The full version is in `references/checklist.md`.

- [ ] Each page is exactly one Diataxis mode and does not mix modes.
- [ ] The required sections for that mode are present.
- [ ] A quickstart or tutorial is single-path, under ten steps, states a destination, and ends in a shown, verifiable outcome.
- [ ] Every code sample is complete, copy-pasteable, and uses safe placeholders, with omissions marked by a real comment.
- [ ] Example code is single-sourced and embedded, not retyped, and passes in CI; shown output is generated by running it.
- [ ] Cheat sheets are searchable text and scannable; catalog entries are copy-paste-complete with shown input and output; an abstract concept carries an analogy and a contrast table, and a footgun sits in a callout, not prose.
- [ ] Voice pass covers the builder voice in narrative prose (first person, confident assertion, varied sentence length), imperative procedure steps, active voice, present tense, one term per concept, and neutral reference pages.
- [ ] Inclusive-language pass covers no generic he or she, people-first phrasing, and non-biased technical terms.
- [ ] Accessibility pass covers one h1, no skipped levels, descriptive link text, alt text, and diagrams with a text equivalent.
- [ ] Links resolve, both internal relative paths and external URLs.
- [ ] The README is still a front door with a pitch, install, one working example, and a path to deeper docs (a docs-site link, or the tool's own `--help` for a standalone README), and no duplicated API content.
- [ ] A standalone README (no docs site) states each command surface once across the Why, quickstart, and command table, carries its reference tables in-file, and defers the exhaustive flag list to `--help` — see `references/standalone-readme.md`.
- [ ] The CHANGELOG is updated in Keep a Changelog format.
- [ ] `slop-cop check <file> --lang=markdown` findings are triaged: genuine tells fixed, deliberate voice moves consciously kept.
- [ ] The docs site builds green.

## Great Docs and Quarto projects

When the project uses Great Docs with Quarto, see `references/great-docs-quarto.md` for the stack specifics. They cover `great-docs.yml` sections, the curated symbol reference from the package `__init__` re-exports, Google-style docstrings on the public API only, `.qmd` front matter, the `gd-embed` marker workflow for single-sourcing example code, and building green with `uv run great-docs build` after `uv sync --group docs`.
