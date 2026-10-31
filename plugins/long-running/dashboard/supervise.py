import argparse
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CARRIED = ("PATH", "HOME", "LANG", "CEREBRAS_API_KEY")
CCI_SHIM = Path(".claude/plugins/marketplaces/cc-inbox/plugin/bin")


def label(config):
    return f"com.yasyf.long-running.dashboard.{config['drive']}"


def job(config_path, config, env):
    log = str(config_path.parent / "server.log")
    return {
        "Label": label(config),
        "ProgramArguments": [sys.executable, str(HERE / "app" / "server.py"), "--config", str(config_path)],
        "WorkingDirectory": str(config_path.parent),
        "EnvironmentVariables": {name: env[name] for name in CARRIED if name in env} | {"PATH": f"{env['PATH']}:{Path(env['HOME']) / CCI_SHIM}"},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
    }


def main():
    parser = argparse.ArgumentParser(description="Run the drive dashboard under launchd so it restarts whenever it exits.")
    parser.add_argument("--config", required=True, type=Path)
    config_path = parser.parse_args().config.resolve()
    config = json.loads(config_path.read_text())
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label(config)}.plist"
    with os.fdopen(os.open(plist, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
        plistlib.dump(job(config_path, config, os.environ), out)
    service = f"gui/{os.getuid()}/{label(config)}"
    subprocess.run(["launchctl", "bootout", service], capture_output=True)
    while subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
        time.sleep(0.5)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], check=True)
    print(service)


if __name__ == "__main__":
    main()
