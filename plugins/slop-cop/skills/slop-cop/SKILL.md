---
name: slop-cop
description: Check a file (or piece of text) for LLM-generated prose tells using the slop-cop CLI and report the violations. Use when the user asks to "check/lint/scan this file for slop", "find LLM tells in <file>", "run slop-cop on <path>", or wants a violations report for a doc, README, blog post, PR description, or commit message. Report-only by default; offers to apply fixes if asked.
allowed-tools: Bash(slop-cop:*), Bash(bash:*), Read
---

# Slop Cop

Run `slop-cop check` over a target file (or stdin) and summarize the
LLM-prose-tell violations it finds: overused intensifiers, filler adverbs,
negation pivots, em-dash abuse, throat-clearing, hedge stacks, metaphor
crutches, and so on. This is the common-case report flow — it does **not**
rewrite the file unless the user asks.

## Resolve the binary

`bin/slop-cop` under the plugin root is a symlink that
`scripts/install-binary.sh` maintains silently — pointing at a brew-installed
binary, a durable download under the plugin data dir, or a local dev build.
The plugin's `SessionStart` hook normally runs the installer before you do,
so the symlink is usually already in place. Resolve the symlink first, PATH
second:

```bash
# Provision or refresh the managed symlink. Silent, idempotent, and tracks
# the newest release — once per session is plenty.
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-binary.sh" || true
# 1. The managed symlink (normal path).
if [ -x "${CLAUDE_PLUGIN_ROOT}/bin/slop-cop" ]; then
  SLOP_COP="${CLAUDE_PLUGIN_ROOT}/bin/slop-cop"
# 2. PATH fallback (CI, scripting, or a standalone install).
else
  SLOP_COP=slop-cop
fi
```

`${CLAUDE_PLUGIN_ROOT}` is substituted to a real path in this skill's text.
The installer prefers a brew-installed binary; otherwise it sha256-verifies a
download of the latest [`yasyf/slop-cop`](https://github.com/yasyf/slop-cop)
release into the plugin data dir (a location that survives plugin updates).
No Go toolchain is required.

## Run

Pick the target:

- The path the user named.
- Otherwise the file they have open / selected.
- `-` to read from stdin (pass `--lang=markdown` for prose drafts).

```bash
"$SLOP_COP" check --pretty <path>
```

`slop-cop` auto-detects the input language from the extension and masks
non-prose regions before running detectors — `.md` / `.markdown` / `.mdx`
→ markdown, `.html` / `.htm` → html, `.jsx` / `.tsx` / `.ts` / `.js` →
the matching tree-sitter mode (so only comments, string literals, and JSX
text are scanned). Override with `--lang=<mode>` when needed (e.g.
`--lang=text` to see every regex hit, `--lang=markdown` for stdin prose).

It prints a JSON document:
`{"text_length": N, "violations": [...], "counts_by_rule": {...}, "counts_by_category": {...}, "lang": "...", "llm": {...}}`.

## Report

Parse the JSON and present, concisely:

- The total violation count and the `counts_by_category` breakdown.
- One bullet per violation: the rule id, the matched span (first ~60 chars),
  and the `suggestedChange` / `explanation` when present.
- If there are no violations, say so plainly.

Do not paste the raw JSON unless asked. End with a short pointer: the user
can ask for a revision and you will apply the canonical fixes
(e.g. `utilize` → `use`, drop sentence-opening `importantly` / `ultimately`,
collapse hedge stacks, replace em-dash pivots with proper punctuation, cut
metaphor clichés). Only rewrite the file when the user asks for it.
