#!/bin/sh
# Wrapper over `lr wait`, kept so lanes that still call desk-wait.sh keep working. Sessions
# started on a 0.7 plugin run their own bin shim, which hops to the newest installed
# version only if it has bin/desk-wait.sh and skills/long-running/scripts/desk-wait.sh.
case "${1:-}" in -h | --help) echo "desk-wait.sh forwards to: lr wait SECONDS (SOURCE... [-- COMMAND...] | -- COMMAND...)" ;; esac
exec lr wait "$@"
