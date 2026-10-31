#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///

import sys
from long_running.board_rows import main

if __name__ == "__main__":
    from long_running import drive

    drive.run_installed(__file__)
    sys.exit(main(sys.argv[1:]))
