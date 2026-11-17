"""The one shape every session the lane drive starts runs on, owner, builder, stager, auditor or acker, so none loads more than its role needs: the old drive's light settings (no auto-memory, the headless switches, the user's skills off, the skill listing cut, only the plugins a code session needs, the lane rules in place of his whole CLAUDE.md, the Orca lane's env with its compact window and no auto-update), `--strict-mcp-config` with the Datadog servers alone, Opus as the advisor of an owner, builder or stager and none for the rest, and the model and effort of the drive config's `models` table, never max or xhigh, by his Nov 15 words "make them as lightweight as possible" and his 6:40 PM order to apply every cost, model, effort, params and env var ruling of the old drive here. The lane's own hooks, env and denials go on top."""

import json
from pathlib import Path

ROLES = ("owner", "builder", "stager", "auditor", "acker")
MODELS = {"owner": ("opus", "high"), "builder": ("opus", "high"), "stager": ("opus", "high"), "auditor": ("sonnet", "high"), "acker": ("haiku", None)}
ADVISED = ("owner", "builder", "stager")
ADVISOR = "opus"
CEILING = ("xhigh", "max")
CEILED = "high"


def model(config: dict, role: str) -> tuple[str, str | None]:
    """The role's model and effort, from the drive config's `models` table over MODELS, an effort above CEILED brought down to it."""
    picked, effort = (config.get("models") or {}).get(role) or MODELS[role]
    return picked, CEILED if effort in CEILING else effort


def merged(light: dict, own: dict) -> dict:
    """The lane's own settings over light's: env, denials, plugins and hooks joined, every other key the lane's."""
    joined = light | own
    joined["env"] = light.get("env", {}) | own.get("env", {})
    joined["enabledPlugins"] = light.get("enabledPlugins", {}) | own.get("enabledPlugins", {})
    joined["permissions"] = {"deny": list(dict.fromkeys([*light.get("permissions", {}).get("deny", []), *own.get("permissions", {}).get("deny", [])]))}
    if "hooks" in light or "hooks" in own:
        joined["hooks"] = {event: [*light.get("hooks", {}).get(event, []), *own.get("hooks", {}).get(event, [])] for event in {*light.get("hooks", {}), *own.get("hooks", {})}}
    return joined


def settings(role: str, checkout: Path, own: dict) -> dict:
    from drive import agents

    advised = role in ADVISED
    base = agents.session_settings(own.get("env", {}) | ({} if advised else agents.ADVISOR_OFF))
    return merged(agents.light(base, agents.WATCHDOG if role == "auditor" else agents.BUILDER, checkout), own)


def flags(config: dict, role: str) -> list[str]:
    """The flags that go before any other, as the lane rules' file flag must: the lane rules, strict MCP with the Datadog servers, the model, the effort and the advisor."""
    from drive import agents

    picked, effort = model(config, role)
    return [*agents.LANE_RULES_ARGS, *agents.STRICT_MCP, "--mcp-config", json.dumps(agents.datadog_servers()), "--model", picked, *(["--effort", effort] if effort else []), *([agents.ADVISOR_FLAG, ADVISOR] if role in ADVISED else [])]


def argv(config: dict, role: str, checkout: Path, own: dict, head: list[str], tail: list[str]) -> list[str]:
    """`claude <head> <flags> --settings <light over own> <tail>`."""
    return ["claude", *head, *flags(config, role), "--settings", json.dumps(settings(role, checkout, own)), *tail]
