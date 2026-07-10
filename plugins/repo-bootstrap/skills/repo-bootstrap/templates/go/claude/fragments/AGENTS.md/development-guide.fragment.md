# {{PROJECT_NAME}} Development Guide

{{#FEATURE_RELEASE}}{{DESCRIPTION}} Distributed via Homebrew: `brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}`.{{/FEATURE_RELEASE}}{{^FEATURE_RELEASE}}{{DESCRIPTION}}{{/FEATURE_RELEASE}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── cmd/{{PROJECT_NAME}}/   # main package — the CLI entry point
├── internal/
│   ├── cli/               # cobra command tree — TODO(bootstrap): name the commands
│   ├── version/           # build version, stamped via -ldflags
│   └── log/               # slog setup
├── .github/               # GitHub Actions workflows
├── AGENTS.md              # This file — shared conventions
└── README.md              # Project overview
```
