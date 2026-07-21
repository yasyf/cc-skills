---
name: writing-docs
description: Use when writing or revising project documentation of any kind — a README, docs-site landing page, tutorial, quickstart, how-to guide, reference page, API doc, cheat sheet, examples catalog, limitations or FAQ page, or changelog — and when consolidating, auditing, or rebuilding a whole docs set. Applies the Diataxis framework (one mode per page), a technical-builder voice, runnable code-sample rules, the conversion skeleton for READMEs and landing pages (a pain-naming opener, one live get-started path with a real-run demo, use cases that show observed behavior, an agent block), standalone-README design, consolidation by rewriting (parallel per-page audits with defects and gems, an explicit merge map with per-page verdicts, alias frontmatter, a link retarget sweep), honesty gates that test docs claims against the real package (reflection gate, extract-and-run, regenerated CLI output, parity-gated demos), findability rules (task-phrased headings, a narrative home for every task), delegated-prose work orders (verbatim landing, surgical fixes tied to audit items, a check-back contract), ship discipline (docs CI on the push as the gate), and a required, triaged slop-cop prose-lint pass. Triggers on "write/rewrite/improve the docs", "write a README", "consolidate/merge the docs", "audit the docs", "write a tutorial", "document this", "getting-started page", "write a cheat sheet", "build an examples catalog", or any natural-language documentation task.
---

# Writing Docs

This skill is opinionated and checklist-driven. Documentation is four kinds of page serving four reader needs, and the largest source of bad docs is letting the kinds bleed together. The second largest is publishing claims nobody executed: docs claims are tested, not proofread — every documented symbol, snippet, and sample output is verified against the real package before it ships (see Honesty gates below). A wrong doc is worse than a missing one, because readers trust and act on it.

Run `slop-cop check <file> --lang=markdown` on every page you touch and triage every finding: fix the genuine tells, keep deliberate voice moves. This is not optional.

Writing always runs on fable: never delegate prose to a down-routed subagent — inherit the session model or pass `model: fable`. Delegation hands over the model, never the rules: a subagent or workflow prompt that writes docs directs the agent to read this skill and its references, and never restates them. A paraphrase drifts — one orchestrator compressed this skill to "technical-builder voice, no hype adjectives", then instructed a stacked install section and a "How it works" internals tour, and the writing agent obeyed the prompt it was given. The delegating prompt does not outrank the skill: it cannot add a section the skeleton drops or waive the single-install rule, and a delegated writer that receives such an instruction surfaces the conflict instead of complying. For how to delegate docs work at all, see `references/work-orders.md`.

## Pick the job

Three jobs enter this skill; each has its own path through the references.

- **Write or revise one page.** Classify its Diataxis mode below, write to the mode's rules in the builder voice, run the honesty gates on what you claimed, then the checklist.
- **Write or rebuild a README or landing page.** `references/readme.md` owns the skeleton and the conversion craft; `references/standalone-readme.md` owns the no-docs-site branch.
- **Consolidate or rebuild a docs set.** `references/consolidation.md` owns the process (audit, merge map, rewrite, aliases, link sweep); `references/work-orders.md` owns how slices delegate; `references/worked-example.md` walks a real rebuild — a 22-page guide down to 10, every killed URL still resolving — end to end.

## The process, five gates in order

1. **Understand the reader.** Name the persona, their goal, and what they already know. Do not draft until you can.
2. **Plan.** Pick the Diataxis mode. Outline the headings before the prose.
3. **Draft.** Front-load with the inverted pyramid: lead every page, section, and paragraph with the most important point. Include at least one runnable example.
4. **Edit in focused passes.** Completeness, then accuracy, then structure, then clarity, then brevity. Split the technical-correctness edit from the language edit; the correctness edit runs the honesty gates.
5. **Maintain.** Stale docs are a bug. Keep them current with the code.

Hold every draft to the Write the Docs guardrails: skimmable, exemplary (always an example), consistent, current, nearby, unique, cumulative, complete. Prefer ARID over strict DRY — a task page may restate a key fact, but never duplicate whole sections; link instead.

## Diataxis, one mode per page, never mixed

Classify the page before writing a line. The mode dictates the allowed content.

| Mode | Serves | Contains | Never contains |
|------|--------|----------|----------------|
| **Tutorial** | Learning by doing | One guaranteed-to-work path from zero to a first result | Alternatives, options, "you could also", deep why |
| **How-to** | Doing one real task | The shortest correct path for a competent reader | Concept teaching, exhaustive option lists |
| **Reference** | Looking a fact up | Accurate, complete, neutral tables and signatures | Narrative, opinion, step-by-step |
| **Explanation** | Understanding | Concepts, tradeoffs, history, the why | Step-by-step instructions, first statement of a fact |

Decide by the reader's goal. If a tutorial makes you want to explain a concept, link out to an explanation page instead of inlining it — that is the top tutorial smell. Map repo directories to modes and assign each existing page one mode; never build empty four-folder scaffolding. `references/diataxis.md` carries the required sections per page type plus the findability rules: headings phrased as the reader's task, and a narrative home for every "where do I…" question so the how-to outranks the generated reference in site search. A cheat sheet and an examples catalog entry are formats of reference and how-to, not new modes — `references/reference-genres.md`.

## Voice and style

Write as a technical builder: someone who has already built the thing, explains it clearly, and has opinions about what's good and what's a mess. The voice governs narrative prose; procedure steps stay imperative; reference pages stay neutral. The load-bearing rules: state the point, then elaborate; first person freely, "we" only for shared technical reality; confident assertion over hedging; active voice, present tense, no future "will" for general behavior; one term per concept; open on substance (no "This page explains", no pre-announced structure, no pre-emptive nannying — but a genuine footgun earns a flat warning callout); keep internals, packaging, and history out of task pages, reference pages, and the README. The full rule set, the opener register, and the slop-cop triage policy live in `references/voice-and-style.md`.

## Code samples

- Complete and copy-pasteable: imports, setup, no fragment that errors. One intro sentence before each sample.
- Mark omissions with a real language comment, never a bare ellipsis.
- Show real, verified output; normalize variable values (ports, session IDs, timestamps) to visible placeholders. Never fake output — regenerating it is Gate 3 in `references/honesty-gates.md`.
- Safe fictional placeholders only: example.com, RFC-reserved IPs, USER_ID. No real PII, secrets, or credentials.
- No pseudocode in user-facing docs. Real code, in the target language, contrasting success against error when behavior differs.

## Runnable and tested docs

Keep example code in real source files under test and embed it into the page instead of retyping, so the rendered page and the tested code cannot diverge; run the examples in CI so a stale example fails the build. Demo media obeys the same law: it shows a real run of the exact command it sits under, and its generator is committed.

State the run convention once and use it verbatim everywhere: for a uvx/pipx tool, every command is the full `uvx <tool> ...` form. The ephemeral invocation *is* the install section — no runner narration, no stacked alternates, no "to add it to a project" line unless the tool is genuinely a library. Every install names ONE canonical, live-today method: no release-timeline narration ("goes live with", "until then"), and every later command runs the exact artifact the install produced — same name, same form, prerequisites named or absent. A genuinely distinct path (a plugin that auto-installs, a real `[extra]`) earns one line; a redundant alternate earns zero.

## Honesty gates

Before any docs change ships, four mechanical gates run against what the pages claim (`references/honesty-gates.md`):

1. **Reflection gate** — every documented symbol and signature asserted against the real package via import + inspect. ANY missing symbol → STOP; the draft is wrong, not the code.
2. **Extract-and-run** — every runnable block extracted and executed through the real engine; syntax-check what can't run standalone.
3. **Regenerated output** — sample CLI output pasted from a live run, trimmed with a real comment, never hand-typed.
4. **Parity-gated demos** — interactive and animated demos recorded from the real tool and replayed deterministically; anything canned is labeled as recorded, never faked live.

## READMEs and landing pages

The README is a front door, not the house: it converts a visitor in one screenful, demonstrates the result before explaining anything, and funnels every deeper question out. It runs the conversion skeleton — an opener that names the pain in the reader's words, one get-started path with the result demonstrated directly below it, use cases showing real code *and* the observed behavior, an agent-paste block, and a funnel ending. The docs landing page tells the same story with the same use cases, synced by hand; the README renders raw on GitHub, so no shortcodes or embed markers. The skeleton, per-section rules, and a worked example live in `references/readme.md`; the inline tail for repos with no docs site in `references/standalone-readme.md`.

## Quickstart and tutorial design

A quickstart is a Diataxis tutorial with one language, one use case, and zero branches: destination and time budget up front, first success under five minutes, at most ~10 steps, complete copy-paste snippets that say where the code goes, expected output shown so the reader self-verifies. Drive it with the repo's committed runnable example, never an in-joke about the project's own development. Multi-page tutorials title each page as a promise and keep one idea per page. Walkthrough checkpoints and the Quarto mechanics live in `references/great-docs-quarto.md`.

## Consolidating a docs set

A rewrite of one page is about cutting: set a line-count reduction target, pick the page's one job, relocate the rest, and gate the result on accuracy, completeness (diff against the prior version — a fact may move, it never vanishes), and prose. A rebuild of many pages is a process with its own reference: parallel per-page audits recording defects and gems, an explicit merge map with a verdict and a WHY for every page (merge, stay-standalone, or kill), each merged page rewritten as one coherent piece — never concatenated — with a gems-survival checklist, `aliases:` frontmatter covering every killed URL, and a link sweep driven by an explicit retarget map. `references/consolidation.md` owns the process; `references/worked-example.md` shows it applied.

## Ship discipline

Docs CI on the pushed commit is the gate — never a local build run as the gate. Commit atomically per concern (one merge, one page family, one sweep per commit), and hold the killed-page reference count at zero before shipping: the final `git grep` for dead page names returns nothing. Changelogs stay hand-written in Keep a Changelog format; accessibility (one h1, no skipped levels, alt text, descriptive links) is lint-able and on the checklist.

## Run slop-cop (required)

Before you call any doc done, run it through slop-cop and triage every finding.

```bash
slop-cop check path/to/page.md --lang=markdown
```

slop-cop is a Go binary (the `slop-cop` plugin, GitHub Releases, or `go install`), not a PyPI package — never `uvx slop-cop`. If it isn't on PATH, run the `slop-cop:slop-cop-check` skill, which bootstraps the binary. Use `--lang=markdown` for `.md`, `.mdx`, and `.qmd` so code blocks, links, headings, and front matter are masked (`.qmd` is not auto-detected). A finding is a prompt for judgment, not an order: the full keep/fix triage policy lives in `references/voice-and-style.md`. Run slop-cop in CI as a report, not a hard gate, and never reflow pre-existing untouched lines to satisfy it.

## The reference map

| Reference | Open it when |
|---|---|
| `references/diataxis.md` | Classifying a page, choosing its required sections, or making a capability findable |
| `references/voice-and-style.md` | Writing any narrative prose; triaging slop-cop findings |
| `references/readme.md` | Writing a README or docs landing page — the skeleton and the conversion craft |
| `references/standalone-readme.md` | The README is the only documentation (no docs site) |
| `references/reference-genres.md` | Writing a cheat sheet or an examples catalog |
| `references/honesty-gates.md` | Verifying docs claims before ship — symbols, snippets, output, demos |
| `references/consolidation.md` | Merging, killing, or rebuilding many pages |
| `references/work-orders.md` | Delegating a docs slice to another agent |
| `references/worked-example.md` | Seeing the whole process applied to a real multi-slice rebuild |
| `references/great-docs-quarto.md` | The project uses Great Docs / Quarto — embeds, aliases, widgets, build |
| `references/checklist.md` | The pre-merge pass: every gate and anti-pattern in checkbox form |
