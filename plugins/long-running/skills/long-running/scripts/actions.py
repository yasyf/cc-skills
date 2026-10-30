#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
import sys

from long_running.actions import main

if __name__ == "__main__":
    sys.exit(main())
