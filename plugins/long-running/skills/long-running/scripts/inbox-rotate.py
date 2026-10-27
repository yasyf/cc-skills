#!/usr/bin/env python3
import sys

import drive
import inboxes

drive.run_installed(__file__)
sys.exit(inboxes.main(["rotate", *sys.argv[1:]]))
