# Python Stack: Rationale and Knobs

Every choice in the python layer, why it won, and what to adjust when the project deviates.
Worked example throughout: project `captain-hook`, dist+CLI `capt-hook`, package `captain_hook`.

## Stack at a Glance

| Choice | Why | Rejected alternative |
|---|---|---|
| uv + `uv_build` backend | One tool for env, lock, build, publish; backend is zero-config and fast | hatchling/setuptools — extra config surface, slower, second tool to version |
| Flat package layout | Package importable from repo root; no `src/` indirection for editable installs or grep paths | `src/` layout — solves an install-shadowing problem uv-managed envs don't have |
| Click | Groups, subcommands, `CliRunner` for tests, `version_option` from dist metadata | argparse — no test runner, manual subcommand wiring, no completion |
| loguru | Zero-config structured logging, one import | stdlib `logging` — handler/formatter boilerplate before the first log line |
| pytest, `--strict-markers` | Typo'd markers fail instead of silently passing; strict assertions are the house style | unittest — class ceremony, weak parametrize, no fixtures |
| ruff `E,F,I,UP` @ line-length 120 | Mechanical layer only; the prek commit hook owns enforcement, but run it by hand whenever it helps | flake8+isort+pyupgrade — three tools for what one does |
| ty (default) + pyright (basic, secondary) | ty is fast, handles modern syntax, and skips the strict-pyright false positives on pydantic/beanie dynamic defaults and PK-type overrides; pyright stays for editors. Pairs with "type everything" in STYLEGUIDE.md | strict pyright — noisy on dynamic defaults / PK-type overrides; mypy — slower, weaker inference on modern syntax |
| Great Docs | API reference generated from Google-style docstrings via one YAML file; publishes to GitHub Pages | mkdocs — nav/plugin config sprawl; Read the Docs — second platform to wire when Pages is already there |
| Async-native from day 1 | All I/O is `async`; use native-async drivers so the event loop never blocks | sync driver + `asyncio.to_thread` — leaks the sync boundary into every caller, caps throughput at the thread pool |

## The Naming Triad

Three names, three placeholders filled at scaffold time, three distinct jobs:

| Placeholder | Job | Worked example | Constraints |
|---|---|---|---|
| the dist name supplied at scaffold time | PyPI distribution **and** CLI command — they must match so `uvx <dist>` just works | `capt-hook` | Short, memorable, typeable. Hyphens fine. |
| the package name supplied at scaffold time | Import name (`import captain_hook`) | `captain_hook` | Valid identifier; underscores, no hyphens. May differ from the dist name. |
| the project name supplied at scaffold time | Repo / directory / docs display name | `captain-hook` | Usually the dist name spelled out, or the same. |

Before settling on a dist name, confirm it's both *unregistered* and *clear of PyPI's
name-similarity guard*. PyPI ultranormalizes a name — lowercases it and strips every `-`, `_`,
and `.` — and refuses registration when an existing project ultranormalizes to the same token
("This project name is too similar to an existing project"). Checking the exact name therefore
misses the guard; sweep the separator variants too. `check-name` covers the exact token only, so
run the variants by hand against the JSON endpoint:

```bash
for n in cookiesync cookie-sync cookie_sync; do
  printf '%s %s\n' "$n" "$(curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/$n/json)"
done
```

A `200` on the name *or any same-ultranormalization variant* means pick another (for `foobar`
also check `foo-bar`; for `foo-bar` also check `foobar` and `foo_bar`); only `404` across the
whole set is clear. `cookiesync` was blocked this way — the existing `cookie-sync`
ultranormalizes to the same `cookiesync` — and the fix kept the import package and console script
as `cookiesync` while publishing the *distribution* as `cookiesync-cli`; `uv_build`'s
`module-name` pins the module, so a distribution name that differs from the module name works.

A taken-or-too-similar dist name forces the triad apart (captain-hook's PyPI name `captain-hook`
was taken — hence `capt-hook`); that's fine, but decide it before scaffolding, since the dist name
threads through `pyproject.toml`, `README.md` badges, the console script, and `tests/test_cli.py`.
Confirm the GitHub repo slug is free too.

## pyproject.toml Walkthrough

Scaffolded with the values supplied at scaffold time. Everything below exists in the
generated file except the gated pieces: the `docs` dependency group and `Documentation`
URL appear only with feature `docs`, and the `license`/`license-files` lines are
dropped with license `none`.

**`[project]`** — `version = "0.1.0"` is a placeholder, never the real version: the release
workflow overwrites it from the git tag (see Versioning below). `requires-python = ">=<min>"`
is the support floor. Classifiers start at `Development Status :: 3 - Alpha`; bump to
`4 - Beta` when the API stabilizes (captain-hook ships Beta). `Typing :: Typed` is earned by
the `py.typed` marker — keep them in sync or drop both. Add `keywords` and per-version
`Programming Language :: Python :: 3.X` classifiers once support is deliberate.

**`dependencies`** — starts at `click>=8` and `loguru>=0.7`. Floor-only constraints
(`>=`), no upper bounds: upper-capping libraries causes resolver gridlock downstream.

**`[project.optional-dependencies].dev`** vs **`[dependency-groups].docs`** — deliberate split:
- `dev = ["anyio>=4", "pytest>=8.0", "ruff>=0.8"]` is an *extra*: it ships in dist
  metadata, so contributors and CI install it with `uv sync --extra dev` and consumers
  could `pip install <dist>[dev]`. ty is **not** in the extra: the ty-pre-commit hook rev
  pins its version and supplies it at run time — run it ad hoc with
  `uvx prek run ty --all-files`. pyright is config-only (run on demand via `uvx pyright`
  or an editor extension).
- `docs = ["griffelib>=2.0", "great-docs>=0.15,<0.16"]` is a PEP 735 *dependency group*:
  uv-local, invisible to PyPI consumers. Install with `uv sync --group docs`; build with
  `uv run great-docs build`, preview with `uv run great-docs preview`. The `<0.16` cap keeps
  new repos inside gd-build's patch-gate window (the deliberate exception to the floor-only
  rule above), and `griffelib>=2.0` forces griffe 2.x — see `reference/docs-site.md`.
Tooling that consumers might want goes in the extra; pure repo plumbing goes in a group.

**`[project.scripts]`** — `capt-hook = "captain_hook.cli:main"`: dist name on the left,
`<package>.cli:main` on the right. This line is why dist == CLI command.

**`[project.urls]`** — `Documentation` points at the docs URL supplied at scaffold time, which
is the GitHub Pages URL (`https://<user>.github.io/<project>/`) that
`.github/workflows/docs.yml` publishes to. `Changelog` points at
`<repo>/blob/main/CHANGELOG.md`. These render as sidebar links on PyPI — keep them live.

**`[build-system]` + `[tool.uv.build-backend]`** — the flat-layout mechanism:

```toml
[build-system]
requires = ["uv_build>=0.11,<0.12"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "captain_hook"
module-root = ""
```

`uv_build` defaults to expecting `src/<module>`; `module-root = ""` relocates the package to
the repo root and `module-name` names it explicitly. Both keys are required for flat layout —
deleting either breaks `uv build`. The backend is the one place with an upper bound
(`<0.12`), because build backends pin for reproducibility.

**`[tool.pytest.ini_options]`** — `testpaths = ["tests"]`,
`addopts = ["-ra", "--strict-markers", "--tb=short", "-q"]`, and registered markers
`unit` / `integration`. `--strict-markers` turns an unregistered `@pytest.mark.integraton`
typo into an error. `anyio_mode = "auto"` ships in the generated config, `anyio` (which
bundles the pytest plugin) in the dev extra, and `tests/conftest.py` pins the asyncio
backend — so async tests run with no extra wiring. This house is async-native and
centralizes on `anyio`, not `pytest-asyncio` (see § Async by Default).

**`[tool.ty.rules]` + `[tool.pyright]`** — **ty (Astral) is the default type checker**; it runs
on every commit via the prek hook (`astral-sh/ty-pre-commit` in `.pre-commit-config.yaml` — the
rev pins the ty version, `uvx prek autoupdate` bumps it) and re-runs in CI
(`uvx prek run ty --all-files`), since prek activation is opt-in per clone. In both places it is
**warning-only, never blocking**: `[tool.ty.rules]` sets `all = "warn"`, ty exits nonzero only on
error-level diagnostics, so warnings print and the commit/CI step proceeds. ty is fast, handles
modern syntax, and avoids the strict-pyright false positives that pydantic/beanie patterns
provoke — dynamic defaults (`Field(default_factory=list)` → `list[Unknown]`) and PK-type
overrides (`id: UUID` on a beanie `Document` → `reportIncompatibleVariableOverride`). Beyond
`all = "warn"`, `[tool.ty.rules]` only silences `unused-type-ignore-comment` so cross-checker
`# pyright: ignore` comments don't trip ty. pyright
stays as a secondary (editors / `uvx pyright`) in `typeCheckingMode = "basic"` with the
`reportUnknown*` and `reportIncompatibleVariableOverride` family set to `none` — pure noise on
typed pydantic code. `pythonVersion` is the floor; `include` scopes to the package (tests excluded
— test code mocks freely); `venvPath`/`venv` point pyright at uv's env. Fix real type errors;
don't reach for `Any`. Inside Claude Code sessions `.claude/settings.json` sets
`TY_CONFIG_FILE = .claude/ty-quiet.toml` (`[rules] all = "ignore"`), so ty emits zero diagnostics
and the agent can't thrash on noise — the commit hook inherits that env too (git runs hooks from
the repo root, so the relative path resolves), keeping in-session commits silent. Commits made
outside Claude sessions, CI, and editors see the real config above and get the warnings. (bioqa
disables a longer list — `reportMissingImports`,
`reportAttributeAccessIssue`, … — because its monorepo pulls many untyped scientific deps; a clean
package needs only the override/unknown-type silences.)

**`[tool.ruff]`** — `line-length = 120`, `target-version` matching the floor (e.g. `py312`),
`src = [".", "tests"]` for import-order resolution, and `select = ["E", "F", "I", "UP"]`:
pycodestyle errors, pyflakes, import sort, and pyupgrade. Deliberately small — style
judgment lives in STYLEGUIDE.md and review, not lint rules. Per AGENTS.md: the prek commit
hooks (`.pre-commit-config.yaml`: `astral-sh/ruff-pre-commit` + `astral-sh/ty-pre-commit`) own
ruff — auto-formatting and fixing import order on every commit; running ruff by hand is fine
too. CI does not run ruff; the commit hook is the mechanical-lint gate.

## .python-version vs requires-python

Two different knobs, both filled at scaffold time:

- `.python-version` holds the *pin* — the exact interpreter uv installs locally and the
  version CI's `setup-uv` uses (`python-version: "<pin>"` in the workflows). Develop on the
  newest version you support.
- `requires-python = ">=<min>"` in `pyproject.toml` is the *floor* — the oldest version
  consumers may run. `[tool.pyright].pythonVersion` and `[tool.ruff].target-version` must
  match the floor, not the pin, so checks catch syntax too new for supported consumers.

Pin ≥ floor, always. Raising the floor is a breaking change: bump all three keys together
and note it in CHANGELOG.md.

## Async by Default

Async from day 1 means the *library* is async, not a blocking call shoved onto a thread.
Write `async def` for anything that touches I/O and pick a driver with a native async API.
`asyncio.to_thread` / `run_in_executor` is an escape hatch for libraries with no async
equivalent, never the default — the thread-pool wrapper leaks a sync boundary into every
caller and caps throughput at the pool size. Structured concurrency goes through `anyio`
(`TaskGroup`s over hand-rolled `asyncio.gather` lifecycles), and tests use `anyio`'s bundled
pytest plugin — the scaffold ships `anyio` with `anyio_mode = "auto"` and a `tests/conftest.py`
pinning the asyncio backend, so the first async test runs unwired.

The house defaults, pulled from bioqa:

| Boundary | Async-native | Blocking alternative it replaces |
|---|---|---|
| HTTP client | `httpx` | `requests` |
| SQLite | `aiosqlite` | `sqlite3` + `asyncio.to_thread` |
| Postgres | `asyncpg` | `psycopg2` |
| MongoDB | `pymongo` `AsyncMongoClient` (4.13+) | `motor`, sync `pymongo` |
| Redis | `redis.asyncio` (`redis[hiredis]`) | `aioredis`, sync `redis` |
| Elasticsearch | `elasticsearch[async]` | sync `elasticsearch` |
| AWS / S3 | `aioboto3` | `boto3` |
| Filesystem | `aiofiles` | blocking `open()` on the event loop |

`motor` (folded into `pymongo`'s `AsyncMongoClient`) and `aioredis` (folded into
`redis.asyncio`) are retired — reach for the in-driver async API, not the legacy split
package. Structured concurrency and the test harness both run through `anyio`: use `anyio`
`TaskGroup`s over hand-rolled `asyncio.gather`, and the bundled `anyio` pytest plugin
(`anyio_mode = "auto"`) over `pytest-asyncio`. Pair with `uvloop` for a faster event loop
(`anyio` runs on it via the asyncio backend) when the project needs it.

## Starter Package Anatomy

For package `captain_hook`, the scaffold generates:

- `captain_hook/__init__.py` — docstring is the project description, then
  `from __future__ import annotations`. No `__all__`: as public API appears, re-export it here
  with plain imports (`from captain_hook.matcher import Matcher`; no `as` alias), per
  STYLEGUIDE.md § Code Organization. F401 is disabled for `__init__.py` (see `[tool.ruff]`) so
  those re-exports aren't flagged as unused.
- `captain_hook/cli.py` — a `@click.group()` function named `main` with
  `@click.version_option(package_name="capt-hook")`. `package_name` is the **dist** name, not
  the package: Click reads the version from installed distribution metadata. It ships one
  working `hello` command as a starter placeholder — bootstrapping leaves it in place;
  building real commands is product work for after the repo exists, not part of the scaffold.
- `captain_hook/__main__.py` — three lines enabling `python -m captain_hook`, mirroring the
  console script.
- `captain_hook/py.typed` — empty PEP 561 marker; ships type info to downstream checkers and
  justifies the `Typing :: Typed` classifier. Never delete one without the other.
- `tests/test_cli.py` — `CliRunner().invoke(main, ...)` driving the CLI in-process. Note
  `assert result.output.startswith("Usage: main")`: CliRunner derives the prog name from the
  *function* name, so renaming the group function breaks this assertion — update both together.

## Testing Conventions

The scaffolded tests model the house rules (full statement in STYLEGUIDE.md):

- **Strict assertions against specific values.** `test_hello_greets` asserts
  `result.output == "Hello from capt-hook!\n"` — full equality including the newline, not
  `"Hello" in result.output`. A test that can't fail uncovers nothing.
- **Parameterize repeats** with `@pytest.mark.parametrize`, each case carrying a descriptive
  `id` and its own expected values.
- **Mock boundaries only** — network, filesystem, clock. The code under test stays real;
  a test exercising a mock of the function proves nothing.
- **Databases use testcontainers, not mocks.** A database (or any stateful service: Mongo,
  Postgres, Redis, …) is *not* a boundary to mock. When a test needs one, start a real ephemeral
  instance with `testcontainers[<backend>]` (add it to the dev extra) and point the code under
  test at the container via a fixture. In-memory fakes and mocked drivers drift from real
  behaviour — they pass while production breaks. GitHub's Linux runners ship Docker, so
  testcontainers works in CI with no extra setup.
- **Mark tests** `unit` or `integration` (the registered markers); add new markers to
  `[tool.pytest.ini_options].markers` first or `--strict-markers` fails the run.

Run with `uv run pytest` after `uv sync --extra dev`.

## CHANGELOG and Versioning

`CHANGELOG.md` follows [Keep a Changelog]: an `[Unreleased]` section accrues entries under
`Added` / `Changed` / `Fixed` headings as work merges; the scaffold seeds it with
"Initial scaffolding." and a `[Unreleased]: <repo>/commits/main` link.

The version is **never hand-edited**. The flow:

1. `pyproject.toml` carries the inert `0.1.0` placeholder forever on `main`.
2. Pushing a `v*` tag (e.g. `v0.3.0`) triggers `.github/workflows/release-pypi.yml`.
   Its first job, `verify-tag-on-main`, fails the release unless the tagged commit is on
   `main` (`git merge-base --is-ancestor`), so an unmerged commit can't be published.
3. The workflow runs `uv version --frozen "${GITHUB_REF_NAME#v}"` — version derived from the
   tag at build time — then `uv build`, publishes via PyPI trusted publishing (the `pypi`
   environment, `id-token: write`), and cuts a GitHub release with `--generate-notes`.

So: a hand-bumped version in a PR is a bug; the single source of truth is the tag. When
cutting a release, promote `[Unreleased]` entries to a dated version section in
`CHANGELOG.md` in the same commit you tag.
