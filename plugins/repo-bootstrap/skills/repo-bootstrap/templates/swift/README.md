# {{PROJECT_NAME}}

![{{PROJECT_NAME}} banner](docs/assets/readme-banner.webp)

{{#FEATURE_RELEASE}}
[![Release](https://img.shields.io/github/v/release/{{GITHUB_USER}}/{{PROJECT_NAME}}?sort=semver)]({{REPO_URL}}/releases)
{{/FEATURE_RELEASE}}
[![CI](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/ci.yml?branch=main&label=ci)]({{REPO_URL}}/actions/workflows/ci.yml)
{{#HAS_LICENSE}}
[![License: {{LICENSE_ID}}](https://img.shields.io/badge/License-{{LICENSE_BADGE}}-blue.svg)]({{REPO_URL}}/blob/main/LICENSE)
{{/HAS_LICENSE}}

{{DESCRIPTION}}

TODO(bootstrap): expand the one-line description into a two-sentence pitch — what
it is, and the one property that makes it worth using.

## Install

{{#FEATURE_RELEASE}}
Homebrew (macOS):

```bash
brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}
```

Or clone and build with the Swift toolchain:

```bash
git clone {{REPO_URL}}
cd {{PROJECT_NAME}}
swift build -c release   # -> .build/release/{{PROJECT_NAME}}
```
{{/FEATURE_RELEASE}}
{{^FEATURE_RELEASE}}
Clone and build with the Swift toolchain:

```bash
git clone {{REPO_URL}}
cd {{PROJECT_NAME}}
swift build -c release   # -> .build/release/{{PROJECT_NAME}}
```

Or run straight from the checkout:

```bash
swift run {{PROJECT_NAME}} hello
```
{{/FEATURE_RELEASE}}

## Quickstart

TODO(bootstrap): a complete, working example a reader can run in under 30 seconds,
with the expected output shown.

```bash
{{PROJECT_NAME}} hello
```

## What problems does this solve?

TODO(bootstrap): 3-4 bullets, each naming a concrete pain and how this addresses it.
