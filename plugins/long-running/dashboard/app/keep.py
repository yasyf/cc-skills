import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    try:
        urllib.request.urlopen(f"http://{config['host']}:{config['port']}/api/model", timeout=15).read()
        return
    except OSError as error:
        down = f"{type(error).__name__}: {error}"
    log = (args.config.parent / "server.log").open("a")
    subprocess.Popen([sys.executable, str(HERE / "server.py"), "--config", str(args.config)], cwd=args.config.parent, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    subprocess.run(["cci", "post", "--drive", config["drive"], "--lane", config["keeper_lane"], "--to", "root", "--kind", "defect", "--text", f"drive dashboard was down ({down[:200]}); the keeper restarted it"], capture_output=True)
    print("restarted")


if __name__ == "__main__":
    main()
