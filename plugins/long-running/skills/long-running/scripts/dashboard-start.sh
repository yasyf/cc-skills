#!/bin/sh
set -eu
config=$1
log=${config%.json}.log
nohup python3 "$(dirname "$0")/../../../dashboard/app/server.py" --config "$config" >>"$log" 2>&1 &
echo "drive dashboard pid $! log $log"
