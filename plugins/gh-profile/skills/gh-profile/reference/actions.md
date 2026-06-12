# Workflows and Secrets

`$PROFILE render --target DIR` installs up to four workflows into
`.github/workflows/`, plus the committed updater at
`.github/scripts/update_profile.py` (and, with `--with claude`,
`PROFILE_GUIDE.md` at the repo root — per-user overrides only; the canonical
rules live in this plugin). Each workflow's `{{CRON_MINUTE}}` is substituted
with a random 0–59 at render time so profile repos don't pile onto GitHub's
:00 scheduler herd — expect different minutes per file, and don't "fix" them.

| Workflow | Installed | Schedule | Secrets |
|---|---|---|---|
| `profile-refresh.yml` | always (fancy+) | every 6 h + dispatch | none (`GITHUB_TOKEN`) |
| `profile-snake.yml` | always (fancy+) | daily + dispatch | none (`GITHUB_TOKEN`) |
| `profile-claude-refresh.yml` | `--with claude` | daily + dispatch | `ANTHROPIC_API_KEY` |
| `profile-metrics.yml` | `--with metrics` (max) | daily + dispatch | `METRICS_TOKEN` (classic PAT) |

Two platform facts govern all of them:

- **Pushing workflow files needs the `workflow` token scope** —
  `gh auth refresh -h github.com -s repo,workflow` if preflight flagged it.
- **GitHub disables scheduled workflows after ~60 days of repo inactivity.**
  Any push re-arms them; so does
  `gh workflow enable profile-refresh.yml -R "$LOGIN/$LOGIN"`.

## profile-refresh.yml — the mechanical refresh

Checks out the repo and runs
`python3 .github/scripts/update_profile.py update` with
`GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}` — the exact script and code path the
skill ran at compose time. It re-harvests the dossier, re-judges the flattery
gates against the thresholds in the line-1 meta comment, rewrites only the
marker interiors, and commits as `github-actions[bot]` with
`chore: refresh profile README sections` — but only if something changed.
`GITHUB_TOKEN` commits never retrigger workflows, so this cannot loop.

Division of labor: this workflow owns the **numbers** (marker interiors); the
daily Claude pass below owns the **words** (the summaries sidecar, prose,
structure). The boundary is the sidecar: Claude writes it, this workflow's
updater reads it — neither edits the other's territory.

## profile-snake.yml — the contribution snake

Two steps: `Platane/snk/svg-only@v3` renders the user's contribution graph as
`dist/github-snake.svg` plus a dark variant whose `?color_dots=` are GitHub's
own dark-theme level colors (snk's `github-dark` palette draws level-1 days
nearly black, which reads as an empty graph on busy profiles), then
`crazy-max/ghaction-github-pages@v4` publishes `dist/` to the **`output`
branch** (`permissions: contents: write`). The README never embeds the
workflow's output directly — it hotlinks the branch:

```
https://raw.githubusercontent.com/$LOGIN/$LOGIN/output/github-snake.svg
https://raw.githubusercontent.com/$LOGIN/$LOGIN/output/github-snake-dark.svg
```

Until the first run, the `output` branch doesn't exist and both URLs 404 —
which is why Phase 5 seeds it before the image-URL check. Each run
force-rebuilds the branch; history there is disposable.

## profile-claude-refresh.yml — the daily Claude pass (opt-in)

`anthropics/claude-code-action@v1`, daily. The workflow is a thin shim: its
`plugin_marketplaces`/`plugins` inputs install `gh-profile@skills` fresh from
the cc-skills marketplace on every run, and the prompt is the single line
`/gh-profile:refresh` — the canonical instructions live in that skill, so
updating the skill updates every profile repo without touching their YAML.
The pass rewrites the summaries sidecar from real commit/release data (the
activity and shipped lines pick the summaries up on the next render),
refreshes prose when activity warrants it, never edits inside marker
interiors, and commits directly to the default branch.

Mechanics worth knowing:

- The step-level `GH_TOKEN` env is what lets the updater's `gh api`
  subprocesses authenticate inside Bash steps.
- `@skills` is the marketplace name from cc-skills'
  `.claude-plugin/marketplace.json` — not `@cc-skills`.
- The `plugin_marketplaces` URL must end in `.git` — the action validates
  the suffix and fails the run otherwise.
- The run can race the 6-hourly mechanical refresh; the refresh skill
  rebases and re-renders once on a rejected push.
- Cost knob: the cron line. Daily keeps push summaries current (the digest
  churns daily on active accounts); drop to weekly and most activity lines
  spend the week plain.

`claude_args: --max-turns 50 --allowedTools "Read,Edit,Write,Glob,Grep,Bash(git:*),Bash(gh:*),Bash(python3:*),Bash(date:*)"` —
the pass harvests, fetches commit subjects, writes the sidecar, re-renders,
and commits; `Bash(python3:*)` is what lets it run the committed updater.

### Secret walkthrough: ANTHROPIC_API_KEY

```bash
gh secret set ANTHROPIC_API_KEY -R "$LOGIN/$LOGIN"
# paste the key at the prompt — it never lands in shell history
```

Non-interactive alternative: `gh secret set ANTHROPIC_API_KEY -R "$LOGIN/$LOGIN" --body "$KEY"`.
For CREATE mode, the repo must exist first — set the secret after Phase 5's
`gh repo create`.

Claude subscription instead of an API key? The action also accepts an OAuth
token: store it as `CLAUDE_CODE_OAUTH_TOKEN`
(`gh secret set CLAUDE_CODE_OAUTH_TOKEN -R "$LOGIN/$LOGIN"`) and swap the
workflow input from `anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}` to
`claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}`.

## profile-metrics.yml — lowlighter/metrics (max only)

Renders a `lowlighter/metrics@latest` infographic
(`base: header,repositories` + `plugin_languages`) and commits it to the
repo. It needs a **classic** PAT — the default `GITHUB_TOKEN` and
fine-grained tokens don't satisfy every metrics plugin.

### Secret walkthrough: METRICS_TOKEN

1. Create the token at <https://github.com/settings/tokens> → "Generate new
   token (classic)". For public-data metrics, **no scopes** are needed; check
   `public_repo` only if a plugin you add later asks for it.
2. Store it:

   ```bash
   gh secret set METRICS_TOKEN -R "$LOGIN/$LOGIN"
   ```

Remember the stat-widget gate: install this workflow only at `max` intensity
**and** only when the numbers flatter.

## Seeding — first run, watched to green

Cron hasn't fired on a fresh push, so every installed workflow gets kicked
once and watched:

```bash
for wf in profile-snake.yml profile-refresh.yml; do
  gh workflow run "$wf" -R "$LOGIN/$LOGIN"
done
sleep 5   # dispatched runs take a few seconds to appear in the list
for wf in profile-snake.yml profile-refresh.yml; do
  run_id=$(gh run list -R "$LOGIN/$LOGIN" --workflow "$wf" -L 1 --json databaseId -q '.[0].databaseId')
  gh run watch "$run_id" -R "$LOGIN/$LOGIN" --exit-status
done
```

Extend the list with `profile-claude-refresh.yml` / `profile-metrics.yml`
when installed — but only after their secrets are set, or the seed run fails
on auth and you'll be reading red logs you caused. Confirm the snake landed:

```bash
gh api "repos/$LOGIN/$LOGIN/contents/github-snake.svg?ref=output" -q .name
```

A failed run's logs: `gh run view "$run_id" -R "$LOGIN/$LOGIN" --log-failed`.
