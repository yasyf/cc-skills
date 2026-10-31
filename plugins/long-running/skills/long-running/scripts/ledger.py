#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///

from long_running.ledger import main

if __name__ == "__main__":
    from long_running import drive

    drive.run_installed(__file__)
    raise SystemExit(main())
