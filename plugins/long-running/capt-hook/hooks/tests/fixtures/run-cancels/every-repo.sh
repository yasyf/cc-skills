#!/bin/zsh
while read -r r; do
  gh api "repos/yasyf/$r/actions/runs?status=queued&per_page=100" --jq '.workflow_runs[] | select(.event=="pull_request") | .id' |
  while read -r id; do
    gh api -X POST "repos/yasyf/$r/actions/runs/$id/cancel"
  done
done < repos.txt
