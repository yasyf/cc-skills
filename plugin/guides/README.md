# guides

The fleet's shared guide-fragment pack. The repo root's `.claude/cc-guides.toml` publishes this directory (`guides = "plugin/guides"`), and ~40 repos consume it as `github:yasyf/cc-skills@main` to render their standard artifact set: `AGENTS.md`, `CLAUDE.md`, `.claude/settings.json`, and `.gitignore`.

## Layout

Five fragment kinds, one directory each:

| Directory | Contents |
|-----------|----------|
| `md/` | `AGENTS.md`/`CLAUDE.md` prose fragments: `ask-before-assuming`, `ccx`, `claude-rules`, `code-review-response`, `parallelize`, `version-control`, `writing-plans` |
| `sh/` | `binrun-shim.sh` (the binrun wrapper) + `render-descriptor.sh` (per-release descriptor render), and `install-binary-pinned.sh` (the transitional direct installer for consumers not yet on binrun) |
| `json/` | `settings-base.json` plus `settings-go`/`settings-python`/`settings-swift`/`settings-bun`, deep-merged |
| `yml/` | Docs-site workflow pieces (`docs-build-*`, `docs-publish`, `great-docs-*`) and the prek `.pre-commit-config.yaml` pieces: `precommit-base` owns the `repos:` key and carries the `repo: builtin` hygiene hooks; `precommit-go`/`precommit-python`/`precommit-swift` continue the list and hold the centrally managed rev pins |
| `gitignore/` | Root-`.gitignore` pieces — `gitignore-base` plus per-layer `gitignore-python`/`gitignore-go`/`gitignore-swift`/`gitignore-bun` and the docs-feature `gitignore-docs`, concatenated in layout order; a consumer's repo-specific residue rides last in its local `gitignore-local` fragment so negations win |

## How a repo consumes it

Each consumer declares a `layout.toml` per artifact under `.claude/fragments/<artifact-path>/`, with a local `settings-overrides.fragment.json` as its overlay. `.claude/fragments/cc-guides.lock` pins the source commit. `.github/workflows/guides.yml` is a never-changing shim onto `yasyf/cc-guides/.github/workflows/guides.yml@main`: every push to main re-renders and commits the artifacts and lock (a daily `17 9 * * *` cron backstops quiet repos), and a pull request that hand-edits a rendered artifact fails `pr-check` with a pointer at the fragment dir to edit instead.

## Propagation

Pull-only. Each consumer re-renders on its own pushes and its daily cron, so a pack change lands in active repos on their next push and fleet-wide within 24 hours of merging — let it. Never run a fleet-wide dispatch loop or a manual render sweep. A single quiet repo that needs its render early can trigger its own workflow (`gh workflow run guides.yml -R yasyf/<repo>`); that trigger exists for one repo at a time, not for fan-outs.

One reliability caveat: GitHub disables a repo's scheduled workflows after 60 days without repo activity, and scheduled runs themselves don't count as activity. A dormant repo stops pulling silently — re-enable with `gh workflow enable guides.yml -R yasyf/<repo>` when it wakes up.

## Editing

Never hand-edit a rendered artifact — an artifact-only edit self-reverts on the next render, and a PR carrying one goes red on `pr-check`. Never run `cc-guides render` locally either: edit the fragment, commit and push the fragment alone, and CI renders and commits the artifacts and lock. The only sanctioned local renders are repo-creation flows (repo-bootstrap's scaffold and settings-onboard's first render), where the artifacts must exist before the repo has CI.

## Fleet coverage

Every active development repo renders from this pack. Standing exclusions:

| Repo | Why excluded |
|------|--------------|
| `homebrew-tap`, `homebrew-do`, `homebrew-summ` | Machine-committed Formula/Cask output repos |
| `yasyf` | Profile README, gh-profile-managed |
| `landing-pages` | Empty placeholder |
| `gpt-do`, `summ` | Owner decision (2026-07-11) |

One behavioral exception: captain-hook renders from the standard base + python + overrides layout, but its overlay pins `"captain-hook@captain-hook": false` and carries a repo-local `hooks` block dispatching the in-development `.venv/bin/hook` client — a plugin cannot dependency-enable itself in its own dev repo.
