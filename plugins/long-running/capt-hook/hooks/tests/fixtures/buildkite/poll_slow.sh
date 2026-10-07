#!/bin/zsh
while true; do
  left=0
  for b in "$@"; do
    s=$(bk api "/pipelines/test/builds/$b" 2>/dev/null | jq -r .state)
    case $s in passed|failed|canceled) ;; *) left=1 ;; esac
  done
  [ $left = 0 ] && exit 0
  sleep 30
done
