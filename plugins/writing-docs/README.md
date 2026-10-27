# writing-docs

**Stop shipping docs nobody ran.** `writing-docs` holds every page to one Diataxis mode and a technical-builder voice, and tests each claim against the real package before it ships.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install writing-docs@skills
```

Prerequisites:
- The `gemini` CLI on `PATH`, which drafts the prose with `gemini -m gemini-4-argon`.
- `slop-cop` binary on `PATH`, or the `slop-cop` plugin installed so `slop-cop:slop-cop-check` can bootstrap the Go binary.

## Use it

### Rebuild a project README around real runs

Your README opens with architecture internals, mixes setup options, and shows hand-typed mock output that drifted from the CLI months ago. With `writing-docs` installed, ask:

```text
Rewrite the README for this repo.
```

Claude loads the conversion skeleton, or the standalone-readme rules when there is no docs site. It drops the contributing section, the development section, and any "How it works" tour. The README gets an opener that names the reader's pain and one live get-started path with a real run shown directly below it. It also gets use cases built from real commands and observed behavior, an agent-paste block, and a funnel ending. Claude hands the facts to `gemini -m gemini-4-argon -p` and lands its reply verbatim. It then runs `slop-cop check README.md --lang=markdown`, triages every finding, and works through the pre-merge checkbox checklist.

### Write a single how-to or reference page that passes honesty gates

You need a new guide or API reference page and cannot risk documenting invented symbols or unverified code blocks. With `writing-docs` installed, ask:

```text
Write a how-to page for configuring webhook retries and a reference page for the retry client.
```

Claude classifies each page into a single Diataxis mode and never mixes modes on one page. The how-to covers the shortest correct path for one task, and the reference page holds neutral tables and signatures. Before shipping the draft, Claude runs four honesty gates. The reflection gate imports and inspects the real package to assert every documented symbol and signature, and stops the draft if any symbol is missing. Claude then extracts and runs every runnable code block, pastes sample CLI output from live runs, and records any demos from the real tool. Finally, it runs `slop-cop check <file> --lang=markdown`, and falls back to `slop-cop:slop-cop-check` to bootstrap the binary when `slop-cop` is not on `PATH`, before triaging every finding.

### Consolidate a sprawling documentation site without breaking URLs

Your documentation has grown into dozens of overlapping pages and readers cannot find the canonical answer. With `writing-docs` installed, ask:

```text
Audit and consolidate the docs directory.
```

Claude runs parallel per-page audits that record both defects and gems. It then builds an explicit merge map assigning every page a `merge`, `stay-standalone`, or `kill` verdict with a reason. Each merged destination is rewritten as one piece and never concatenated from source files. Claude adds `aliases:` frontmatter to cover every killed URL so old links still resolve, and runs a link sweep driven by a retarget map. It commits atomically per concern and treats docs CI on the pushed commit as the ship gate, never a local build.

## What's inside

| Component | Type | What it does |
| --- | --- | --- |
| `writing-docs:writing-docs` | Skill | Writes, revises, audits, and consolidates READMEs and docs pages with Diataxis modes, builder voice, honesty gates, and a triaged `slop-cop` pass. |

Part of [cc-skills](../../README.md).
