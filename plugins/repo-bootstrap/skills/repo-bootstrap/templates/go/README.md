# ![{{PROJECT_NAME}}](docs/assets/readme-banner.webp)

{{> _partials/readme-opener.md}}

{{#FEATURE_RELEASE}}
[![Release](https://img.shields.io/github/v/release/{{GITHUB_USER}}/{{PROJECT_NAME}}?sort=semver)]({{REPO_URL}}/releases)
{{/FEATURE_RELEASE}}
[![CI](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/ci.yml?branch=main&label=ci)]({{REPO_URL}}/actions/workflows/ci.yml)
{{#HAS_LICENSE}}
[![License: {{LICENSE_ID}}](https://img.shields.io/badge/License-{{LICENSE_BADGE}}-blue.svg)]({{REPO_URL}}/blob/main/LICENSE)
{{/HAS_LICENSE}}

## Get started

{{#FEATURE_RELEASE}}
```bash
brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}
{{PROJECT_NAME}} hello
```

<details>
<summary>Without Homebrew</summary>

```bash
go install {{MODULE_PATH}}/cmd/{{PROJECT_NAME}}@latest
```

</details>
{{/FEATURE_RELEASE}}
{{^FEATURE_RELEASE}}
```bash
go install {{MODULE_PATH}}/cmd/{{PROJECT_NAME}}@latest
{{PROJECT_NAME}} hello
```

<details>
<summary>From a clone</summary>

```bash
git clone {{REPO_URL}}
cd {{PROJECT_NAME}}
task build   # -> ./bin/{{PROJECT_NAME}}
```

</details>
{{/FEATURE_RELEASE}}

<img src="docs/assets/demo.png" alt="TODO(bootstrap): Terminal running the command above, and its visible result" width="700">

{{> _partials/readme-demo-todo.md}}

Driving with an agent? Paste this:

```text
{{#FEATURE_RELEASE}}
Install {{PROJECT_NAME}} (`brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}`) and TODO(bootstrap): the first concrete goal to hand an agent.
{{/FEATURE_RELEASE}}
{{^FEATURE_RELEASE}}
Install {{PROJECT_NAME}} (`go install {{MODULE_PATH}}/cmd/{{PROJECT_NAME}}@latest`) and TODO(bootstrap): the first concrete goal to hand an agent.
{{/FEATURE_RELEASE}}
```

{{> _partials/readme-use-cases.md}}

{{> _partials/readme-inline-tail.md}}

{{> _partials/readme-footer.md}}
