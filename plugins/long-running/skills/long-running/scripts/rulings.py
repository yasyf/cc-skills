#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///

import sys
from long_running.rulings import main
from long_running import drive

if __name__ == "__main__":
    drive.run_installed(__file__)
    raise SystemExit(main(stdin=sys.stdin, stdout=sys.stdout))
