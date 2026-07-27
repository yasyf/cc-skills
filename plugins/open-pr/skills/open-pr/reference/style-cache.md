# The style cache

House style is expensive to learn and slow to change, so it's scouted once per repo and cached. `pr-cache.sh` owns the cache:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-cache.sh" status <owner/repo>   # missing | stale:<reason> | fresh
bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-cache.sh" path <owner/repo>     # prints the cache dir, creating it
bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-cache.sh" clear <owner/repo>    # removes it
```

## Layout

Cards live outside `${CLAUDE_PLUGIN_ROOT}` — a plugin update wipes that — under `${CLAUDE_PLUGIN_DATA:-~/.cache/open-pr}/repos/<host>/<owner>/<repo>/`, keyed by host so an enterprise `owner/repo` never collides with the public one (`--host` overrides the origin-remote detection). The dir holds `style.md`, the card the scout writes atomically, plus `pr/<number>.json` scout working state. The card is the consumer contract; read it, not the workings.

## `style.md`

YAML frontmatter carries the machine-readable header; prose sections carry the observed rules with verbatim examples traceable to `sample.pr_numbers`:

```yaml
---
schema: 1
repo: cli/cli
generated_at: 2026-07-26T18:04:00Z
viewer: yasyf
ownership_at_scout: foreign
sample:
  listed: 50
  after_bot_filter: 31
  after_ai_filter: 24
  used: 12
  maintainer: 9
  outside_contributor: 3
  pr_numbers: [13960, 13944, 13938]
  newest_number: 13960
  oldest_merged_at: 2026-05-02T11:19:00Z
confidence: {title: high, body: high, prose: medium, commits: high, review_response: low, code_style: medium}
title_pattern: '^(feat|fix|chore|docs|refactor)(\([a-z-]+\))?: .+'
title_len_p50: 48
title_len_p90: 68
body_words_p50: 110
body_words_p90: 260
commit_trailers: [Signed-off-by]
squash_merge: true
issue_link_style: "Fixes #N on its own final line"
ai_filtered_out: {count: 7, reasons: {co_author_trailer: 4, generated_with: 2, section_skeleton: 1}}
---
```

The flat fields answer the mechanical questions without prose: `title_pattern` and the p50/p90 lengths shape the title, `body_words_p50/p90` bound the body, `commit_trailers` is the exhaustive trailer list to match, `squash_merge: true` means the PR title becomes the commit subject — put the effort there. The sections — `## Titles`, `## Body structure`, `## Prose register`, `## Commit messages`, `## Responding to review`, `## Code-style tells`, `## Caveats` — carry the judgment the flat fields can't.

## The confidence contract

The `sample:` block shows the filters working — `listed` down through `after_bot_filter` and `after_ai_filter` to `used`. What survives is small, and per-axis `confidence` says what a sample that size supports. Two kinds of axis:

- **Conventions** — `title`, `body`. Categorical and low-variance: three merged PRs that all title as `component: imperative summary` establish the grammar. These hold at any sample size.
- **Tendencies** — `review_response`, `prose`. Continuous and high-variance: at `low` they're a lean, not a rule, and the PR template and guidelines card outweigh them. A convention read off three samples described those three.

When an axis is `low`, write to the template and guidelines first and let the card break ties — the reverse of the high-confidence order. `## Caveats` is where the scout recorded what the sample couldn't show; read it before leaning on any `medium`.

## Staleness

`status` is the arbiter, and each reason names a different decay:

- `stale:schema` — an older card format; the current schema version is what `pr-cache.sh` pins.
- `stale:drift` — the repo merged more than 25 PRs past the card's `newest_number`; the house style may have moved.
- `stale:age` — `generated_at` is more than 90 days old (also the fallback check when `gh` can't reach the repo to measure drift).

The reason is diagnostic; the response is uniform: spawn the scout, which overwrites the card in place. Corrections go through a rescout too — a hand-edited card is one the next staleness check can't vouch for.
