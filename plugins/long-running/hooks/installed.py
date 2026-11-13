import fcntl
import importlib.util
import json
import os
import runpy
import subprocess
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


def environment() -> Path:
    """The drive's own virtualenv, beside the interpreter record, holding the packages the plugin's uv.lock pins, spawnllm among them."""
    return interpreter_record().with_name("venv").absolute()


def built(plugin: Path) -> bool:
    venv = environment()
    lock = venv / "uv.lock"
    return os.access(venv / "bin" / "python", os.X_OK) and lock.is_file() and lock.read_bytes() == (plugin / "uv.lock").read_bytes()


def synced(plugin: Path) -> str:
    """Build the drive's environment from this plugin's uv.lock with `uv sync` when the lock it was last built from differs or its interpreter is gone, one process at a time, and record its interpreter, which hooks/python.sh, the drive MCP server and the drive shim exec; its interpreter."""
    venv = environment()
    if not built(plugin):
        venv.parent.mkdir(parents=True, exist_ok=True)
        with venv.with_name("venv.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            if not built(plugin):
                done = subprocess.run(
                    ["uv", "sync", "--frozen", "--no-dev", "--no-install-project", "--quiet", "--project", str(plugin)],
                    env=os.environ | {"UV_PROJECT_ENVIRONMENT": str(venv)},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if done.returncode:
                    raise SystemExit(f"long-running: uv sync could not build the drive's environment in {venv}: {done.stderr.strip()}")
                (venv / "uv.lock").write_bytes((plugin / "uv.lock").read_bytes())
    python = str(venv / "bin" / "python")
    record_interpreter(python)
    return python


def record_interpreter(python: str) -> None:
    kept = interpreter_record()
    if kept.is_file() and kept.read_text().strip() == python:
        return
    kept.parent.mkdir(parents=True, exist_ok=True)
    fresh = kept.with_name(f".interpreter.{os.getpid()}")
    fresh.write_text(f"{python}\n")
    fresh.replace(kept)


def entered(plugin: Path, argv: list[str]) -> None:
    """Run argv again on the drive's environment when this interpreter cannot import spawnllm, building the environment first when it is missing or stale; an interpreter that imports it, such as a test run's or capt-hook's, runs on. The environment's own interpreter lacking it means a damaged environment, which is rebuilt in place once, with no exec."""
    if importlib.util.find_spec("spawnllm") is not None:
        return
    venv = environment()
    if Path(sys.prefix).resolve() != venv.resolve():
        python = synced(plugin)
        os.execv(python, [python, "-I", *argv])
    (venv / "uv.lock").unlink(missing_ok=True)
    synced(plugin)
    importlib.invalidate_caches()
    if importlib.util.find_spec("spawnllm") is None:
        raise SystemExit(f"long-running: the drive's environment in {venv} still cannot import spawnllm after uv sync rebuilt it")


def main(hook: str) -> None:
    plugin = root()
    if hook == "session_start.py":
        synced(plugin)
    entered(plugin, [__file__, hook])
    script = plugin / "hooks" / hook
    if not registered(plugin, hook):
        return
    os.environ["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1])
