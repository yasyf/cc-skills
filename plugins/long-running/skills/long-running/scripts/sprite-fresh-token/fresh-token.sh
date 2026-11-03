#!/bin/sh
tool=$1
shift
PATH=$SPRITE_TOOLS_PATH
token=$(printf "protocol=https\nhost=github.com\nusername=x-access-token\n\n" | git credential-cache get | sed -n "s/^password=//p")
[ -z "$token" ] || export GH_TOKEN="$token"
exec "$tool" "$@"
