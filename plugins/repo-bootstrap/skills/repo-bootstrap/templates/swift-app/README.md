# {{PROJECT_NAME}}

![{{PROJECT_NAME}} banner](docs/assets/readme-banner.webp)

[![CI](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/ci.yml?branch=main&label=ci)]({{REPO_URL}}/actions/workflows/ci.yml)
{{#HAS_LICENSE}}
[![License: {{LICENSE_ID}}](https://img.shields.io/badge/License-{{LICENSE_BADGE}}-blue.svg)]({{REPO_URL}}/blob/main/LICENSE)
{{/HAS_LICENSE}}

{{DESCRIPTION}}

TODO(bootstrap): expand the one-line description into a two-sentence pitch — what
it is, and the one property that makes it worth using.

## Requirements

- Xcode 26+ (Swift 6 toolchain)
- iOS {{IOS_DEPLOYMENT_TARGET}}+ device or simulator

## Quickstart

Open the project and press Run:

```bash
open {{PROJECT_NAME}}.xcodeproj
```

Or build and test from the command line:

```bash
xcodebuild build -project {{PROJECT_NAME}}.xcodeproj -scheme {{PROJECT_NAME}} \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO
xcodebuild test -project {{PROJECT_NAME}}.xcodeproj -scheme {{PROJECT_NAME}} \
  -destination 'platform=iOS Simulator,name=iPhone 17'
```

TODO(bootstrap): show the app doing its one thing — a screenshot or a short capture.

## What problems does this solve?

TODO(bootstrap): 3-4 bullets, each naming a concrete pain and how this addresses it.
