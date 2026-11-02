#!/bin/zsh
for id in $(gh run list -R yasyf/cc-skills --status queued --event pull_request --json databaseId --jq '.[].databaseId'); do
  gh run cancel -R yasyf/cc-skills $id
done
