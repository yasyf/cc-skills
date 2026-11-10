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


def main(hook: str) -> None:
    plugin = root()
    script = plugin / "hooks" / hook
    if not script.is_file():
        return
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1])
