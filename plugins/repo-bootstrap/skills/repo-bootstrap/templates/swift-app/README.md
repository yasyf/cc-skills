# ![{{PROJECT_NAME}}](docs/assets/readme-banner.webp)

{{> _partials/readme-opener.md}}

[![CI](https://img.shields.io/github/actions/workflow/status/{{GITHUB_USER}}/{{PROJECT_NAME}}/ci.yml?branch=main&label=ci)]({{REPO_URL}}/actions/workflows/ci.yml)
{{#HAS_LICENSE}}
[![License: {{LICENSE_ID}}](https://img.shields.io/badge/License-{{LICENSE_BADGE}}-blue.svg)]({{REPO_URL}}/blob/main/LICENSE)
{{/HAS_LICENSE}}

## Get started

Requires Xcode 26+ and an iOS {{IOS_DEPLOYMENT_TARGET}}+ simulator or device.

```bash
open {{PROJECT_NAME}}.xcodeproj
```

Press Run.

<details>
<summary>Build and test from the command line</summary>

```bash
xcodebuild build -project {{PROJECT_NAME}}.xcodeproj -scheme {{PROJECT_NAME}} \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO
xcodebuild test -project {{PROJECT_NAME}}.xcodeproj -scheme {{PROJECT_NAME}} \
  -destination 'platform=iOS Simulator,name=iPhone 17'
```

</details>

<img src="docs/assets/demo.png" alt="TODO(bootstrap): the app doing its one thing" width="300">

TODO(bootstrap): demo media — a real screenshot of the running app doing its one
thing (simulator or device; no mockups), committed at `docs/assets/demo.png` and
under 1 MiB. A short capture works when the interaction is the point.

Driving with an agent? Paste this:

```text
Open {{PROJECT_NAME}}.xcodeproj, build the {{PROJECT_NAME}} scheme for the iOS Simulator, and TODO(bootstrap): the first concrete goal to hand an agent.
```

{{> _partials/readme-use-cases.md}}

{{> _partials/readme-inline-tail.md}}

{{> _partials/readme-footer.md}}
