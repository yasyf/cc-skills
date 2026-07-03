# ![{{PROJECT_NAME}}]({{#FEATURE_PYPI}}{{REPO_URL}}/raw/main/{{/FEATURE_PYPI}}docs/assets/readme-banner.webp)

{{> _partials/readme-opener.md}}

[![CI](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/ci.yml?branch=main&label=ci)]({{REPO_URL}}/actions/workflows/ci.yml)
{{#FEATURE_PYPI}}
[![PyPI](https://img.shields.io/pypi/v/{{DIST_NAME}}.svg)](https://pypi.org/project/{{DIST_NAME}}/)
{{/FEATURE_PYPI}}
{{#FEATURE_DOCS}}
[![Docs](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/docs.yml?branch=main&label=docs)]({{DOCS_URL}})
{{/FEATURE_DOCS}}
{{#HAS_LICENSE}}
[![License: {{LICENSE_ID}}](https://img.shields.io/badge/License-{{LICENSE_BADGE}}-blue.svg)]({{REPO_URL}}/blob/main/LICENSE)
{{/HAS_LICENSE}}

## Get started

{{#FEATURE_PYPI}}
```bash
uvx {{DIST_NAME}}
```
{{^FEATURE_DOCS}}

<details>
<summary>Use it as a library</summary>

```bash
uv add {{DIST_NAME}}
```

</details>
{{/FEATURE_DOCS}}
{{/FEATURE_PYPI}}
{{^FEATURE_PYPI}}
```bash
git clone {{REPO_URL}}
cd {{PROJECT_NAME}}
uv run {{DIST_NAME}}
```
{{/FEATURE_PYPI}}

<img src="{{#FEATURE_PYPI}}{{REPO_URL}}/raw/main/{{/FEATURE_PYPI}}docs/assets/demo.png" alt="TODO(bootstrap): Terminal running the command above, and its visible result" width="700">

TODO(bootstrap): finish the fence above with the real first command, then capture
the demo as a real run of that exact command. Default: a static terminal
screenshot via freeze, with the one-liner committed at `docs/scripts/demo.sh`.
When motion is the payoff (TUI, progress, multi-step flow): an animated SVG via
the cli-demo skill, with `.cli-demo/demo.tape` committed. No tooling: replace the
img line with a fenced output block.

Driving with an agent? Paste this:

```text
{{#FEATURE_PYPI}}
Install {{PROJECT_NAME}} (run it via `uvx {{DIST_NAME}}`) and TODO(bootstrap): the first concrete goal to hand an agent.
{{/FEATURE_PYPI}}
{{^FEATURE_PYPI}}
Clone {{REPO_URL}} and run it via `uv run {{DIST_NAME}}`; TODO(bootstrap): the first concrete goal to hand an agent.
{{/FEATURE_PYPI}}
{{#FEATURE_DOCS}}
Docs: {{DOCS_URL}}
{{/FEATURE_DOCS}}
```

{{> _partials/readme-use-cases.md}}

{{#FEATURE_DOCS}}
## More in the docs

TODO(bootstrap): 3-6 teaser lines — **bold feature name**, a benefit clause, and
a deep link into the docs. Teasers funnel; they never document.

Read the [docs]({{DOCS_URL}}) for the full guide.
{{/FEATURE_DOCS}}
{{^FEATURE_DOCS}}
{{> _partials/readme-inline-tail.md}}
{{/FEATURE_DOCS}}

{{> _partials/readme-footer.md}}
