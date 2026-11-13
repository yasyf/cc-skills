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


def interpreter_record() -> Path:
    """Where hooks/python.sh reads the interpreter it execs: beside the drives' saved state, where the drive's `state.root` puts it."""
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        return Path(explicit) / "interpreter"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "long-running" / "interpreter"


def record_interpreter() -> None:
    """Record the interpreter running this hook, the one `python3` resolved to, so hooks/python.sh execs it directly from the next hook on, with no version manager's shim resolving it again."""
    kept = interpreter_record()
    if kept.is_file() and kept.read_text().strip() == sys.executable:
        return
    kept.parent.mkdir(parents=True, exist_ok=True)
    fresh = kept.with_name(f".interpreter.{os.getpid()}")
    fresh.write_text(f"{sys.executable}\n")
    fresh.replace(kept)


def interpreter() -> str:
    """The interpreter hooks/python.sh execs: the one record_interpreter kept while it still runs, else `python3` on PATH."""
    kept = interpreter_record()
    recorded = kept.read_text().strip() if kept.is_file() else ""
    return recorded if recorded and os.access(recorded, os.X_OK) else "python3"


def main(hook: str) -> None:
    record_interpreter()
    plugin = root()
    script = plugin / "hooks" / hook
    if not registered(plugin, hook):
        return
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1])
