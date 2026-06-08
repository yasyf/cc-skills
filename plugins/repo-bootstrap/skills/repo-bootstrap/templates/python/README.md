# {{PROJECT_NAME}}

{{#FEATURE_PYPI}}
[![PyPI](https://img.shields.io/pypi/v/{{DIST_NAME}}.svg)](https://pypi.org/project/{{DIST_NAME}}/)
[![Python](https://img.shields.io/pypi/pyversions/{{DIST_NAME}}.svg)](https://pypi.org/project/{{DIST_NAME}}/)
{{/FEATURE_PYPI}}
{{#FEATURE_DOCS}}
[![Docs](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/docs.yml?branch=main&label=docs)]({{DOCS_URL}})
{{/FEATURE_DOCS}}
[![License: {{LICENSE_ID}}](https://img.shields.io/badge/License-{{LICENSE_ID}}-blue.svg)]({{REPO_URL}}/blob/main/LICENSE)

{{DESCRIPTION}}

TODO(bootstrap): expand the one-line description into a two-sentence pitch — what
it is, and the one property that makes it worth using.

## Install

{{#FEATURE_PYPI}}
No install needed — run everything through [uvx](https://docs.astral.sh/uv/):

```bash
uvx {{DIST_NAME}} --help
```

`uvx` fetches {{PROJECT_NAME}} into a throwaway environment and runs it. To add it
to a project instead:

```bash
uv add {{DIST_NAME}}
```
{{/FEATURE_PYPI}}
{{^FEATURE_PYPI}}
Clone the repo and run it with [uv](https://docs.astral.sh/uv/):

```bash
git clone {{REPO_URL}}
cd {{PROJECT_NAME}}
uv run {{DIST_NAME}} --help
```
{{/FEATURE_PYPI}}

## Quickstart

TODO(bootstrap): a complete, working example a reader can run in under 30 seconds,
with the expected output shown.

## What problems does this solve?

TODO(bootstrap): 3-4 bullets, each naming a concrete pain and how this addresses it.
{{#FEATURE_DOCS}}

## Docs

[Read the docs]({{DOCS_URL}}) for the full guide and API reference.
{{/FEATURE_DOCS}}
