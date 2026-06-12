---
name: refresh
description: Headless refresh of a Claude-maintained summaries sidecar in a consumer repo — read the repo's summaries config (.github/summaries.config.json), gather raw material per group via the config's recipes, rewrite the sidecar whole with reuse-before-rewrite, then run the config's render command so the consumer's lines pick up the one-line summaries. Use when running inside a consumer repo's scheduled Claude workflow or when asked to refresh a summaries sidecar; consumer skills (e.g. /gh-profile:refresh) wrap this with repo-specific steps.
allowed-tools: Bash(gh:*, git:*, python3:*, date:*), Read, Write, Edit, Glob, Grep
---

# Summaries refresh (headless)

A consumer repo commits a render script whose output lines carry
` — <summary>` suffixes read from a JSON sidecar via this plugin's
`summaries.py` module; this skill is the scheduled Claude pass that owns the
**words** — it rewrites that sidecar from real data, then re-renders. The
mechanics (staleness gate, sanitizing, key matching) live in the module; the
per-repo specifics (which groups, where the raw material comes from, how to
re-render) live in the repo's config. Non-interactive by design — never ask
questions, never wait for confirmation; if a step can't proceed, print why
and stop.

## What consumers commit (one-time setup)

- `summaries.py` — copy
  `${CLAUDE_PLUGIN_ROOT}/skills/refresh/templates/scripts/summaries.py`
  next to the render script, which imports `load_summaries` / `summary_for`
  (plus `SUMMARY_STALE_DAYS` / `SUMMARY_MAX_LEN`, `summaries_fresh`,
  `clean_summary`, `parse_iso` as needed). The render script only ever
  **reads** the sidecar; this skill is the only writer.
- `.github/summaries.config.json` — the config below.

## The config

```jsonc
{
  "sidecar": ".github/profile-summaries.json",  // sidecar path, repo-root-relative
  "style_file": null,                           // optional path; its rules WIN over the style core below
  "stale_days": 10,                             // freshness window; must match what the render script passes
  "render_command": "python3 .github/scripts/update_profile.py update",  // run after the sidecar is written
  "groups": {                                   // one entry per sidecar group
    "<name>": {
      "description": "what one entry of this group summarizes",
      "key_hint": "how keys are formed, with an example",
      "as_of_hint": "the reuse token recorded per entry, or 'none' for immutable keys",
      "raw_material": "prose instructions — gh api recipes — for gathering what summaries are built from"
    }
  }
}
```

`sidecar`, `render_command`, and `groups` are required; `style_file` defaults
to null and `stale_days` to 10.

Work the steps in order.

## 1 — Resolve the config

Read `.github/summaries.config.json` in the repo being refreshed — or the
config path the invoking skill named. Config missing, or missing one of
`sidecar` / `render_command` / `groups` → print what and stop; this skill
never invents per-repo specifics. If `style_file` is set, read it now: its
rules override the style core in step 4.

## 2 — Read the current sidecar

Read the config's `sidecar` file, if present — entries you can prove
unchanged get reused, not rewritten.

## 3 — Gather raw material

Per group, follow the config's `raw_material` instructions to fetch what the
summaries will be built from (keep it to ~15 `gh api` calls across all
groups). The raw material is the entire universe of facts: nothing a summary
says may come from anywhere else.

## 4 — Write the sidecar

Rewrite the sidecar **whole** — regeneration is pruning; never merge. Schema:

```jsonc
{
  "version": 1,
  "generated_at": "<now, ISO-8601 Z>",          // bump EVERY run, even if no entry changed
  "<group>": {
    "<key>": {"as_of": "<reuse token>", "summary": "..."}
  }
}
```

Rules, non-negotiable (the config's `style_file` may override the style core
only):

- **Keys** come from the current raw material only, formed per the group's
  `key_hint`. Keys absent from the current data don't get entries — that's
  the pruning.
- **Reuse before rewriting:** an existing entry whose key and `as_of` (per
  the group's `as_of_hint`) still match is kept verbatim.
- **Style core:** each summary is a lowercase continuation clause — it
  renders after ` — ` on an already-verbed line — ≤ 80 chars, no trailing
  period, no emoji, no first person. Concrete and artifact-shaped: "built
  the realtime inline-comment web UI", not "made improvements".
- **Flattery law:** every word traces to the gathered raw material (commit
  subjects, PR/issue titles, release content, ...). Never invent, never
  editorialize beyond what the material says. When the raw material is
  uninformative ("wip", "bump deps", "fix typo"), **omit the entry** — a
  plain line beats a fake summary.
- Deterministic bytes: sorted keys, 2-space indent, trailing newline.

## 5 — Re-render

Run the config's `render_command`. A nonzero exit means the consumer's
render is broken — stop and report; never patch the consumer's files from
this skill.

## 6 — Hand off or commit

Invoked by a wrapper skill (e.g. `/gh-profile:refresh`): you're done — the
wrapper owns its repo-specific steps and the commit. Invoked standalone:

```bash
git add <sidecar> <render outputs>
git commit -m "chore: refresh summaries"
git pull --rebase && git push
```

If the push rejects or the rebase touches the rendered files, re-run step 5
once and push again.
