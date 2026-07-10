# {{PROJECT_NAME}} Development Guide

{{#FEATURE_RELEASE}}{{DESCRIPTION}} Distributed via Homebrew: `brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}`.{{/FEATURE_RELEASE}}{{^FEATURE_RELEASE}}{{DESCRIPTION}}{{/FEATURE_RELEASE}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── Package.swift               # SPM manifest — targets, products, dependencies
├── Sources/
│   ├── {{MODULE_NAME}}/        # the library — all logic lives here
│   └── {{PROJECT_NAME}}/       # the executable — a thin ArgumentParser shell
├── Tests/{{MODULE_NAME}}Tests/ # Swift Testing (@Test / #expect) against the library
├── .github/                    # GitHub Actions workflows
├── AGENTS.md                   # This file — shared conventions
└── README.md                   # Project overview
```
