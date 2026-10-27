# repo-summaries

**Bare activity lines say where you pushed, never what you built.** `repo-summaries` maintains a Claude-written JSON sidecar that turns real commit and release data into one-line suffixes like `Pushed to [cc-review] — built the realtime inline-comment web UI`.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install repo-summaries@skills
```

Prerequisites:
- `gh` CLI (authenticated)
- `python3`

Before the first refresh, the consumer repo commits two files. Copy `summaries.py` from the plugin's `skills/refresh/templates/scripts/` next to your render script. Commit `.github/summaries.config.json` naming the `sidecar` path, the `render_command`, and the `groups` with their raw-material recipes. Optional keys are `style_file` and `stale_days`, which defaults to 10. The render script only reads the sidecar, and the skill is the only writer.

## Use it

### Refresh the summaries sidecar from live GitHub data

Your scheduled workflow or local checkout needs fresh summaries for recent commits and releases. With `repo-summaries` installed, ask:

```text
Refresh the summaries sidecar in this repo
```

The skill runs headlessly and never asks questions. If `.github/summaries.config.json` or a required key is missing, it prints what is missing and stops. It reads the current sidecar and gathers raw material per group using the config's `gh api` recipes, making about 15 calls across all groups.

It rewrites the sidecar with sorted keys, 2-space indent, and a trailing newline. Absent keys get dropped, and entries whose key and `as_of` match stay verbatim. Entries whose raw material is uninformative, like `"wip"`, `"bump deps"`, or `"fix typo"`, are omitted. Each new summary is a concrete lowercase continuation clause up to 80 characters with no trailing period, emoji, or first person.

It then runs `render_command`, stopping on a nonzero exit without patching consumer files. When run standalone, it commits `chore: refresh summaries` and pushes. A consumer repo's scheduled Claude workflow runs `/repo-summaries:refresh` the same way, while gh-profile's `/gh-profile:refresh` wraps it and owns the commit.

## What's inside

| Component | Type | What it does |
| --- | --- | --- |
| `repo-summaries:refresh` | Skill | Gathers GitHub raw material from `.github/summaries.config.json`, rewrites the JSON summaries sidecar, runs `render_command`, and commits the refresh. |
| `summaries.py` | Template module | Stdlib-only Python module a consumer copies next to its render script; exports `load_summaries` and `summary_for` and handles staleness, sanitizing, and key matching. |

Part of [cc-skills](../../README.md).
