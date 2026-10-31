#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
import sys

from long_running import drive
import inboxes

drive.run_installed(__file__)
sys.exit(inboxes.main(["rotate", *sys.argv[1:]]))
