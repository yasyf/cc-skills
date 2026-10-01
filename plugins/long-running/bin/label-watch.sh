#!/bin/sh
exec "$(dirname "$0")/../skills/long-running/scripts/label-watch.sh" "$@"
