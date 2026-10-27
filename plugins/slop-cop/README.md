# slop-cop

**Catch LLM prose tells before they ship.** Scans files or drafted text for filler intensifiers, negation pivots, em-dash abuse, throat-clearing, hedge stacks, and metaphor crutches.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install slop-cop@skills
```

A `SessionStart` hook pre-warms the pinned binary by downloading and caching it at session start, so no Go toolchain or extra prerequisites are required.

## Use it

### Scan a doc or README for LLM tells

Drafted docs and READMEs collect filler adverbs and negation pivots that read like machine output. With `slop-cop` installed, ask:

```text
Scan README.md for slop and show me the violations
```

The skill runs `slop-cop check --pretty README.md` and returns the total violation count, the counts-by-category breakdown, and one bullet per violation with the rule ID, matched span, and suggested change. It stays report-only unless you ask it to rewrite the file with canonical fixes like replacing `utilize` with `use`, dropping sentence-opening `importantly` or `ultimately`, collapsing hedge stacks, replacing em-dash pivots with punctuation, or cutting metaphor cliches.

### Check prose inside source files or pasted drafts

Tells also leak into comments, string literals, pull request descriptions, and commit messages. With `slop-cop` installed, ask:

```text
Find LLM tells in src/Landing.tsx
```

`slop-cop` auto-detects the language from the extension, treating `.md`, `.markdown`, and `.mdx` as `markdown`, `.html` and `.htm` as `html`, and `.jsx`, `.tsx`, `.ts`, and `.js` in tree-sitter mode where only comments, string literals, and text content inside JSX, or JavaScript XML, markup are scanned. You can also pipe a draft over stdin with `-` and `--lang=markdown`, or pass `--lang=text` to see every regex hit.

## What's inside

| Kind | Name | What it does |
| --- | --- | --- |
| Skill | `slop-cop:slop-cop` | Runs `slop-cop check --pretty` on a file or stdin, reports violations by category, and applies canonical fixes on request. |
| Hook | `SessionStart` | Runs `bin/slop-cop --version` at session start to pre-warm the pinned binary. |
| CLI | `bin/slop-cop` | Wrapper that downloads, sha256-verifies, and caches the release pinned in `bin/slop-cop.binrun` from `github.com/yasyf/slop-cop`, falling back to `slop-cop` on `PATH`. |

Part of [cc-skills](../../README.md).
