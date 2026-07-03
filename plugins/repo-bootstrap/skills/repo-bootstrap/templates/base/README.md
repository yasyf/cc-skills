# ![{{PROJECT_NAME}}](docs/assets/readme-banner.webp)

{{> _partials/readme-opener.md}}

[![CI](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/ci.yml?branch=main&label=ci)]({{REPO_URL}}/actions/workflows/ci.yml)
{{#HAS_LICENSE}}
[![License: {{LICENSE_ID}}](https://img.shields.io/badge/License-{{LICENSE_BADGE}}-blue.svg)]({{REPO_URL}}/blob/main/LICENSE)
{{/HAS_LICENSE}}

## Get started

TODO(bootstrap): the one canonical path from zero to a first result — a single
fenced command block, no narration. A genuinely distinct alternate goes in one
`<details>` block; a redundant one gets cut.

<img src="docs/assets/demo.png" alt="TODO(bootstrap): Terminal running the command above, and its visible result" width="700">

TODO(bootstrap): demo media — a real run of the exact command above. Default: a
static terminal screenshot via freeze, with the one-liner committed at
`docs/scripts/demo.sh`. When motion is the payoff (TUI, progress, multi-step
flow): an animated SVG via the cli-demo skill, with `.cli-demo/demo.tape`
committed. No tooling: replace the img line with a fenced output block.

Driving with an agent? Paste this:

```text
TODO(bootstrap): the exact install/run invocation and the first concrete goal to hand an agent.
```

{{> _partials/readme-use-cases.md}}

{{> _partials/readme-inline-tail.md}}

{{> _partials/readme-footer.md}}
