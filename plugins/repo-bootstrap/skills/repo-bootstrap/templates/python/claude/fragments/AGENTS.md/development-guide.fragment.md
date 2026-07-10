# {{PROJECT_NAME}} Development Guide

{{#FEATURE_PYPI}}{{DESCRIPTION}} Published to PyPI as `{{DIST_NAME}}`; the CLI is `{{DIST_NAME}}`, run as `uvx {{DIST_NAME}}`.{{/FEATURE_PYPI}}{{^FEATURE_PYPI}}{{DESCRIPTION}} The CLI is `{{DIST_NAME}}`, run with `uv run {{DIST_NAME}}`.{{/FEATURE_PYPI}}

## Repository Structure

```
{{PROJECT_NAME}}/
├── {{PACKAGE}}/      # The package — TODO(bootstrap): name the key modules
├── tests/            # Pytest suite
├── .github/          # GitHub Actions workflows
├── AGENTS.md         # This file — shared conventions
└── README.md         # Project overview
```
