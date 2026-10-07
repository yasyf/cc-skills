from __future__ import annotations

import os
import re
from collections.abc import Mapping

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)
MIN_VALUE = 8
PREFIXED = re.compile(
    r"(?<![\w-])(?:sk-[A-Za-z0-9_-]{16,}|xox[abpr]-[A-Za-z0-9-]{10,}|gh[op]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})(?![\w-])"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"
)


def env_values(env: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in env.items() if SECRET_NAME.search(name) and len(value) >= MIN_VALUE}


def hits(text: str, env: Mapping[str, str] | None = None) -> list[str]:
    found = [name for name, value in env_values(os.environ if env is None else env).items() if value in text]
    if PREFIXED.search(text):
        found.append("token-shaped")
    return found


def redacted(text: str, env: Mapping[str, str] | None = None) -> str:
    for name, value in env_values(os.environ if env is None else env).items():
        text = text.replace(value, f"<{name}>")
    return PREFIXED.sub("<redacted>", text)
