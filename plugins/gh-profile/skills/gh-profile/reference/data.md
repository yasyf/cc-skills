# The Dossier — Harvest Schema, gh Commands, and Gates

Produced by `update_profile.py harvest` (which `$PROFILE harvest` delegates
to) and consumed by every downstream phase. One rule above all: **if it's not
in the dossier, it doesn't go on the page.**

## CLI surface (the committed updater)

```
update_profile.py harvest [--login X] [--out F]
update_profile.py update  [--readme PATH] [--sections a,b] [--check] [--login X]
```

Login resolution, in order: `--login` → the owner of `$GITHUB_REPOSITORY`
(set in Actions) → `gh api user -q .login`. Stdlib only; every GitHub read
goes through one `_gh()` subprocess boundary, so the whole shaping pipeline
is pure and unit-tested.

## The gh commands (verbatim, with cache flags)

| Call | Cache | Yields |
|---|---|---|
| `gh api users/$LOGIN` | `3600s` | `user` block |
| `gh api "users/$LOGIN/repos?per_page=100&type=owner" --paginate` | `3600s` | raw repos |
| `gh api graphql -f query=... -f login=$LOGIN` | `3600s` | `pinnedItems(first: 6)` + `contributionsCollection.contributionCalendar.totalContributions` |
| `gh api "users/$LOGIN/events?per_page=100"` | `900s` | raw events |
| `gh api repos/$LOGIN/<repo>/releases/latest` × top 15 scored repos | `3600s` | latest release each; 404 (no releases) tolerated |

About 20 calls total, cached — re-running harvest inside an hour is nearly
free and `RATE_REMAINING` barely moves. The shorter events cache (15 min)
keeps the activity digest fresh. Two Events API caveats to repeat to users:
new events take **30 s–6 h** to appear, and the feed only covers **90 days**.

## Schema (shapes mirror the updater's dataclass-free dicts, verbatim)

```jsonc
{
  "generated_at": "2026-06-12T08:00:00Z",
  "user": {                       // gh api users/$LOGIN, these keys only
    "login": "octocat", "name": "Mona Lisa Octocat", "bio": "...",
    "followers": 4242, "company": "@github", "blog": "https://octocat.dev",
    "location": "San Francisco"
  },
  "pinned": [                     // GraphQL pinnedItems, up to 6
    {"name": "Hello-World", "description": "...", "url": "https://github.com/octocat/Hello-World",
     "stars": 2048, "language": "JavaScript"}
  ],
  "repos": [                      // included repos, score-sorted desc, cap 50
    {"name": "Hello-World", "description": "...", "url": "...",
     "stars": 2048, "forks": 1700, "language": "JavaScript",
     "topics": ["demo"], "pushed_at": "2026-06-01T12:00:00Z",
     "archived": false, "score": 2070.83}
  ],
  "languages": [                  // histogram over included repos, count desc
    {"name": "Python", "count": 12}
  ],
  "recent_events": [              // deduped digest, newest first, cap 12
    {"type": "PushEvent", "repo": "octocat/Hello-World", "created_at": "2026-06-10T09:30:00Z"}
  ],
  "releases": [                   // newest 10 across the probed repos
    {"repo": "Hello-World", "tag": "v2.1.0", "name": "Warp-stable",
     "url": "https://github.com/octocat/Hello-World/releases/tag/v2.1.0",
     "published_at": "2026-05-30T00:00:00Z"}
  ],
  "contributions": {"total_last_year": 1234},
  "excluded": [                   // quality-floor drops, with reasons
    {"name": "old-fork", "reason": "fork"}
  ]
}
```

## Shaping rules (pure functions, unit-tested)

- **Exclusion floor:** forks, archived repos, and repos without a description
  are dropped into `excluded` with a reason — visible so you can argue
  exceptions in Phase 1 instead of silently losing repos. The fix for
  "no description" is `gh repo edit -d "..."` and a re-harvest.
- **Score:** `stars + 25 * max(0, 1 - days_since_push/180)` — a recency bonus
  worth up to 25 stars, fading linearly to zero at 180 days. Included repos
  sort by score (ties alphabetical), capped at 50.
- **Events digest:** deduped per `(type, repo)` keeping the newest, 30-day
  window, cap 12, newest first. Event types map to human verbs
  (`PushEvent` → "Pushed to", `ReleaseEvent` → "Cut a release in", ...).
- **Releases:** `releases/latest` for the 15 top-scored repos, keep the 10
  newest by `published_at`.

## Flattery gates — semantics and defaults

Thresholds live in the line-1 meta comment; defaults fill any gap. The
updater re-judges every gate against fresh data on every run — **thresholds
persist, verdicts don't** — so a star badge appears by itself the day a repo
crosses the line, and disappears if the threshold is later raised.

| Threshold | Default | Gates |
|---|---|---|
| `min_stars_badge` | 30 | Star badge on a `featured` card. Below: the card leans on description + language — no numbers anywhere |
| `min_contributions` | 750 | The "N contributions in the last year" line in `activity` |
| `shipped_window_months` | 6 | `shipped` only lists releases this recent; nothing recent → empty interior, and the 2-item rule hides the section |

Follower counts are **never** rendered by default — `user.followers` is in
the dossier for your judgment, not for the page. And gate verdicts are
binary show/hide: there is no "rounded up", no "almost 30 stars", and no
explanatory footnote for what was hidden.

## Markers, meta, and `update` semantics

```
<!-- gh-profile:start:<id> -->   ...interior owned by the updater...
<!-- gh-profile:end:<id> -->
```

ids: `featured`, `shipped`, `activity`, `languages`.

- `update` rewrites **only marker interiors**, via pure string splicing — no
  regex substitution on user content, so interiors containing `$` or regex
  metacharacters splice byte-exactly. Bytes outside markers are untouchable.
- A missing or malformed pair prints `NOMARKER <id>` and touches nothing for
  that id.
- `--sections featured,shipped` restricts the run to a subset; the default is
  all four.
- `--check` writes nothing: it prints the would-be unified diff and exits 1
  if dirty, 0 (`CLEAN`) if not. The refresh workflow's commit-if-changed step
  relies on the same no-op behavior: identical data in, byte-identical README
  out.
- The meta comment must be **line 1**, a single JSON object:

  ```
  <!-- gh-profile:meta {"intensity": "fancy", "last_refresh": "...", "min_contributions": 750, "min_stars_badge": 30, "shipped_window_months": 6, "skill_version": "0.1.0"} -->
  ```

  Only integer-valued threshold keys override the defaults; everything else
  (`intensity`, `skill_version`, ...) rides along untouched. `last_refresh`
  is bumped only when an interior actually changed — which is what makes
  `update` idempotent.
