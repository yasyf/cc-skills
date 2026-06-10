# CI and Release Pipelines

Up to three workflows land in `.github/workflows/`: `ci.yml` (tests + wheel smoke, **always**),
`docs.yml` (great-docs to GitHub Pages, **feature `docs`**), and `release-pypi.yml` (tag-driven
trusted publishing, **feature `pypi`**). The one-time setups below only matter for the
features you enabled — skip the Pages setup without `docs`, skip the PyPI setup without `pypi`.

## ci.yml

Triggers on `push` to `main` and every `pull_request`. Concurrency group `ci-${{ github.ref }}`
with `cancel-in-progress: true` kills stale runs when a PR gets a new push.

The `test` job matrixes over the Python floor and pin versions supplied at scaffold time
(`{{PYTHON_MIN}}` and `{{PYTHON_PIN}}`), with `fail-fast: false` so one version failing doesn't
mask the other. **If floor == pin, collapse the matrix** to a single entry — two identical jobs
waste minutes and clutter checks.

Steps, in order:

1. `actions/checkout@v6`
2. `astral-sh/setup-uv@v8.2.0` with `python-version: ${{ matrix.python-version }}` and
   `cache-dependency-glob: uv.lock` — caching keys off the lockfile, so commit `uv.lock`
   in the first push or this step warns and the cache never hits. setup-uv publishes no
   floating major tag past `v7`, so the pin is exact-semver — at scaffold time, check the
   latest release (`gh api repos/astral-sh/setup-uv/releases/latest`) and bump if newer.
3. `uv sync --extra dev`
4. `uv run pytest`
5. Wheel smoke test:

   ```bash
   uv build
   rm -rf .wheel-smoke
   uv venv --seed .wheel-smoke
   uv pip install --python .wheel-smoke/bin/python dist/*.whl
   .wheel-smoke/bin/<dist-name> --help
   ```

   The last line invokes the console script by the dist name supplied at scaffold time
   (for captain-hook: `.wheel-smoke/bin/capt-hook --help`). This catches whole classes of
   bugs pytest never sees because pytest runs in the dev venv with the source tree on path:
   - **Missing console script** — `[project.scripts]` entry typo'd or pointing at a missing
     `main` (template wires `{{DIST_NAME}} = "{{PACKAGE}}.cli:main"` in `pyproject.toml`).
   - **Wrong module packaged** — `[tool.uv.build-backend] module-name` not matching the
     actual package directory, so the wheel ships empty or ships the wrong tree.
   - **Missing `py.typed` or data files** — files present in the repo but absent from the wheel.
   - **Import errors only visible outside the dev venv** — e.g. importing a dev-only
     dependency from runtime code.

## docs.yml

Same triggers and a `docs-${{ github.ref }}` cancel-in-progress concurrency group. Two jobs:

**`build-docs`** (runs on PRs too, so docs breakage blocks merge):

1. `actions/checkout@v6` with `fetch-depth: 0` (great-docs reads git history)
2. `astral-sh/setup-uv@v8.2.0` pinned to the scaffold-time pin version, same
   `cache-dependency-glob: uv.lock`
3. `quarto-dev/quarto-actions/setup@v2` — great-docs renders via Quarto
4. `uv sync --group docs` — docs deps live in `[dependency-groups] docs` in `pyproject.toml`
   (note: dependency *group*, not the `dev` extra)
5. `uv run great-docs build` with `env: GITHUB_TOKEN: ${{ github.token }}` — the token lets
   great-docs embed the navbar widget's star/fork counts at build time, so visitors' browsers
   never hit (and 403 on) the GitHub API
6. `uv run python docs/scripts/fix_color_swatch.py` — rewrites great-docs' broken runtime
   `color-swatch.js` loader to depth-correct static tags (see `reference/docs-site.md`)
7. `actions/upload-pages-artifact@v5` with `path: great-docs/_site` and
   `include-hidden-files: true` (the site contains dotfiles that Pages needs)

**`publish-docs`**: `needs: build-docs`, gated `if: github.ref == 'refs/heads/main'`,
permissions `pages: write` + `id-token: write`, environment `github-pages` with
`url: ${{ steps.deployment.outputs.page_url }}`. Single step: `actions/deploy-pages@v5`.

## release-pypi.yml

Triggers on `push` of tags matching `v*`. Concurrency group `release-pypi` with
`cancel-in-progress: false` — never kill a half-finished publish. Four chained jobs:

**`verify-tag-on-main`** (the gate): checks out with `fetch-depth: 0`, fetches `main`,
and runs `git merge-base --is-ancestor "$GITHUB_SHA" FETCH_HEAD`. The job fails if the
tagged commit is not reachable from `main`. `build` has `needs: verify-tag-on-main`, so
nothing builds, publishes, or releases unless the tag points at a commit already on
`main`. Tags are not covered by branch protection, so without this gate anyone who can
push a tag could ship an unmerged commit to PyPI under the project's identity. A commit
is its own ancestor, so tagging main's exact tip passes; squash-merge repos must tag the
squashed commit on `main` (e.g. `git tag vX.Y.Z origin/main`), not the pre-squash branch
commit.

**`build`** (`needs: verify-tag-on-main`):

1. Checkout + setup-uv (pin version, lockfile cache glob)
2. `uv version --frozen "${GITHUB_REF_NAME#v}"` — strips the `v` and writes the tag's
   version into `pyproject.toml` for this build only. The committed `version = "0.1.0"`
   is a placeholder that is **never hand-bumped**; the tag is the single source of truth.
3. `uv build`
4. `actions/upload-artifact@v7` with `name: dist`, `path: dist/*`, `if-no-files-found: error`

**`publish`**: `needs: build`, `environment: pypi`, `permissions: id-token: write`. Downloads
the `dist` artifact and runs `pypa/gh-action-pypi-publish@release/v1` with no inputs. This is
OIDC trusted publishing — there is **no API token anywhere**: no secret, no `password:` input.
The `id-token: write` permission plus the `pypi` environment plus the PyPI-side publisher
registration (below) are the entire auth story.

**`github-release`**: `needs: publish`, `permissions: contents: write`. Downloads `dist` and runs:

```bash
gh release create "${GITHUB_REF_NAME}" \
  --title "${GITHUB_REF_NAME}" \
  --generate-notes \
  --latest \
  --repo "${{ github.repository }}" \
  dist/*
```

with `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`. Release notes are auto-generated from merged PRs;
the built sdist and wheel attach as release assets.

## One-Time Setup

Do these before the first tag. Walk the user through the PyPI steps (a–b) in a browser;
the Pages step (c) runs from the CLI.

### a. PyPI pending publisher

PyPI account → **Publishing** → **Add a new pending publisher** (the "pending" form works
before the project exists on PyPI):

| Field | Value | captain-hook example |
|---|---|---|
| PyPI Project Name | the dist name from scaffold time | `capt-hook` |
| Owner | the GitHub user/org | `yasyf` |
| Repository name | the repo name | `captain-hook` |
| Workflow name | `release-pypi.yml` | `release-pypi.yml` |
| Environment name | `pypi` | `pypi` |

### b. GitHub `pypi` environment

Repo → **Settings** → **Environments** → **New environment** → name it exactly `pypi`.
Optionally add required reviewers so a human approves each publish.

### c. GitHub Pages source

```bash
gh api repos/{owner}/{repo}/pages -X POST -f build_type=workflow ||
  gh api repos/{owner}/{repo}/pages -X PUT -f build_type=workflow
```

POST creates the site; on 409 (already enabled) the PUT flips it to the Actions build.
Browser fallback: Repo → **Settings** → **Pages** → **Source** = **GitHub Actions** (not
"Deploy from a branch"). Without this, `deploy-pages` fails on the first main push. While
here, point the repo homepage at the docs site: `gh repo edit --homepage "$DOCS_URL"`.

## Cutting a Release

1. Update `CHANGELOG.md`: move `[Unreleased]` entries into a new `## [X.Y.Z] - <date>` section
   and add the version's link reference at the bottom.
2. Commit and push to `main` first, then tag a commit that is on `main` — e.g.
   `git tag vX.Y.Z origin/main` after pulling — and `git push --tags`. The
   `verify-tag-on-main` gate fails the release if the tag points anywhere off `main`.
3. Watch the **Release (PyPI)** workflow run: verify-tag-on-main → build → publish →
   github-release.
4. Verify the version on PyPI (`https://pypi.org/project/<dist-name>/`) and the new GitHub
   release with generated notes and `dist/*` assets.

Do not edit `version = "0.1.0"` in `pyproject.toml` — `uv version --frozen` derives it from
the tag at build time.

## Common Failures

- **Tag not on main** — the `verify-tag-on-main` job fails before any build runs because
  the tagged commit isn't reachable from `main`. Delete the tag (`git tag -d vX.Y.Z &&
  git push origin :refs/tags/vX.Y.Z`), merge the commit to `main`, re-tag the merged
  commit, and push the tag again.
- **Tag pushed before the pending publisher was registered** — the `publish` job 403s
  ("invalid-publisher"). Register the publisher on PyPI, then re-run just the failed job from
  the workflow run page; no need to re-tag.
- **Pages source not set to GitHub Actions** — `publish-docs` fails at `deploy-pages` with a
  "Not Found" / pages-not-enabled error. Run the Pages `gh api` command above, re-run the job.
- **`uv.lock` missing from the first commit** — `setup-uv` warns that `cache-dependency-glob:
  uv.lock` matched nothing and every CI run resolves from scratch. Run `uv sync` locally and
  commit the lockfile.
- **Environment name mismatch** — the workflow's `environment: pypi` must match both the
  GitHub environment name and the PyPI publisher's environment field exactly.
