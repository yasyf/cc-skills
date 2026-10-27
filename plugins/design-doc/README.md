# design-doc

An architecture design is a stack of decisions built on a stack of assumptions. `design-doc` runs the design as a conversation where you make every decision, then renders an interactive proposal where every claim traces back to an assumption, a decision, and the question round that produced it.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install design-doc@skills
```

Prerequisites:
- `python3`
- `gemini` CLI, where `gemini-4-argon` writes all the doc's prose
- Chrome or Chromium for the PDF and render checks, setting `CHROME=/path/to/chrome` if not found
- Network connection because Mermaid loads from a CDN
- Optional: `cc-present` plugin for interview boards, a `wlm` voice profile, `slop-cop`, and `wrangler` for public hosting

## Use it

When starting a system design from scratch and you want root causes diagnosed before anyone proposes an architecture, ask:

```text
Help me design a job queue for our build workers
```

The skill interviews you about the current system, writes root causes before proposing anything, classifies the design as a change or net-new, and names three to five themes. It sends every design fork to you on a live `cc-present` board or through `AskUserQuestion` when `cc-present` is absent. Each fork shows options with consequences, pros and cons, one recommendation, and an escape hatch that adds the fork to the open list. Your answers go verbatim into `qa-log.json`, and every unmeasured number is marked `(E)` and gated on a named spike. It stops at the design and writes no implementation code.

When a design discussion is scattered across notes and you need a reviewable proposal with traceable decisions, ask:

```text
Turn this thread about tenant isolation into a design doc
```

The skill records assumptions as `A#`, decisions as `DQ#`, open questions as `Q#`, and spikes as `V#` in stable-ID registers. Changed decisions get a new entry while the old one is marked replaced and never edited in place. An adversarial review checks the middle draft using the `codex` plugin when available or a fresh-context subagent, giving every finding a disposition. It then builds a single-file interactive HTML document that opens on a full-screen summary deck and draws the system diagram with Mermaid. The document shows a plain-English twin of every entry first and links decisions and open items to pull requests with live GitHub state. It also replays the interview and prints a PDF from the same page.

## What's inside

| Component | Kind | What it does |
| --- | --- | --- |
| `design-doc:design-doc` | Skill | Runs an assumptions-first design interview and produces an interactive HTML design document and PDF. |
| `design.py` | Driver | Runs `scaffold`, `check`, `summary-text`, `plainify`, `glossary`, `build`, `render-check`, `pdf`, `snapshot`, and `links`. |
| `design-doc` (`design-doc.fork`) | `cc-present` block pack | Renders the interview surface for a single design fork with consequences and an escape hatch that returns a titled open question. |

Part of [cc-skills](../../README.md).
