#!/bin/sh
exec python3 "$(dirname "$0")/inboxes.py" wait "$@"
