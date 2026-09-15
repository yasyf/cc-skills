# Merge state

Graphite's merge queue squash-merges: the squashed commit lands on trunk and the PR closes without GitHub ever recording a merge. The fields everyone reads first — `state=CLOSED`, `mergedAt=null`, `mergeCommit=null` — are identical for a landed PR and an abandoned one, so reading state alone reports a successful merge as an abandonment. In one measured repo, 58 of the last 69 closed PRs had actually merged through the queue. Everything below reduces to one rule: confirm by content on trunk, never by PR state.

## Merged or abandoned

1. `state == MERGED` — merged, done.
2. Otherwise read the close event's actor:

```bash
gh api graphql -f query='query { repository(owner: "<owner>", name: "<name>") {
  pullRequest(number: <n>) { state mergedAt
    timelineItems(last: 5, itemTypes: [CLOSED_EVENT]) {
      nodes { ... on ClosedEvent { actor { login } } } } } } }'
```

`graphite-app` closed it — merged through the queue. A human closed it — genuinely abandoned.

3. Confirm by content; the queue keeps the PR number in the squash subject:

```bash
git log --format='%H%x09%s' origin/<trunk> | grep -E '\(#<n>\)$'
```

Anchor to `$`: PR-number mentions in commit bodies and reverts false-positive without the suffix anchor. Key any merge watch on `state != "OPEN"` and then verify content; keying on `MERGED` waits forever.

The obvious git-side signals mislead too. `git cherry origin/<trunk> <branch>` marks every commit `+` unmerged — the squash's combined patch-id matches no original. `git merge-base --is-ancestor <tip> origin/<trunk>` answers no even after the squash lands; compare trees instead (`git diff --stat <tip> origin/<trunk> -- <subtree>`). `state` can even read `OPEN` on a PR that already landed. The signal that doesn't lie is the head ref: graphite-app deletes `refs/heads/<branch>` on landing while `refs/pull/<n>/head` survives at the last pushed SHA, so `gh api repos/<owner>/<name>/branches/<branch>` returning 404 plus a `graphite-app[bot]` close in the timeline means landed:

```bash
gh api repos/<owner>/<name>/issues/<n>/timeline --paginate \
  --jq '.[] | select(.event=="head_ref_deleted" or .event=="closed") | {actor: .actor.login, event, created_at}'
```

## The squash commit comes from the PR body

The queue builds the squashed commit message from the PR title and body, not the branch's commit message. Editing the PR after pushing is enough to change what lands — no amend, force-push, or CI rerun — and the reverse cuts too: a careful branch commit message never reaches trunk when the PR body says something else. Put the effort where the squash reads from.

## Queue rejections are rebase conflicts

The queue rebases the whole stack onto the trunk tip before merging. GitHub's `mergeable`/`mergeStateStatus` compute a three-way merge of head into base — and a stacked PR's base is the unmoved branch below it — so `MERGEABLE`/`CLEAN` says nothing about whether the queue can rebase. Two rejection messages, one cause:

- **"had merge conflicts"** — a rebase conflict against a trunk that moved, even while every PR in the stack reports `MERGEABLE`.
- **"removed due to downstack failures on PR #N"**, N being the stack's bottom — worded as a CI failure, but when the queue never created a speculative build, nothing failed: the stack didn't rebase onto the current trunk. The missing speculative build is the tell; a real CI failure has one to point at. Don't go check-hunting through neutral and skipped checks — they don't block.

The check that answers it, worth running before enqueuing any stack whose bottom is behind trunk:

```bash
BASE=$(git merge-base <bottom-branch> origin/<trunk>)
comm -12 <(git diff --name-only "$BASE" <tip-branch> | sort) \
         <(git diff --name-only "$BASE" origin/<trunk> | sort)
```

Empty output means the queue can rebase cleanly — also worth checking before re-pushing a rebased stack, since a trunk that moved without touching your files needs no second rebase. The queue's verdict lives in its merge-activity comment, not the checks UI:

```bash
gh api repos/<owner>/<name>/issues/<n>/comments \
  --jq '.[] | select(.body | contains("Merge activity")).body'
```

## Queue drops

The queue reports itself in one "Merge activity" comment it **edits in place**, appending a bullet per event, and DMs the author on Slack. Nothing else fires: no new comment, no check, no timeline event a watcher can key on. So poll that comment's `updated_at` and body, not the comment list — a watcher keyed on new comments sees a queue drop as silence. The comment is authored by whoever enqueued the PR, not by `graphite-app[bot]`, so an author filter on the queue bot matches nothing.

```bash
gh api "repos/<owner>/<name>/issues/<n>/comments" \
  --jq '.[] | select(.body | test("Merge activity")) | {id, updated_at, body}'
```

Observed verdict lines, each the last bullet of a drop: `couldn't merge this PR because **it had merge conflicts**`, `disabled "merge when ready" on this PR due to: a merge conflict with the target branch`, `This pull request can not be added to the ... queue. Please try rebasing and resubmitting`. A drop takes the merge label off with it, so the fix is: push the rebase or the fix first, then relabel. Relabelling first re-enqueues the rejected head, and relabelling without a push is a no-op — the queue drops it again for the same reason.

**Removing the label does not dequeue an in-flight PR.** Once the queue has taken it, the label is a record of how it got there, not a handle on it: Graphite landed one PR 50 minutes after its label came off, on a head amended in between (`#19776`, `#19850`). To actually hold a PR, close it or red a required check.

**Landing a parent deletes its head branch, which auto-closes the child.** GitHub closes a PR whose base ref disappears, and a closed PR whose branch was force-pushed after the close cannot be reopened — the work is recoverable only by recreating the base branch at trunk and opening a fresh PR (`#19700` lost that way, `#19707` recovered). Before landing a stack's bottom, repoint every child's base at trunk.

Queue-merged PRs read `CLOSED` with `mergedAt: null`, so the drop path and the success path look identical in the PR's own fields; resolve them by the close actor, as above.

## After a downstack squash-merge

A squash orphans the Graphite stack parent: the merged branch's content is on trunk under a new SHA, so children still point at commits gt can no longer place.

- **Restack can't find the parent.** Replay the child by hand — `git rebase --onto origin/<trunk> <old-parent-sha> <child>` — then reattach with `gt track --parent <trunk>`.
- **The phantom replay.** When `gt restack` does run, it replays the merged parent's commit under a new SHA. `git diff <trunk> --stat` reads exactly right — the replayed commit is a no-op against a trunk that already holds its content — and only `git log --oneline <trunk>..<branch>` shows the extra commit; the PR then ships carrying a re-do of merged work. Count commits after every restack; never trust the diff. Drop it with `git rebase --no-autosquash --onto <trunk> <replayed-sha> <branch>`, then `gt track --parent <trunk>`.
- **Stale local branches.** gt refuses to submit ("merged but the merged commits are not contained in the latest trunk"). Never `git branch -D` the merged locals — that orphans the rest of the chain from gt's tracking. `gt delete <merged-branch> --no-verify` reparents children onto the deleted branch's parent and physically restacks the chain in one pass.

Two repair regimes follow, and patch-id tells them apart. `gt submit` restacks server-side at push time, so remote branches may already sit on the new trunk while local ones sit on the old base — a blind `--force` then reverts every branch's base. Compare `git diff <parent> <branch> | git patch-id --stable` against the remote's own patch-id over its old base:

- **Identical** — the server restack moved the base and nothing else; remote is authoritative. Reset local refs to origin (`git fetch origin '+refs/heads/<prefix>/*:refs/remotes/origin/<prefix>/*'`, record `git for-each-ref 'refs/heads/<prefix>/*'` first, then `git update-ref refs/heads/<b> refs/remotes/origin/<b>` per branch), rebase only your branch onto the refreshed parent (`git rebase --onto <refreshed-parent> <old-parent-sha> <your-branch>`), re-run checks since the base moved, and gate on `gt submit --branch <yours> --dry-run` showing every other branch `(No-op)` and only yours `(Create)` — `--branch` still validates the whole downstack.
- **Differing** — local carries content remote lacks (a fresh restack, applied patches); local is authoritative and resetting to origin loses it. `git fetch origin <trunk>:<trunk>`, `gt delete` the merged branches, then `gt submit --stack --no-edit --update-only` force-pushes-with-lease over the server rebases without `--force` — dry-run first, expecting every branch `(Update code)` and zero creates.

Either way, verify: `git rev-list --count <trunk>..<tip>` drops by exactly the merged commit count.

## Stale graphite-base refs

Leftover `graphite-base/<n>` refs on origin pin Graphite's web UI to an old stack even after correct GitHub bases, correct `gt track` parents, and a clean submit — Graphite re-derives parentage from those synthetic refs. Record their SHAs, then `git push origin --delete graphite-base/<n>` per PR you own (the namespace is shared; don't touch others'), and re-submit. Recreate with `git push origin <sha>:refs/heads/graphite-base/<n>` if needed.

## A rebase that lands exactly on the trunk tip

`HEAD == <base>` after a rebase looks like a clean no-op; it means git dropped your commit as already applied upstream — the PR already merged — and pushing would force-empty a merged PR's branch. After every rebase, before pushing, `git rev-list --count <base>..HEAD` must be non-zero and `git rev-parse HEAD` must differ from `git rev-parse <base>`. gt catches this with "PR for the following branch has already been merged or closed" — read the refusal instead of reaching for `--force`.
