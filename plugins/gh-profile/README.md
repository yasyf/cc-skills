# gh-profile

**A GitHub profile README freezes the day you write it.** `gh-profile` builds the `<username>/<username>` profile repo from your real repos, releases, and events, then keeps its data sections current on a schedule.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install gh-profile@skills
```

Prerequisites:
- `gh` CLI authenticated with `repo` and `workflow` scopes
- `python3`
- Optional: the `gen-image` plugin with `OPENAI_API_KEY` for an AI banner, an `ANTHROPIC_API_KEY` repo secret for daily Claude refresh, and a classic personal access token as `METRICS_TOKEN` for `max` intensity metrics

## Use it

### Create a profile README from scratch

Your `github.com/<username>` page has no profile repo yet, or you want a page backed by live repo data and scheduled Actions. With `gh-profile` installed, ask:

```text
Make my GitHub profile fancy.
```

The skill reads your login from `gh` and never asks for your username. It harvests a dossier of your repos, stars, pinned items, languages, releases, and recent events through about 20 cached `gh api` calls, then shows a one-screen summary including what the flattery gates hide. By default, star counts show at 30+ stars, the contribution total at 750+ a year, and releases from the last 6 months. Next, it runs one pre-filled interview round covering your tagline, "Now" focus bullets, intensity, and the opt-in daily Claude refresh. It composes the README and workflows, shows the diff, and waits for your confirmation before any push. It creates the public `<username>/<username>` repo only when none exists, pushes, watches each workflow to green, checks that every image URL returns 200, and opens `github.com/<login>`.

### Add live data sections to a hand-written README

You already wrote a profile README by hand and want live `featured`, `shipped`, `activity`, and `languages` blocks without losing your prose. With `gh-profile` installed, ask:

```text
Update my GitHub profile README with live activity and shipped releases, keeping my existing bio.
```

The skill detects the existing hand-written README and offers Annex, Remix, or Sections-only modes. Choosing the default Annex mode preserves every byte of your existing prose and inserts `<!-- gh-profile:start:<id> -->` / `<!-- gh-profile:end:<id> -->` markers around the new managed sections so the committed updater rewrites only those interiors. If you pick Sections-only instead, it renders the sections to a scratch file and pushes nothing. On later runs against a managed profile, the interview collapses to a single question asking whether to refresh data only or revisit voice and intensity.

### Enrich daily activity lines with commit summaries

Raw event lines like `Pushed to [cc-review]` hide what changed in those commits and releases. Inside your profile repo's scheduled `profile-claude-refresh.yml` workflow, the daily job runs:

```text
/gh-profile:refresh
```

Running headlessly inside the scheduled workflow, this skill wraps the `repo-summaries` plugin's `/repo-summaries:refresh` skill to rewrite `.github/profile-summaries.json` from real commit and release data. It re-renders the managed sections so `Pushed to [cc-review]` becomes `Pushed to [cc-review] — built the realtime inline-comment web UI`, refreshes prose only when activity warrants it, and pushes one commit. If the `.github/profile-summaries.json` sidecar ages past 10 days without a refresh, those lines render plain instead of stale.

## What's inside

| Component | Type | What it does |
| --- | --- | --- |
| `gh-profile:gh-profile` | Skill | Creates or non-destructively refreshes the `<username>/<username>` profile README from GitHub API data and configures scheduled refresh workflows. |
| `gh-profile:refresh` | Skill | Runs headlessly inside `profile-claude-refresh.yml` to update `.github/profile-summaries.json`, re-render managed sections, and push one commit. |

Part of [cc-skills](../../README.md).
