#!/usr/bin/env python3
import sys

import drive
import inboxes

drive.refuse_stale(__file__)
sys.exit(inboxes.main(["digest", *sys.argv[1:]]))
