# Content Blueprint — the 10-Section House Style

The fixed section order with one worked example each, for placeholder user
`octocat`. Omit any section that misses the 2-item minimum; never reorder.
Sections 3, 4, 6, and 7 carry the managed markers (`activity`, `featured`,
`shipped`, `languages`) — the marker **positions** are yours, the interiors
belong to the committed updater. The interiors shown below are illustrative:
write the empty marker pairs, then let the updater fill them.

Line 1 of the file — before everything — is the meta comment:

```html
<!-- gh-profile:meta {"intensity": "fancy", "last_refresh": "2026-06-12T00:00:00Z", "min_contributions": 750, "min_stars_badge": 30, "shipped_window_months": 6, "skill_version": "0.3.0"} -->
```

## 1 — Header

Banner `<picture>` (dark/light pair from gen-image) **or** typing-SVG hero —
never both; that would blow the one-animated-element budget. The `<source>`
carries the dark variant; the `<img>` is the light fallback and holds the alt
text.

Banner form:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/banner-dark.webp">
  <img src="assets/banner-light.webp" alt="octocat — friendly tentacled tools for developers" width="100%">
</picture>
```

Typing-SVG form (the no-banner escape hatch; URL anatomy in `widgets.md`):

```html
<h1 align="center">Hi, I'm Mona 👋</h1>
<p align="center">
  <img src="https://readme-typing-svg.herokuapp.com?font=Fira+Code&pause=1000&lines=Friendly+tentacled+tools+for+developers;Mascot+by+day%2C+maintainer+by+night" alt="Friendly tentacled tools for developers. Mascot by day, maintainer by night.">
</p>
```

## 2 — Social badges

shields.io static badges, `for-the-badge` style, **max 5**, each a real link
from the interview. One centered row.

```html
<p align="center">
  <a href="https://octocat.dev"><img src="https://img.shields.io/badge/Blog-octocat.dev-1f6feb?style=for-the-badge" alt="Blog"></a>
  <a href="https://twitter.com/octocat"><img src="https://img.shields.io/badge/Twitter-%40octocat-1da1f2?style=for-the-badge" alt="Twitter"></a>
  <a href="mailto:mona@github.com"><img src="https://img.shields.io/badge/Email-mona%40github.com-ea4335?style=for-the-badge" alt="Email"></a>
</p>
```

## 3 — Now

Hand-written current-focus bullets (interview answers, drafted from the
90-day events), with the managed `activity` digest folded into a `<details>`
beneath so timestamps and event noise stay below the fold.

```markdown
## 🔭 Now

- Rewriting [Spoon-Knife](https://github.com/octocat/Spoon-Knife)'s fork flow — fewer steps, same forks
- Reviewing community PRs on [Hello-World](https://github.com/octocat/Hello-World)

<details>
<summary>Recent activity</summary>

<!-- gh-profile:start:activity -->
- `2026-06-10` Pushed to [octocat/Spoon-Knife](https://github.com/octocat/Spoon-Knife) — rebuilt the fork flow around a single click
- `2026-06-08` Cut a release in [octocat/Hello-World](https://github.com/octocat/Hello-World)
<!-- gh-profile:end:activity -->

</details>
```

The updater appends `**N,NNN contributions in the last year**` inside the
markers only when the total clears `min_contributions`. The ` — summary`
suffixes come from the summaries sidecar (`data.md`) while it's fresh; a
line whose event has no summary renders plain, like the release line above.

## 4 — Start Here

The flagship list: pinned repos first, then top-scored fill, 3–5 items. The
updater owns the interior — plain-text star counts appear per the gate,
one-liners come from the dossier. After the first render, punch up the
surrounding framing (not the interior) in the user's voice.

```markdown
## 🚀 Start here

<!-- gh-profile:start:featured -->
- **[Hello-World](https://github.com/octocat/Hello-World)** ⭐ 2,048 — My first repository on GitHub! `JavaScript`
- **[Spoon-Knife](https://github.com/octocat/Spoon-Knife)** — This repo is for demonstration purposes only. `HTML`
<!-- gh-profile:end:featured -->
```

Want better one-liners here? Fix the repo descriptions at the source
(`gh repo edit octocat/Spoon-Knife -d "..."`) — the updater re-reads them
every run, so edits inside the markers get overwritten within hours.

## 5 — More things I built

Static prose: cluster the remaining dossier repos by topic or language, 4–8
items per category, every one-liner rewritten in the user's voice. Categories
with fewer than 2 items merge or vanish.

```markdown
## 🧰 More things I built

**Git, but friendlier**

- [git-consortium](https://github.com/octocat/git-consortium) — herding multi-remote setups so you don't have to
- [boysenberry-repo-1](https://github.com/octocat/boysenberry-repo-1) — the test bed where the bad ideas go first

**Teaching material**

- [hello-worId](https://github.com/octocat/hello-worId) — yes, that's a capital i; spot-the-bug as a service
- [octocat.github.io](https://github.com/octocat/octocat.github.io) — the website, built the boring way on purpose
```

## 6 — Recently shipped

Managed `shipped`: dated release lines within `shipped_window_months`. When
nothing shipped recently the interior renders **empty** and the 2-item rule
hides the whole section — staleness is never advertised.

```markdown
## 📦 Recently shipped

<!-- gh-profile:start:shipped -->
- `2026-05-30` [Hello-World v2.1.0](https://github.com/octocat/Hello-World/releases/tag/v2.1.0) — warp-stable orbital mechanics and a faster boot
- `2026-04-17` [Spoon-Knife v0.9.0](https://github.com/octocat/Spoon-Knife/releases/tag/v0.9.0)
<!-- gh-profile:end:shipped -->
```

The ` — ` suffix prefers the sidecar summary (what the release actually
shipped, written from its real changelog) over the bare release name; with
neither, the line is just the dated tag.

## 7 — Toolbox

skillicons.dev grid (cap 16 — beyond that it reads as a keyword dump), with
the managed `languages` text histogram in a `<details>` beneath. Pick icons
from the dossier's language list plus tools the user confirms; icon ids in
`widgets.md`.

````markdown
## 🛠 Toolbox

<p align="center">
  <img src="https://skillicons.dev/icons?i=ts,python,rust,go,docker,kubernetes" alt="TypeScript, Python, Rust, Go, Docker, Kubernetes">
</p>

<details>
<summary>Language breakdown</summary>

<!-- gh-profile:start:languages -->
```text
Python      ████████████████████   46%
TypeScript  █████████░░░░░░░░░░░   31%
Rust        ████░░░░░░░░░░░░░░░░   23%
```
<!-- gh-profile:end:languages -->

</details>
````

## 8 — Writing

Only when the user has a blog feed. Wire
[gautamkrishnar/blog-post-workflow](https://github.com/gautamkrishnar/blog-post-workflow)
by hand (it's not in this skill's templates); it uses its **own** comment
markers — don't confuse them with gh-profile's.

```markdown
## ✍️ Writing

<!-- BLOG-POST-LIST:START -->
<!-- BLOG-POST-LIST:END -->
```

```yaml
# .github/workflows/profile-blog-posts.yml
name: Latest blog posts
on:
  schedule:
    - cron: "0 8 * * *"
  workflow_dispatch:
permissions:
  contents: write
jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: gautamkrishnar/blog-post-workflow@v1
        with:
          feed_list: "https://octocat.dev/feed.xml"
          max_post_count: 5
```

## 9 — Random facts

Interview answers in a `<details>` — the one place pure whimsy belongs. Skip
the section if the user passes; never pad it with filler.

```markdown
<details>
<summary>🎲 Random facts</summary>

- I have five arms for typing and three for coffee
- My first commit message was "initial commit" and I stand by it
- I review PRs faster upside down

</details>
```

## 10 — Footer

Horizontal rule, the philosophy line, then the snake `<picture>` — the snake
goes here and nowhere else. The SVGs come from the `output` branch
(`actions.md` explains the mechanics) and 404 until the first workflow run.

```html
---

<p align="center"><em>Ship small, ship often.</em></p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/octocat/octocat/output/github-snake-dark.svg">
  <img src="https://raw.githubusercontent.com/octocat/octocat/output/github-snake.svg" alt="Contribution graph eaten by a snake">
</picture>
```
