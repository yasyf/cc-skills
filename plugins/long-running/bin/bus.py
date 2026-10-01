#!/bin/sh
exec python3 "$(dirname "$0")/../skills/long-running/scripts/bus.py" "$@"
