import json
import os
import runpy
import sys
from pathlib import Path


def root() -> Path:
    """The newest installed long-running, so a live session runs the hooks installed now, never the ones it loaded at start; a checkout runs its own."""
    here = Path(__file__).resolve().parent.parent
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    if not here.is_relative_to((config / "plugins" / "cache").resolve()):
        return here
    plugins = json.loads((config / "plugins" / "installed_plugins.json").read_text())["plugins"]
    installs = [entry for key, entries in plugins.items() if key.partition("@")[0] == "long-running" for entry in entries]
    return Path(max(installs, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")["installPath"])


def registered(plugin: Path, hook: str) -> bool:
    """Whether the installed version's hooks.json still runs the hook, so a session that loaded an older list runs only the hooks the installed version does."""
    events = json.loads((plugin / "hooks" / "hooks.json").read_text())["hooks"].values()
    return any(command["command"].endswith(f" {hook}") for groups in events for group in groups for command in group["hooks"])


def main(hook: str) -> None:
    plugin = root()
    script = plugin / "hooks" / hook
    if not registered(plugin, hook):
        return
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1])
