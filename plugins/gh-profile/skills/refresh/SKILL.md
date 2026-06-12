---
name: refresh
description: Headless refresh for a gh-profile-managed profile repo — rewrite the summaries sidecar (.github/profile-summaries.json) from real commit and release data so the activity and shipped sections carry one-line summaries, re-render the marker sections, refresh prose when activity warrants it, and push one commit. Use when running inside the profile repo's scheduled Claude workflow (profile-claude-refresh.yml) or when asked to refresh profile summaries.
allowed-tools: Bash(gh:*, git:*, python3:*, date:*), Read, Write, Edit, Glob, Grep
---

# Profile refresh (headless)

The daily CI pass for a profile repo the **gh-profile** skill composed. The
committed Python updater owns the numbers (every marker interior, refreshed
mechanically every 6 hours); this pass owns the **words**: the one-line
summaries that turn `Pushed to [cc-review]` into
`Pushed to [cc-review] — built the realtime inline-comment web UI`, plus the
prose outside the markers. Non-interactive by design — never ask questions,
never wait for confirmation; if a step can't proceed, print why and stop.

This skill is a **thin wrapper over the repo-summaries plugin's refresh
skill**: the sidecar choreography and its hard rules — style core, flattery
law, reuse-before-rewrite, deterministic bytes, never invent — live in
`/repo-summaries:refresh`; this wrapper supplies gh-profile's config and the
profile-specific steps (prose pass, taste budget, commit). The worked section
markup lives in this plugin
(`${CLAUDE_PLUGIN_ROOT}/skills/gh-profile/reference/blueprint.md`). The
profile repo's `PROFILE_GUIDE.md`, if present, holds only per-user overrides
and **wins over the defaults**.

Work the steps in order.

## 1 — Guard

The cwd must be a profile-repo checkout: `README.md` exists, its line 1 is a
`<!-- gh-profile:meta {...} -->` comment, it contains
`<!-- gh-profile:start:` markers, and `.github/scripts/update_profile.py`
exists. Anything missing → print what and stop; this skill never bootstraps.

## 2 — Refresh the sidecar (the shared pass)

Apply the `/repo-summaries:refresh` choreography — read its SKILL.md and
follow steps 1–5 (the commit stays here, step 4 below). If the repo-summaries
plugin isn't installed, this marketplace ships it alongside gh-profile: read
the sibling skill at
`${CLAUDE_PLUGIN_ROOT}/../repo-summaries/skills/refresh/SKILL.md`.

The config: `.github/summaries.config.json` if the repo has one, else this
plugin's template at
`${CLAUDE_PLUGIN_ROOT}/skills/gh-profile/templates/github/summaries.config.json`
(repos composed before gh-profile 0.3.0 don't carry the file; the template is
the same config). It sets the sidecar path (`.github/profile-summaries.json`),
the two groups — `activity` (keys `<EventType>:<owner/repo>`, `as_of` = push
head sha or event date) and `shipped` (keys `<repo>@<tag>`) — their
raw-material recipes (harvest the dossier via
`python3 .github/scripts/update_profile.py harvest`, then commit subjects and
release content via `gh api`), the render command, and the 10-day staleness
window.

Two profile-specific checks on the shared pass:

- The render command prints `NOMARKER <id>` when a marker pair is broken —
  stop and report; never patch markers from this skill.
- After rendering, `python3 .github/scripts/update_profile.py update --check`
  must exit 0 (idempotence: the sidecar is just another deterministic input).

## 3 — Prose pass (only when activity warrants it)

If the dossier shows material change — new repos since the prose was written,
shifted clusters, a "Now" section that no longer matches the recent activity —
refresh the prose (worked markup per section in
`${CLAUDE_PLUGIN_ROOT}/skills/gh-profile/reference/blueprint.md`): rewrite the
"Now" bullets from recent activity, punch up one-liners for repos that
appeared since the last pass, recategorize "More things I built" if the
clusters shifted. Honor `PROFILE_GUIDE.md` overrides first.

Invariants, non-negotiable:

- Never edit inside `<!-- gh-profile:start:<id> -->` /
  `<!-- gh-profile:end:<id> -->` interiors — the updater owns them.
- The line-1 meta comment survives byte-for-byte; never change thresholds
  unasked.
- Taste budget: ≤ 1 animated element above the fold, ≤ 2 stat widgets, 2-item
  minimum per section, ≤ 1 emoji per heading.
- Every fact traces to the dossier.

If nothing material changed, change nothing — don't churn words daily for
their own sake.

## 4 — Commit & push

One commit, staged precisely:

```bash
git add README.md .github/profile-summaries.json
git commit -m "chore: refresh profile summaries and prose"
git pull --rebase && git push
```

This run can race the 6-hourly mechanical refresh: if the push rejects or the
rebase touches README.md, re-run the render command once and push again. Exit
criteria: pushed, `update --check` exits 0 on the pushed state.
