#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///

from long_running.drive import main, run_installed

if __name__ == "__main__":
    run_installed(__file__)
    raise SystemExit(main())
