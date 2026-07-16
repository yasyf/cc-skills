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

1. `actions/checkout@v7`
2. `astral-sh/setup-uv@v8.2.0` with `python-version: ${{ matrix.python-version }}` and
   `cache-dependency-glob: uv.lock` — caching keys off the lockfile, so commit `uv.lock`
   in the first push or this step warns and the cache never hits. setup-uv publishes no
   floating major tag past `v7`, so the pin is exact-semver — at scaffold time, check the
   latest release (`gh api repos/astral-sh/setup-uv/releases/latest`) and bump if newer.
3. `uv sync --extra dev`
4. `uvx prek run ty --all-files` with `continue-on-error: true` — re-runs the ty commit hook
   (warnings only; `[tool.ty.rules] all = "warn"` already keeps ty's exit code 0) as the
   backstop for clones that never ran `uvx prek install`
5. `uv run pytest`
6. Wheel smoke test:

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

Same triggers and a `docs-${{ github.ref }}` cancel-in-progress concurrency group. The file
is a cc-guides rendered artifact: a repo-local preamble piece plus the shared
`cc-skills:docs-build-{head,sync,tail}` and `cc-skills:docs-publish` pieces. Two jobs:

**`build-docs`** (runs on PRs too, so docs breakage blocks merge):

1. `actions/checkout@v7` with `fetch-depth: 0` (great-docs reads git history)
2. `astral-sh/setup-uv@v8.2.0` pinned to the scaffold-time pin version, same
   `cache-dependency-glob: uv.lock`
3. `quarto-dev/quarto-actions/setup@v2` pinned to Quarto 1.9.38 — the newest stable release
   (1.10 is prerelease-only); the pin bumps centrally in the `docs-build-head` fragment
4. `uv sync --group docs` — docs deps live in `[dependency-groups] docs` in `pyproject.toml`
   (note: dependency *group*, not the `dev` extra)
5. `uv run --with "git+https://github.com/yasyf/cc-skills@main#subdirectory=tools/gd-build" gd-build build`
   with `env: GITHUB_TOKEN: ${{ github.token }}` — the token lets great-docs embed the navbar
   widget's star/fork counts at build time, so visitors' browsers never hit (and 403 on) the
   GitHub API. gd-build materializes `docs/scripts/.gd-build/native_reference_titles.py` (the
   `pre_render` entry that keeps a large API reference's build linear), applies version-gated
   great-docs perf patches (each degrades to a stock build, never a failure), and delegates
   to `great-docs build` (see `reference/docs-site.md`). The job carries
   `timeout-minutes: 45` as a regression guard
6. `actions/upload-pages-artifact@v5` with `path: great-docs/_site` and
   `include-hidden-files: true` (the site contains dotfiles that Pages needs)

**`publish-docs`**: `needs: build-docs`, gated `if: github.ref == 'refs/heads/main'`,
permissions `pages: write` + `id-token: write`, environment `github-pages` with
`url: ${{ steps.deployment.outputs.page_url }}`. Single step: `actions/deploy-pages@v5`.

## release-pypi.yml

The repo's `release-pypi.yml` is a **caller**: it delegates the build to the fleet's shared
reusable workflow `<owner>/homebrew-tap/.github/workflows/release-pypi-build.yml@pypi-v1` (the
Python sibling of the Go `release-go.yml@v1`), then runs the OIDC **publish** + **github-release**
jobs *itself*. Publish must run in this repo's workflow, not the reusable one: PyPI Trusted
Publishing authenticates via the OIDC `job_workflow_ref` claim, which inside a reusable workflow
points at the homebrew-tap repo — and PyPI does **not** support reusable workflows for Trusted
Publishing. Keeping publish in the caller makes `job_workflow_ref` this repo's `release-pypi.yml`,
matching the repo's existing trusted publisher (no pypi.org change).

The `build` job forwards `secrets: inherit` and `with:` inputs:

| Input | Meaning |
|---|---|
| `dist-name` | PyPI project name (defaults to the repo name; set it when dist != repo, e.g. `capt-hook`, `dly`) |
| `python-version` | the setup-uv pin |
| `maturin` | `true` for a PyO3 native-extension repo (builds per-platform wheels); omit for pure-Python |

**Reusable `release-pypi-build.yml`** runs the shared, duplicated half:

- **`verify-tag-on-main`** (the gate): fetches `main` and runs `git merge-base --is-ancestor`,
  failing unless the tagged commit is reachable from `main`. Tags aren't covered by branch
  protection, so without this gate anyone who can push a tag could ship an unmerged commit to
  PyPI. A commit is its own ancestor, so tagging main's tip passes; squash-merge repos must tag
  the squashed commit (`git tag vX.Y.Z origin/main`).
- **build**: setup-uv → the *tag must exceed the latest published version* guard (PyPI JSON API)
  → `uv version --frozen "${GITHUB_REF_NAME#v}"` stamps the tag's version into `pyproject.toml`
  for this build only (the committed `version = "0.0.0"` is an inert sentinel, **never
  hand-bumped**) → `uv build`. A `maturin: true` repo instead builds a per-platform native-wheel
  matrix (macOS arm64/x86_64, manylinux x86_64/aarch64) plus an sdist. The wheel ABI (abi3 vs
  per-CPython) comes from the crate's pyo3 features, not the workflow — e.g. an `abi3-py313`
  crate ships one `cp313-abi3` wheel per platform.
- Uploads `dist*` artifacts and outputs the resolved `tag` for the caller's github-release.

**Caller `publish`** (`needs: build`): `environment: pypi`, `permissions: id-token: write`,
downloads `dist*`, runs `pypa/gh-action-pypi-publish` (no inputs). OIDC trusted publishing —
**no API token anywhere**. **Caller `github-release`** (`needs: [build, publish]`): downloads
`dist*` and `gh release create "${{ needs.build.outputs.tag }}" --generate-notes --latest`.

> **Keep the caller file named `release-pypi.yml` and the environment `pypi`.** The trusted
> publisher matches the OIDC `job_workflow_ref` (this repo's `release-pypi.yml`) + environment
> `pypi`; rename either and `publish` 403s with `invalid-publisher`.

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

Register this **before pushing the first `v*` tag**: a brand-new project name has no publisher
yet, so the first release dies in the `publish` job with `invalid-publisher: valid token, but no
corresponding publisher`. The **Environment name is required whenever the publish job sets
`environment:`** — the OIDC subject GitHub presents is `repo:<owner>/<repo>:environment:<env>`, so
a blank Environment on the PyPI side never matches. This is a manual account-owner step (PyPI
login + 2FA); the scaffold can't automate it.

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
3. Watch the release to completion and verify it with the bundled helper — it resolves
   the run for the tag (preferring the `Release` workflow), waits, then prints per-job
   conclusions, the GitHub release URL + `dist/*` asset names, and (with `--pypi`)
   confirms the version is on PyPI. It walks verify-tag-on-main → build → publish →
   github-release and exits non-zero if the run failed:

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/watch-release.sh" \
     --tag vX.Y.Z --pypi DIST_NAME
   ```

   Drop `--pypi` for a non-PyPI release (e.g. a Homebrew formula bump — it still reports
   the jobs and GitHub release assets); pass `--repo OWNER/REPO` when not running from
   the repo, or an explicit run id / `--workflow NAME` to disambiguate. (The
   `${CLAUDE_PLUGIN_ROOT}` token is substituted to a real path when this skill runs.)

Do not edit `version = "0.0.0"` in `pyproject.toml` — `uv version --frozen` derives it from
the tag at build time.

## Common Failures

- **Tag not on main** — the `verify-tag-on-main` job fails before any build runs because
  the tagged commit isn't reachable from `main`. Delete the tag (`git tag -d vX.Y.Z &&
  git push origin :refs/tags/vX.Y.Z`), merge the commit to `main`, re-tag the merged
  commit, and push the tag again.
- **Tag pushed before the pending publisher was registered** — the `publish` job 403s
  ("invalid-publisher"). Register the publisher on PyPI, then re-run just the failed job from
  the workflow run page (re-check it with `watch-release.sh <run-id>`); no need to re-tag.
- **Pages source not set to GitHub Actions** — `publish-docs` fails at `deploy-pages` with a
  "Not Found" / pages-not-enabled error. Run the Pages `gh api` command above, re-run the job.
- **`uv.lock` missing from the first commit** — `setup-uv` warns that `cache-dependency-glob:
  uv.lock` matched nothing and every CI run resolves from scratch. Run `uv sync` locally and
  commit the lockfile.
- **Environment name mismatch** — the workflow's `environment: pypi` must match both the
  GitHub environment name and the PyPI publisher's environment field exactly.
