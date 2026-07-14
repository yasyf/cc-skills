# guides

The fleet's shared guide-fragment pack. The repo root's `.claude/cc-guides.toml` publishes this directory (`guides = "plugin/guides"`), and ~40 repos consume it as `github:yasyf/cc-skills@main` to render their standard artifact set: `AGENTS.md`, `CLAUDE.md`, and `.claude/settings.json`.

## Layout

Three fragment kinds, one directory each:

| Directory | Contents |
|-----------|----------|
| `md/` | `AGENTS.md`/`CLAUDE.md` prose fragments: `ask-before-assuming`, `ccx`, `claude-rules`, `code-review-response`, `parallelize`, `version-control`, `writing-plans` |
| `sh/` | `install-binary-latest.sh`, `install-binary-pinned.sh` |
| `json/` | `settings-base.json` plus `settings-go`/`settings-python`/`settings-swift`, deep-merged |

## How a repo consumes it

Each consumer declares a `layout.toml` per artifact under `.claude/fragments/<artifact-path>/`, with a local `settings-overrides.fragment.json` as its overlay. `.claude/fragments/cc-guides.lock` pins the source commit. `.github/workflows/guides.yml` checks drift on push and re-renders on a daily cron (`17 9 * * *`).

## Propagation

Pull-only. Each consumer re-renders on its own daily cron, so a pack change lands fleet-wide within 24 hours of merging — let it. Never run a fleet-wide dispatch loop or a manual render sweep. A single repo that needs its render early can trigger its own workflow (`gh workflow run guides.yml -R yasyf/<repo>`); that trigger exists for one repo at a time, not for fan-outs.

One reliability caveat: GitHub disables a repo's scheduled workflows after 60 days without repo activity, and scheduled runs themselves don't count as activity. A dormant repo stops pulling silently — re-enable with `gh workflow enable guides.yml -R yasyf/<repo>` when it wakes up.

## Editing

Never hand-edit a rendered artifact — an artifact-only edit self-reverts on the next cron render. Edit the fragment, render, and commit fragments, artifacts, and lock together in one commit.

## Fleet coverage

Every active development repo renders from this pack. Standing exclusions:

| Repo | Why excluded |
|------|--------------|
| `homebrew-tap`, `homebrew-do`, `homebrew-summ` | Machine-committed Formula/Cask output repos |
| `yasyf` | Profile README, gh-profile-managed |
| `landing-pages` | Empty placeholder |
| `gpt-do`, `summ` | Owner decision (2026-07-11) |

One structural exception: captain-hook's `.claude/settings.json` renders from a fully local fragment and pins `"captain-hook@captain-hook": false` — a plugin cannot dependency-enable itself in its own dev repo.
