# {{PROJECT_NAME}} Development Guide

{{#FEATURE_RELEASE}}{{DESCRIPTION}} Distributed via Homebrew: `brew install {{GITHUB_USER}}/tap/{{PROJECT_NAME}}`.{{/FEATURE_RELEASE}}{{^FEATURE_RELEASE}}{{DESCRIPTION}}{{/FEATURE_RELEASE}}

Run with [bun](https://bun.sh): `bun start`. No build step — bun executes TypeScript directly.

## Repository Structure

```
{{PROJECT_NAME}}/
├── src/              # TypeScript source — entry point (index.ts) and modules
├── tests/            # bun test suite (bun:test)
├── .github/          # CI — typecheck + tests on bun
├── package.json      # scripts, dependencies, bun metadata
├── tsconfig.json     # strict TypeScript config (no emit — bun runs .ts directly)
├── AGENTS.md         # This file — shared conventions
└── README.md         # Project overview
```
