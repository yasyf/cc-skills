#!/bin/zsh
until [ "$(bk api "/pipelines/test/builds/$1" | jq -r .state)" = passed ]; do
  sleep 5
done
