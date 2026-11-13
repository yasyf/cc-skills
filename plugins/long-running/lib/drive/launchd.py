import json
import os
import plistlib
import subprocess
from pathlib import Path

from drive.state import DriveError

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
MANIFEST = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
AGENT_ENV = ("PATH", "CLAUDE_CONFIG_DIR", "XDG_STATE_HOME", "XDG_BIN_HOME", "CC_SLACK_CONFIG", "CHROME")
LAUNCHED_ENV = "CLAUDE_LONG_RUNNING_LAUNCH_AGENT"
LABEL = "com.yasyf.long-running"
DOMAIN = f"gui/{os.getuid()}"
RESTART_SECONDS = 10


def service(label: str) -> str:
    """The launch agent as launchctl names it in this user's login session."""
    return f"{DOMAIN}/{label}"


def launchctl(*args: str) -> None:
    done = subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)
    if done.returncode:
        raise DriveError(f"launchctl {' '.join(args)} exited {done.returncode}: {' '.join(done.stderr.split())}")


def kept(label: str) -> bool:
    return subprocess.run(["launchctl", "print", service(label)], capture_output=True, check=False).returncode == 0


def keep(label: str, args: list[str], log: Path, env: dict[str, str], cwd: Path) -> None:
    """Load the launch agent, which runs `drive <args>` in cwd now and again RESTART_SECONDS after it exits, however it exits, on the newest long-running installed, found the way the drive's MCP server finds it. Its environment holds only AGENT_ENV of this process's, since a plist keeps on disk the API keys the rest holds, then env and LAUNCHED_ENV naming the agent."""
    newest = json.loads(MANIFEST.read_text())["mcpServers"]["drive"]
    path = LAUNCH_AGENTS / f"{label}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": ["/usr/bin/env", newest["command"], *newest["args"][:-1], *args],
                "EnvironmentVariables": {variable: os.environ[variable] for variable in AGENT_ENV if variable in os.environ} | env | {LAUNCHED_ENV: label},
                "WorkingDirectory": str(cwd),
                "KeepAlive": True,
                "RunAtLoad": True,
                "ThrottleInterval": RESTART_SECONDS,
                "StandardOutPath": str(log),
                "StandardErrorPath": str(log),
            }
        )
    )
    launchctl("bootstrap", DOMAIN, str(path))
