# The gt stack

Graphite is local-refs-first: `gt` reads local branches and the local trunk, records parent edges in its own metadata, and force-pushes what it derives. Every hazard here is a place where that model and reality diverge silently — the command exits 0 and the damage surfaces later, on someone else's branch or in a PR carrying the wrong commits.

## One stack, never siblings

Related PRs from one body of work go in one stack, chained, never as independent siblings off trunk — even when the diffs are provably disjoint. "The files don't overlap" is not a reason to split: siblings lose review order and the mid-stack restack machinery. Parallel PRs that touch the same files go in one stack because the merge queue drops the second sibling for conflicts. Chain order comes from what consumes what, and a lane still in flight goes at the tip so finished lanes don't get re-rebased under it. Cut each branch with `ccx vcs stack new <name> [--parent <branch>]`, which gives it its own working copy and gt-tracks it at birth, then submit with `ccx vcs stack submit`.

## ccx owns stack mutations

On the gt lane, the mutation path is `ccx vcs stack new <name>`, `ccx vcs ship`, `ccx vcs stack submit`, and `ccx vcs stack drop <branch>` or `ccx vcs stack drop --repair`. Ship commits through gt, replays a `needs_restack` downstack itself, pushes every downstack branch atomically, and submits each through Graphite's API. Stack submit fetches trunk and replays every lane before pushing and submitting. Stack drop removes a landed or abandoned branch from the middle, retargeting children first; `--repair` reopens and retargets PRs whose base ref is gone.

On tracked branches, raw `git push --force-with-lease`, `gh pr create`, `gh pr edit --base`, REST base edits, and hand-run `gt restack`, `gt sync`, or `gt submit` are banned. Raw pushes and PR/base edits leave Graphite's server unaware of the stack; deleting a landed parent's branch can then close its children. Hand-run restacks read the local trunk and can drag the stack onto a stale one. `ccx vcs stack restack` runs a repo-wide `gt sync`, which times out in big repos; use `ccx vcs stack submit`.

The only hand-run gt mutation allowed is `gt track --force --parent <branch>` to repair metadata. When any ccx verb refuses, hand the refusal to the stack owner verbatim; nobody works around it by hand. Reading state raw (`gt log`, `gh pr view`, `git log`) is always fine.

## What ccx submits

`ccx vcs ship` submits the whole downstack; `ccx vcs stack submit` replays and submits every lane bottom-up. A submit can force-push another lane's branch, move its SHA under active work, and retrigger its CI. If a stack holds another lane's branch, only the stack owner submits it, with `ccx vcs stack submit`. Report every branch moved and verify by content: file count, insertions, and the commit's new parent.

`--draft` sets draft state on whatever the submit touches. Submitting a new tip with `--draft` therefore silently converts published, reviewed PRs downstack back into drafts: reviewers drop off, merge-queue eligibility is lost, nothing warns. Never pass `--draft` over a stack with a published PR anywhere downstack; this skill opens PRs ready with a body, so the flag has no place here anyway. After `ccx vcs ship` or `ccx vcs stack submit`, verify `isDraft` on every touched PR rather than trusting the flags.

## A stale local trunk

gt reads local refs throughout, so a local trunk behind `origin/<trunk>` poisons results silently:

- **Titles.** `gt submit` titles a new PR from the oldest commit in `<local-trunk>..HEAD`; a stale trunk puts already-merged commits in that range and the PR opens under an unrelated title, while the diff itself is right (GitHub computes it against the remote base).
- **Commit counts.** `gt track` computes each branch's range against the local trunk: a one-commit branch reports as several, and `gt submit` then opens a PR containing trunk commits.
- **False all-clear.** `gt restack` exits 0 with "does not need to be restacked" against a stale trunk, leaving the branch behind the real one.
- **Submit abort.** "trunk branch is out of date and could not be updated" means the trunk is also checked out in another worktree, so git refuses to move the ref.

The fix is `ccx vcs stack submit`: it fetches origin trunk once, fast-forwards the local trunk with `update-ref` even when another worktree holds it, and realigns that working copy with its uncommitted work carried. It then replays every lane bottom-up, pushes atomically under leases, and submits each branch through Graphite. It refuses a local trunk holding commits the remote lacks, or a branch whose recorded base reaches back over trunk commits. Pass that refusal to the stack owner verbatim. A stale or pinned trunk is never a reason to rebase or push by hand.

## Why hand-run restacks go wrong

Hand-run restacks are banned on tracked branches; `ccx vcs stack submit` replaces them, and the facts below explain what goes wrong when one runs anyway.

Bare `gt restack` walks the whole repo graph, not just your stack: in a repo with many branches it can hit an unrelated `needs_restack` branch and abort before reaching yours, doing nothing while looking like it ran.

`gt modify` restacks the whole tracked chain above the amended branch on every invocation, no opt-out — including re-parenting the stack bottom onto trunk, so a long editing session quietly follows a moving trunk and invalidates SHA-pinned check results. Re-read branch SHAs after any `gt modify`; comparing `git rev-parse "<sha>:<path>"` tree hashes tells real change from pure rebase. `gt abort` fails outside interactive mode; use `git rebase --abort`, then verify every branch ref still points where expected.

## Worktrees split the stack silently

`gt modify` and `gt restack` skip any branch checked out in another worktree, printing one "Did not restack branch X because it is checked out in worktree ..." line amid dozens of "does not need to be restacked", then record the restack as done. `gt state` reports `needs_restack: false` for the whole chain, while the amend stopped below the pinned branch and the tip is missing your change. Verify propagation by content (`git show <tip>:<file> | grep <the thing you added>`), never by exit code or `needs_restack`. Use `ccx vcs stack submit` for repair and prevention: it replays every lane, including branches held in other working copies. `ccx vcs stack list` names the working copy holding each branch.

## Staging and messages

- `gt modify -a` and `gt create -a` stage untracked files too, not just modified tracked ones — in a reused worktree, previously-ignored build output resurfaces as untracked and gets swept into the commit by the thousand. `git add <path>` explicitly, run `gt modify -c` with no `-a`, and check `git diff --cached --name-only | wc -l` before committing. `git add -A` fails the same way.
- A `-m` argument with embedded newlines makes `gt modify` and `gt create` silently no-op: exit 0, message echoed back, commit and staged content unchanged. Write the message to a file, `git commit --amend -F <file>`, then a scoped restack if not at the tip. Verify with `git log -1 --pretty=%B`, never the exit status.
- `gt modify -m "<subject>"` with a single `-m` replaces the whole message — a multi-paragraph body is gone — and it amends the branch tip, not necessarily the commit you were thinking of.

## Fixups and autosquash

Landing `--fixup` commits with `git rebase -i --autosquash <trunk>` on a mid-stack branch rebases onto trunk directly, replaying every ancestor as a new SHA: the content is identical, but the branch no longer shares its parent's commits, so `git merge-base --is-ancestor <parent> <branch>` goes false and `gt restack` fails with "Cannot perform this operation on diverged branch". Recover without redoing work — only the SHAs diverged:

```bash
git rebase --onto <parent-branch> <old-base-sha> <branch>   # per branch, bottom-up
gt track --parent <parent-branch>
```

`<old-base-sha>` is the branch's stale copy of its parent's tip, readable from `git log --oneline <trunk>..<branch>`. Avoid the whole class by rebasing onto the parent, never trunk, or by amending and letting a scoped restack propagate.

When `git config rebase.autosquash` reports `true`, a plain `git rebase -i` folds and reorders `fixup!` commits even without `--autosquash` — and the reorder conflicts when a fixup was committed against the final tree but targets an earlier commit, a mysterious conflict in a rebase that "changed nothing". Pass `--no-autosquash` on reword-only passes, and prove the todo first: `GIT_SEQUENCE_EDITOR="cat" git rebase -i <base>`.

## When plain git and gt disagree

`gt track --parent <branch>` records the parent edge; it moves nothing. A branch cut from an old base stays there after tracking, and `gt log short` marks it `(needs restack)`. Use `gt track --force --parent <parent>` only to repair metadata, then `ccx vcs stack submit` to replay the stack. A refusal such as "would replay N but owns M" goes to the stack owner verbatim for that metadata repair.

A plain `git rebase` on a gt-tracked branch can leave Graphite's recorded parent revision stale or silently untrack it. Once untracked, every later gt command fails with "Cannot perform this operation on untracked branch", preceded by a warning naming *other* diverged branches, so the error reads as unrelated noise. Repair metadata with `gt track --force --parent <parent>`, then run `ccx vcs stack submit`. Also, `gt create` names the branch from the commit subject, so a branch pre-created with `git worktree add -b` is left behind as an empty stub at the old base; `ccx vcs stack new` tracks the intended branch at birth.

When someone rewrites the parent branch under yours, `gt track --parent <parent>` hard-fails ("<parent> is not in the history of <branch>") and every gt command refuses on the untracked branch. Hand the refusal to the stack owner verbatim. A throwaway branch at the old base or tracking against trunk attributes the rewritten stack's commits to you; neither is a repair.

A refusal of "fetched with Git and then tracked with Graphite" can mean the local content is wrong, not just a stale lease: after ref repairs, gt can't prove origin's heads are its own pushes. Treat it as a content question: capture origin's heads with `git ls-remote` and work out which chain each side holds. "Local is newer" is not evidence local is right; the patch-id test in [merge-state.md](merge-state.md) settles it. Pass the refusal and that evidence to the stack owner; don't force a push around it.

## Sequential lanes need a commit boundary

When stacked PRs build on each other and their lanes run sequentially in one worktree, each lane must commit on its own branch before the next starts. "Nobody commits, the orchestrator ships" destroys the boundary: the second lane's edits interleave with the first's in the same files, and no after-the-fact hunk-level split is fast or trustworthy. Stage N runs plain `git commit` on its branch; stage N+1 starts with `git checkout -b <next>`; the orchestrator still owns `ccx vcs stack submit` and the bodies. Genuinely independent lanes get separate worktrees instead.
