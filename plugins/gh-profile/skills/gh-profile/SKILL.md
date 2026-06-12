---
name: gh-profile
description: Create or non-destructively refresh a fancy GitHub profile README — the special <username>/<username> public repo that renders at github.com/<username>. Driven by real GitHub data (repos, stars, pinned items, languages, releases, recent activity), never invented; marker-delimited sections stay fresh via a committed cron updater, flattery gates hide numbers that don't impress, and the default "fancy" intensity adds an AI banner (dark/light), snake animation, and cron Actions. Use when asked to "make my GitHub profile fancy", create/update/refresh a profile README, set up the github.com/<me> page, or work on the <username>/<username> repo.
allowed-tools: Bash(gh:*, git:*, python3:*, uv:*, mktemp:*, op:*, ls:*, cat:*, curl:*, open:*), Read, Write, Edit, Glob, Grep
---

# Fancy GitHub Profile README

Build the page that renders at `github.com/<login>` from the user's **real**
GitHub data — repos, stars, pinned items, languages, releases, recent events —
styled creative-and-fancy by default, with cron GitHub Actions that keep the
dynamic sections fresh without Claude. The whole skill is driven by a single
command:

```bash
PROFILE="python3 ${CLAUDE_PLUGIN_ROOT}/skills/gh-profile/scripts/profile.py"
$PROFILE preflight | harvest [--login X] [--out F] | render --target DIR [--with metrics,claude] [--force]
```

The keystone: the updater you run at compose time
(`templates/github/scripts/update_profile.py`) is the **same file** `render`
commits into the profile repo as `.github/scripts/update_profile.py`, where a
cron workflow runs it every 6 hours — first render == every cron render, by
construction.

Work the phases below in order. Each ends with an **Exit criteria** line —
don't advance until it holds.

## Terminology

- **Profile repo** — the public repo named exactly the user's login
  (`<login>/<login>`); its README renders on the profile page. A private one
  renders nothing.
- **Dossier** — the normalized JSON harvest of the user's GitHub data
  (`reference/data.md` has the schema). The single source of truth: every
  name, number, and date on the page must trace to it.
- **Managed section** — a block delimited by `<!-- gh-profile:start:<id> -->`
  / `<!-- gh-profile:end:<id> -->` with ids `featured`, `shipped`, `activity`,
  `languages`. The committed updater owns and rewrites the interiors every
  cron run; everything outside is **sacred content** it never touches, byte
  for byte.
- **Intensity** — how fancy: `polished`, `fancy` (default), `max`. See
  Intensity levels below.
- **Flattery gates** — pure threshold checks: a stat renders only when it
  impresses. Defaults: star badges at ≥ 30 stars, contribution total at
  ≥ 750/yr, releases within 6 months. Hidden numbers are never explained.
- **Meta comment** — README line 1: `<!-- gh-profile:meta {json} -->`,
  recording intensity, skill version, last refresh, and the gate thresholds
  (`min_stars_badge`, `min_contributions`, `shipped_window_months`).
  Thresholds persist; verdicts are re-judged against fresh data every run —
  a star badge appears by itself the day a repo crosses 30.

## Phase 0 — Preflight & mode

```bash
$PROFILE preflight
```

Prints KEY=VALUE lines: `GH_VERSION`, `AUTH`, `LOGIN`, `SCOPES`,
`SCOPE_WORKFLOW=ok|MISSING|UNKNOWN`, `PROFILE_REPO=exists|absent`,
`VISIBILITY`, `DEFAULT_BRANCH`, `HAS_MARKERS`, `RATE_REMAINING` — and exits 1
with a `MISSING:` line on stderr per problem, each carrying its fix. Resolve
every `MISSING` first: pushing `.github/workflows/*.yml` fails without the
`workflow` token scope. `SCOPE_WORKFLOW=UNKNOWN` means a fine-grained token
whose scopes gh can't report — proceed, but the push may fail (Common
issues).

`LOGIN` comes from preflight — **never ask the user for their username**.

Decide the mode from the output:

| Mode | Condition | Meaning |
|---|---|---|
| **CREATE** | `PROFILE_REPO=absent` | No profile repo yet; build from scratch |
| **UPDATE-managed** | exists + `HAS_MARKERS=true` | This skill ran before; refresh it |
| **UPDATE-foreign** | exists + `HAS_MARKERS=false` | Hand-written README; migrate carefully |

Set up the workspace — always a temp dir, always `git -C` (never `cd`):

```bash
WORK=$(mktemp -d)
gh repo clone "$LOGIN/$LOGIN" "$WORK/profile"     # UPDATE modes
git init -b main "$WORK/profile"                  # CREATE
```

CREATE defers `gh repo create` to Phase 5 push time — nothing public appears
until the content is ready.

**Exit criteria:** preflight clean (or every `MISSING` resolved), `LOGIN`
known, mode decided, working clone or fresh repo under `$WORK/profile`.

## Phase 1 — Harvest

```bash
$PROFILE harvest --out "$WORK/dossier.json"
```

About 20 `gh api` calls, all cached (`--cache 3600s`, events `900s`) — cheap
to re-run. Read the dossier, then show the user a **one-screen "here's what
your data says about you" summary**: top-starred repos, inferred project
categories (cluster by topics and language), top languages, recently shipped
releases, the shape of the last 90 days — and **what the flattery gates will
hide and why** ("hiding star counts: your top repo has 12; the page reads
better without them"), so nothing in Phase 5's diff is a surprise.

Check `excluded` too: it lists quality-floor drops with reasons (`fork`,
`archived`, `no description`). A great repo dropped for a missing description
deserves a fix at the source (`gh repo edit -d "..."`, then re-harvest), not a
silent loss.

**Exit criteria:** dossier on disk; summary shown, including what the gates
hide.

## Phase 2 — Interview (the only decision phase)

One `AskUserQuestion` round, every question pre-filled from the dossier as
confirm-or-correct:

- **Tagline** — draft 2–3 options from their bio and top repos.
- **Now / current-focus bullets** — drafted from the last 90 days of events.
- **Badge links** — multiSelect, prefilled from `user.blog`, `user.company`,
  and obvious socials; max 5.
- **Fun facts** — optional; skip the section entirely if they pass.
- **Philosophy / footer line** — one sentence they'd put on a t-shirt.
- **Intensity** — `polished` / **`fancy` (default)** / `max`.
- **Claude-refresh opt-in** — default **off** (needs an `ANTHROPIC_API_KEY`
  repo secret — real friction). Weekly taste pass via `claude-code-action@v1`;
  `reference/actions.md`.

**UPDATE-managed re-run:** collapse the round to one question — "refresh data
only, or revisit voice/intensity?". Data-only means no interview and no prose
edits.

**UPDATE-foreign adds a migration choice:**

- **Annex (default)** — keep every byte of their prose; insert marker-wrapped
  data sections around it.
- **Remix** — full blueprint restructure; their old prose pre-fills the
  interview defaults; full diff shown in Phase 5; the old version stays in git
  history.
- **Sections-only** — render the managed sections into a scratch file for them
  to splice by hand; push nothing.

**Exit criteria:** every answer recorded; intensity fixed; for UPDATE-foreign,
the migration choice fixed.

## Phase 3 — Compose

Write `$WORK/profile/README.md` section by section per the **Content
blueprint** below (worked markup per section in `reference/blueprint.md`).
Rules:

- **Every fact traces to the dossier.** If a number, name, or date isn't in
  `dossier.json`, it doesn't go on the page.
- **Rewrite repo one-liners in the user's voice** — punchy, specific, theirs.
  Never copy `description` strings verbatim into prose.
- **Markers from day one.** Lay down all four marker pairs (empty interiors)
  in their blueprint positions, even in Annex mode.
- **Meta comment on line 1.** Choose gate thresholds now (defaults are right
  for almost everyone) and record them with intensity and skill version:
  `<!-- gh-profile:meta {"intensity": "fancy", "skill_version": "0.1.0", "min_stars_badge": 30, "min_contributions": 750, "shipped_window_months": 6} -->`
- **Run the updater once** so the dynamic sections render — gates included —
  through the same code path as cron:

```bash
UPDATER="python3 ${CLAUDE_PLUGIN_ROOT}/skills/gh-profile/templates/github/scripts/update_profile.py"
$UPDATER update --readme "$WORK/profile/README.md" --login "$LOGIN"
$UPDATER update --readme "$WORK/profile/README.md" --login "$LOGIN" --check   # idempotence: must exit 0
```

`WROTE` means sections populated; `NOMARKER <id>` means that pair is missing
or typo'd — fix and re-run (nothing was touched for that id).

**Prose gates:** apply the **writing-docs** skill's voice to everything a
human reads, then `slop-cop check README.md` and triage — widget markup is
exempt, prose is not.

**Exit criteria:** README composed with all four marker pairs; updater run
once (`WROTE`, no `NOMARKER`) and `--check` exits 0; slop-cop triaged.

## Phase 4 — Assets & Actions (gated by intensity)

**Banner** *(fancy+, default-on)* — invoke the **gen-image skill** (a sibling
plugin in this marketplace; if it's not installed, install
`gen-image@skills` from marketplace `yasyf/cc-skills` or apply the no-banner
escape hatch):

```
banner --name $LOGIN --tagline "$TAGLINE" --variant both --out-dir $WORK/profile/assets/
```

It writes `assets/banner-dark.webp` + `assets/banner-light.webp`, each under
1 MiB. gen-image owns the key chain: `OPENAI_API_KEY` env → 1Password
`op read "op://OpenClaw/OpenAI API Key/notesPlain"` → codex `$imagegen`. If
the whole chain comes up empty, fall back to the typing-SVG-only hero —
**remove the `<picture>` block entirely** so nothing dangles. View both
banners with Read before accepting them.

**Workflows** *(fancy+)*:

```bash
$PROFILE render --target "$WORK/profile"
```

Copies the committed updater plus `profile-snake.yml` and
`profile-refresh.yml` into `.github/`, substituting a random `{{CRON_MINUTE}}`
per file (no thundering herd) and failing on any leftover `{{...}}` token.
Prints `WROTE`/`SKIP` per file; `CONFLICT` writes nothing — resolve per file
or re-run with `--force`. Add-ons:

- `--with claude` *(if opted in)* — adds `profile-claude-refresh.yml` plus
  `PROFILE_GUIDE.md` at the repo root (the Action reads it there). Then set
  the secret: `gh secret set ANTHROPIC_API_KEY -R "$LOGIN/$LOGIN"` (CREATE:
  defer until the repo exists in Phase 5).
- `--with metrics` *(max only)* — adds `profile-metrics.yml`; needs a classic
  PAT as `METRICS_TOKEN` (`reference/actions.md` walks through both secrets).

**Writing section** — only with a real blog feed; wire `blog-post-workflow`
per `reference/blueprint.md` §8. No feed, no section.

**Stat-widget gate:** trophies and metrics render only when the numbers
flatter — a C-rank trophy case hurts more than it helps; skip and say so.
Hard rule: **never embed the public Vercel instances of github-readme-stats,
github-profile-trophy, or github-readme-activity-graph** (rate-limited, with
outages as of Jan 2026). Actions-generated or static (shields.io,
skillicons.dev) only; the `featured` section already covers the stats-card
use case. Full green/red-light table: `reference/widgets.md`.

**Exit criteria:** per the chosen intensity — banners exist and reviewed (or
escape hatch applied), `render` exited 0 with no `CONFLICT`, secrets set for
every opted-in workflow (or explicitly deferred to Phase 5 for CREATE).

## Phase 5 — Push & verify

1. **Show the diff, always.** `git -C "$WORK/profile" diff` (CREATE: the full
   README and file list). State which managed sections changed. On a
   data-only UPDATE run, state — and verify in the diff — that **zero bytes
   outside marker interiors changed**. The user confirms before push,
   **always**.
2. **CREATE only:** now make the repo public and wire the remote:

   ```bash
   gh repo create "$LOGIN" --public --description "GitHub profile"
   git -C "$WORK/profile" remote add origin "https://github.com/$LOGIN/$LOGIN.git"
   ```

   Then set any deferred secrets from Phase 4.
3. **Commit and push immediately** (commit+push+verify is one atomic step)
   with `git -C "$WORK/profile"`:
   - CREATE: `feat: bootstrap GitHub profile README`, then `push -u origin main`
   - UPDATE: `chore: refresh profile README sections`, then `push`
4. **Seed the workflows** *(fancy+)* — cron hasn't fired yet, so kick each
   installed workflow and watch it to green (full loop in
   `reference/actions.md`):

   ```bash
   gh workflow run profile-snake.yml -R "$LOGIN/$LOGIN"   # ditto profile-refresh.yml
   sleep 5   # dispatched runs take a moment to appear
   run_id=$(gh run list -R "$LOGIN/$LOGIN" --workflow profile-snake.yml -L 1 --json databaseId -q '.[0].databaseId')
   gh run watch "$run_id" -R "$LOGIN/$LOGIN" --exit-status
   ```

   Then confirm the snake landed:
   `gh api "repos/$LOGIN/$LOGIN/contents/github-snake.svg?ref=output" -q .name`.
5. **Render check:** `gh api "repos/$LOGIN/$LOGIN/readme" -H "Accept: application/vnd.github.html"`
   must return rendered HTML. Then extract every image URL from the raw
   README — `src="https://..."` attributes and `![...](https://...)`
   markdown — and `curl -sIL -o /dev/null -w '%{http_code}'` each: **all must
   be 200** (catches typo'd skillicons names). Relative srcs (`assets/...`)
   check via `https://raw.githubusercontent.com/$LOGIN/$LOGIN/main/...`.
6. Finish with `open "https://github.com/$LOGIN"` so the user sees the live
   page.

**Exit criteria:** pushed; seeded runs green and the snake SVG on the
`output` branch; every image URL returns 200; profile page opened.

## Content blueprint

Fixed order — omit sections (2-item minimum), never reorder. Worked markup
per section in `reference/blueprint.md`.

| # | Section | Source | Dynamic? |
|---|---|---|---|
| 1 | Header — banner `<picture>` dark/light **or** typing-SVG hero, never both | gen-image / interview tagline | static |
| 2 | Social badges — shields `for-the-badge`, max 5 | interview | static |
| 3 | Now — current-focus bullets; recent-activity digest in `<details>` beneath | interview + dossier | managed `activity` |
| 4 | Start Here — 3–5 flagship repos (pinned ∪ top-starred), gated star badges, punched-up one-liners | dossier | managed `featured` |
| 5 | More things I built — topic/language clusters, 4–8 per category | dossier | static prose |
| 6 | Recently shipped — dated release lines | dossier | managed `shipped` |
| 7 | Toolbox — skillicons grid (cap 16); language histogram in `<details>` beneath | dossier | managed `languages` |
| 8 | Writing — blog-post-workflow, **only if a feed exists** | feed | action-managed |
| 9 | `<details>` Random facts | interview | static |
| 10 | Footer — philosophy line + snake `<picture>` | interview + snake Action | static |

## Taste budget & flattery law

The anti-widget-soup law — every README obeys all of it:

- **≤ 1 animated element above the fold** (banner or typing-SVG, not both).
- **≤ 2 stat widgets total**, snake bottom-only.
- Every section clears a **2-item minimum** or is omitted entirely.
- **One emoji per heading**, at most.
- Personality lives in exactly four places: the tagline, the repo one-liners,
  the random facts, and the philosophy line. Everywhere else stays plain.

**Flattery law:** a number appears only if it impresses — hidden, never
explained. The gates make "no numbers" read as a style choice, not a gap.
Never invent a flattering substitute; the only options are show or hide.

## Intensity levels

| Intensity | What it means |
|---|---|
| `polished` | Fully static: no workflows, no banner, no snake. Typing-SVG or plain hero; managed sections filled at compose time, refreshed only by skill re-runs. |
| `fancy` *(default)* | AI banner (dark/light `<picture>`), snake at the bottom, `render` installs the updater + snake + 6-hourly refresh workflows; `--with claude` if opted in. |
| `max` | fancy + `--with metrics` (lowlighter/metrics, classic PAT) and any extra widget that survives the taste budget **and** the stat-widget gate. |

## Escape hatches

- **No key / banner declined** → typing-SVG hero; delete the `<picture>`
  banner block so nothing dangles.
- **No Actions allowed** → `polished`: skip `render` entirely; the page is
  fully static and refreshes on skill re-runs.
- **Thin dossier** (< 4 showable repos) → collapse Start Here + More things
  into one "Things I've built" list — and tell the user that's what happened.
- **Emoji-averse user** → zero emoji; structure unchanged.
- **Foreign README, markers declined** → Sections-only: write the rendered
  sections to a scratch file, push nothing.
- **gen-image plugin missing** → install it from this marketplace
  (`gen-image@skills`) or apply the no-banner hatch.

## Common issues

**`MISSING: gh` / `AUTH=missing`**: install gh (https://cli.github.com), then
`gh auth login`.

**Push rejected: refusing to allow an OAuth App to create or update workflow**:
the token lacks the `workflow` scope. Commonly a stale `GH_TOKEN` env var is
overriding the keyring — `unset GH_TOKEN`, then
`gh auth refresh -h github.com -s repo,workflow` and push again.

**Profile renders nothing**: the repo is private —
`gh repo edit "$LOGIN/$LOGIN" --visibility public --accept-visibility-change-consequences`
after confirming with the user.

**Snake / asset 404 right after setup**: expected — the `output` branch
exists only after the first workflow run; Phase 5 kicks and watches it.

**`NOMARKER <id>`**: that marker pair is missing or typo'd; the updater
touched nothing for that section. Restore the exact
`<!-- gh-profile:start:<id> -->` / `<!-- gh-profile:end:<id> -->` lines.

**Activity section stale or empty**: the Events API lags 30 s–6 h and only
covers 90 days. Quiet accounts render an empty digest — the 2-item rule then
omits the section, by design.

**Cron stopped after ~60 days**: GitHub disables scheduled workflows in
inactive repos — `gh workflow enable profile-refresh.yml -R "$LOGIN/$LOGIN"`
per workflow. Any push re-arms them too.

**Claude refresh run fails on auth**: the `ANTHROPIC_API_KEY` secret is
missing or expired — re-run the `gh secret set` walkthrough in
`reference/actions.md`.

**User asks for github-readme-stats**: warn that the public instance is
rate-limited with outages; offer a self-hosted deployment
(`reference/widgets.md`) or the built-in `featured` section.

**`SCOPE_WORKFLOW=UNKNOWN`**: fine-grained token — gh can't report scopes.
If the push rejects workflow files, switch to OAuth (`gh auth login`) or
grant the token Workflows read/write.

## Reference map

Read these on demand — each is self-contained:

- `reference/blueprint.md` — the 10-section house style with worked markup
  per section, and where each managed marker lives.
- `reference/widgets.md` — the vetted widget catalog: exact URL forms, the
  green/red-light reliability table, the dark/light `<picture>` pattern.
- `reference/actions.md` — each workflow explained (triggers, secrets,
  seeding, output-branch snake mechanics) plus the `ANTHROPIC_API_KEY` and
  `METRICS_TOKEN` walkthroughs.
- `reference/data.md` — the dossier JSON schema, the verbatim gh commands
  with `--cache` flags, and flattery-gate semantics with threshold defaults.
