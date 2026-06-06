# CI and Release Pipelines

Three workflows land in `.github/workflows/`: `ci.yml` (tests + wheel smoke), `docs.yml`
(great-docs to GitHub Pages), and `release-pypi.yml` (tag-driven trusted publishing). Three
one-time browser setups make releases and docs deploys work; everything else is automatic.

## ci.yml

Triggers on `push` to `main` and every `pull_request`. Concurrency group `ci-${{ github.ref }}`
with `cancel-in-progress: true` kills stale runs when a PR gets a new push.

The `test` job matrixes over the Python floor and pin versions supplied at scaffold time
(`{{PYTHON_MIN}}` and `{{PYTHON_PIN}}`), with `fail-fast: false` so one version failing doesn't
mask the other. **If floor == pin, collapse the matrix** to a single entry — two identical jobs
waste minutes and clutter checks.

Steps, in order:

1. `actions/checkout@v6`
2. `astral-sh/setup-uv@v8` with `python-version: ${{ matrix.python-version }}` and
   `cache-dependency-glob: uv.lock` — caching keys off the lockfile, so commit `uv.lock`
   in the first push or this step warns and the cache never hits.
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
2. `astral-sh/setup-uv@v8` pinned to the scaffold-time pin version, same
   `cache-dependency-glob: uv.lock`
3. `quarto-dev/quarto-actions/setup@v2` — great-docs renders via Quarto
4. `uv sync --group docs` — docs deps live in `[dependency-groups] docs` in `pyproject.toml`
   (note: dependency *group*, not the `dev` extra)
5. `uv run great-docs build`
6. `actions/upload-pages-artifact@v5` with `path: great-docs/_site` and
   `include-hidden-files: true` (the site contains dotfiles that Pages needs)

**`publish-docs`**: `needs: build-docs`, gated `if: github.ref == 'refs/heads/main'`,
permissions `pages: write` + `id-token: write`, environment `github-pages` with
`url: ${{ steps.deployment.outputs.page_url }}`. Single step: `actions/deploy-pages@v5`.

## release-pypi.yml

Triggers on `push` of tags matching `v*`. Concurrency group `release-pypi` with
`cancel-in-progress: false` — never kill a half-finished publish. Three chained jobs:

**`build`**:

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

## One-Time Setup (walk the user through these in a browser)

These cannot be done from the CLI without elevated credentials; do them before the first tag.

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

Repo → **Settings** → **Pages** → **Source** = **GitHub Actions** (not "Deploy from a branch").
Without this, `deploy-pages` fails on the first main push.

## Cutting a Release

1. Update `CHANGELOG.md`: move `[Unreleased]` entries into a new `## [X.Y.Z] - <date>` section
   and add the version's link reference at the bottom.
2. Commit, then `git tag vX.Y.Z` and `git push --tags` (push the commit too).
3. Watch the **Release (PyPI)** workflow run: build → publish → github-release.
4. Verify the version on PyPI (`https://pypi.org/project/<dist-name>/`) and the new GitHub
   release with generated notes and `dist/*` assets.

Do not edit `version = "0.1.0"` in `pyproject.toml` — `uv version --frozen` derives it from
the tag at build time.

## Common Failures

- **Tag pushed before the pending publisher was registered** — the `publish` job 403s
  ("invalid-publisher"). Register the publisher on PyPI, then re-run just the failed job from
  the workflow run page; no need to re-tag.
- **Pages source not set to GitHub Actions** — `publish-docs` fails at `deploy-pages` with a
  "Not Found" / pages-not-enabled error. Flip the setting, re-run the job.
- **`uv.lock` missing from the first commit** — `setup-uv` warns that `cache-dependency-glob:
  uv.lock` matched nothing and every CI run resolves from scratch. Run `uv sync` locally and
  commit the lockfile.
- **Environment name mismatch** — the workflow's `environment: pypi` must match both the
  GitHub environment name and the PyPI publisher's environment field exactly.
