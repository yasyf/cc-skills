---
name: writing-docs
description: Use when writing or revising project documentation of any kind, including a README, docs site, tutorial, quickstart, how-to guide, reference page, API doc, or changelog. Applies the Diataxis framework (one mode per page), enforceable voice and style rules, runnable code-sample rules, quickstart and README anatomy, accessibility, and a required slop-cop prose-lint pass before you finish. Triggers on "write/rewrite/improve the docs", "write a README", "write a tutorial", "document this", "getting-started page", or any natural-language documentation task.
---

# Writing Docs

This skill is opinionated and checklist-driven. Documentation is not one thing. It is four kinds of page that serve four different reader needs, and the largest source of bad docs is letting those kinds bleed into each other. Pick the mode first, write to its rules, then run the pre-merge checklist at the end. A wrong doc is worse than a missing one, because readers trust and act on it.

Run `slop-cop check <file> --markdown=on` on every page you touch and revise until it is clean. This is not optional.

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

See `references/diataxis.md` for the required sections per page type.

## Voice and style

These are enforceable. slop-cop catches most of them.

- Address the reader as you and your. Never use we or our for the reader. Write procedure steps in the imperative: "Run the command."
- Use active voice with an explicit actor. Use present tense; ban "will" and "would" for general behavior.
- One idea per sentence, around 20 words, under 26. Keep subject, verb, and object near the start.
- Front-load every level. Put the topic sentence first.
- Use exactly one term per concept everywhere, with identical capitalization. Keep a project term map.
- Be conversational without being frivolous. Use contractions. Ban exclamation marks, slang, and the fillers "simply", "just", "easy", and "quickly".
- Use sentence-case headings with no end punctuation, the Oxford comma, and conditions before instructions.
- Use inclusive, bias-free language. Replace any generic he or she with you, a role, or singular they, use people-first phrasing, and pick non-biased technical terms.
- Code font carries meaning. Use backticks for filenames, paths, commands, function and class names, keywords, types, and placeholder variables, but not for product names or browsable URLs.

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

State the run convention once and use it verbatim everywhere. For a tool distributed through uvx or pipx, every command is the full ephemeral form such as `uvx <tool> ...`, never the bare binary, except where a config file must hold it. Leading with the ephemeral invocation is the uv and Ruff house style.

## Quickstart and tutorial design

A quickstart is a Diataxis tutorial with one language, one use case, and zero branches. State the destination and a time budget up front, as in "Write your first hook in under five minutes". End in one tangible, verifiable outcome the reader can see. Aim for first success under five minutes and at most around ten steps. Give complete copy-paste snippets that say exactly where the code goes, surface every needed value inline, and show the expected output so the reader self-verifies. Defer concepts; give the most minimal inline reason and link to depth. Test the quickstart on a clean machine each release.

## README anatomy

Order the README broad to specific, by cognitive funnel. Lead with the title, badges, and a one-line pitch under 120 characters, then what and why, install, the smallest working example with its output, deeper usage, a documentation link, contributing, and the license as SPDX. The README is the front door, not the house. Link to the docs site for full usage and API reference; never duplicate them, so there is one source of truth. The minimum viable docs set is README, LICENSE, CONTRIBUTING, and CHANGELOG. Use relative links within the repo and badges for live status instead of prose claims.

## Maintenance, accessibility, and the changelog

- Keep a CHANGELOG by hand in Keep a Changelog format, latest first, grouped Added, Changed, Deprecated, Removed, Fixed, Security, with an Unreleased section on top. Never dump a git log.
- Deprecate with an in-docs notice, at least one minor release of overlap, and a migration path.
- Make accessibility lint-able. Use exactly one h1, no skipped heading levels, descriptive link text and never "click here", alt text on every image, a text equivalent for every diagram, tables introduced in prose with header cells, no color-only signaling, and acronyms expanded on first use. Avoid directional words like "above".
- Lint prose in CI with slop-cop, and Vale where configured, and check links.

## Run slop-cop (required)

Before you call any doc done, run it through slop-cop and fix what it flags.

```bash
slop-cop check path/to/page.md --markdown=on
```

Use `--markdown=on` for `.md`, `.mdx`, and `.qmd` so code blocks, links, headings, and front matter are masked, since `.qmd` is not auto-detected. Cut the tells it reports. Split em-dash pivots into two sentences or use a comma, drop filler adverbs such as "rather", "simply", and "just", and drop overused intensifiers, negation pivots, and dramatic fragments. Replace unicode arrows with words, and strip the leading bold from bold-first-bullet walls. A colon before a code block is fine, and table-cell and Quarto-div false positives are acceptable to leave. Wire `slop-cop` into CI so a regression fails the build.

## Anti-patterns to forbid

Mode-mixing; a branchy or wall-of-text quickstart; pseudocode or paste-breaking fragments; hand-typed or stale output; bare-ellipsis omissions; retyped instead of embedded examples; untested illustrative-only code; real secrets or domains in samples; hidden credentials in a quickstart; a README that duplicates the docs site; a git-log changelog; future tense or passive that hides the actor; we or our for the reader; fillers like "simply" and "just"; "click here" links; missing alt text; skipped heading levels; generic he or she; and biased technical terms.

## Pre-merge docs checklist

Run this before merging any docs change. The full version is in `references/checklist.md`.

- [ ] Each page is exactly one Diataxis mode and does not mix modes.
- [ ] The required sections for that mode are present.
- [ ] A quickstart or tutorial is single-path, under ten steps, states a destination, and ends in a shown, verifiable outcome.
- [ ] Every code sample is complete, copy-pasteable, and uses safe placeholders, with omissions marked by a real comment.
- [ ] Example code is single-sourced and embedded, not retyped, and passes in CI; shown output is generated by running it.
- [ ] Voice pass covers second person, imperative steps, active voice, present tense, one term per concept, and no fillers.
- [ ] Inclusive-language pass covers no generic he or she, people-first phrasing, and non-biased technical terms.
- [ ] Accessibility pass covers one h1, no skipped levels, descriptive link text, alt text, and diagrams with a text equivalent.
- [ ] Links resolve, both internal relative paths and external URLs.
- [ ] The README is still a front door with a pitch, install, one working example, and a docs link, and no duplicated API content.
- [ ] The CHANGELOG is updated in Keep a Changelog format.
- [ ] `slop-cop check <file> --markdown=on` is clean, leaving only table-cell or Quarto-div false positives.
- [ ] The docs site builds green.

## Great Docs and Quarto projects

When the project uses Great Docs with Quarto, see `references/great-docs-quarto.md` for the stack specifics. They cover `great-docs.yml` sections, the curated symbol reference from the package `__init__` re-exports, Google-style docstrings on the public API only, `.qmd` front matter, the `gd-embed` marker workflow for single-sourcing example code, and building green with `uv run great-docs build` after `uv sync --group docs`.
