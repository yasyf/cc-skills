# The gt stack

Graphite is local-refs-first: `gt` reads local branches and the local trunk, records parent edges in its own metadata, and force-pushes what it derives. Every hazard here is a place where that model and reality diverge silently — the command exits 0 and the damage surfaces later, on someone else's branch or in a PR carrying the wrong commits.

## One stack, never siblings

Related PRs from one body of work go in one stack, chained, never as independent siblings off trunk — even when the diffs are provably disjoint. "The files don't overlap" is not a reason to split: siblings lose review order and the mid-stack restack machinery. Chain order comes from what consumes what, and a lane still in flight goes at the tip so finished lanes don't get re-rebased under it. `gt track --parent <branch>` per branch, then submit.

## gt owns stack mutations

On the gt lane, cut, amend, restack, and push through gt — a raw `git push --force-with-lease` or `gh pr create` lets remote heads drift from gt's tracked line. The drift surfaces later as stale heads and CONFLICTING stacked PRs, not as an error at push time. Reading state raw (`gh pr view`, `gh pr checks`) is always fine; the rule is about mutations. One escape hatch: when `gt submit` hangs for tens of minutes on an already-created PR whose base is correct, a plain `git push --force-with-lease origin <branch>` unblocks it without touching gt tracking. Then verify the remote head with `git ls-remote origin <branch>`, because gt's exit code lies about what it pushed.

## What `gt submit --stack` touches

`--stack` restacks and force-pushes every branch above the one you submit, rewriting a sibling lane's SHA onto your amended parent and retriggering their CI. That's gt behaving correctly for a stack, not a clobber — but their branch moves under them mid-work. In a stack with lanes you don't own, use `gt submit --no-stack` (current branch plus ancestors; there is no downstack submit flag), and run `--dry-run` first, reading every line: `(Create)` means it would open a PR for a branch that may not be yours. If you do move another lane's branch, say so immediately and verify by content — file count, insertions, their commit's new parent — never by assumption.

`--draft` converts in both directions: it sets draft state on whatever the submit touches, and `--stack` touches every downstack branch. Submitting a new tip with `--draft` therefore silently converts a published, reviewed base PR back into a draft — reviewers drop off, merge-queue eligibility is lost, nothing warns. Never pass `--draft` over a stack with a published PR anywhere downstack; this skill opens PRs ready with a body, so the flag has no place here anyway. After any submit, verify `isDraft` on every touched PR rather than trusting the flags. A `--dry-run` refusing with "trunk out of date" is fixed by `git fetch origin <trunk>:<trunk>` — not by restacking, which would rewrite published commits.

## A stale local trunk

gt reads local refs throughout, so a local trunk behind `origin/<trunk>` poisons results silently:

- **Titles.** `gt submit` titles a new PR from the oldest commit in `<local-trunk>..HEAD`; a stale trunk puts already-merged commits in that range and the PR opens under an unrelated title, while the diff itself is right (GitHub computes it against the remote base). `git log origin/<trunk>..HEAD --oneline` is the honest commit list; fix a wrong title after with `gh pr edit <n> --title`, taking `<n>` from gt's own output.
- **Commit counts.** `gt track` computes each branch's range against the local trunk — a one-commit branch reports as several, and `gt submit` then opens a PR containing trunk commits. Sanity-check gt's counts against `git log --oneline origin/<trunk>..<branch>` before submitting.
- **False all-clear.** `gt restack` exits 0 with "does not need to be restacked" against a stale trunk, leaving the branch behind the real one.
- **Submit abort.** "trunk branch is out of date and could not be updated" means the trunk is also checked out in another worktree, so git refuses to move the ref. `gt submit --ignore-out-of-sync-trunk` is safe — the PR still targets the remote trunk and GitHub merges against the real tip. Never `git branch -f` or `git update-ref` the shared trunk ref to unblock it.

Before trusting any of these, check `git rev-parse <trunk>` against `git rev-parse origin/<trunk>` and measure drift with `git rev-list --count HEAD..origin/<trunk>`. Only the checkout holding the trunk can advance it — `git fetch origin <trunk>:<trunk>` from a worktree refuses. `gt sync` does advance trunk but prunes merged branches across the whole stack, unsafe while sibling lanes are live. And rebasing onto `origin/<trunk>` with plain git plus re-tracking is worse than the disease: gt still computes ranges against the stale local ref. Fast-forward the ref; don't work around it.

## Scope every restack

Bare `gt restack` walks the whole repo graph, not just your stack: in a repo with many branches it can hit an unrelated `needs_restack` branch and abort before reaching yours, doing nothing while looking like it ran. Always scope: `gt restack --branch <tip> --downstack --no-interactive`.

`gt modify` restacks the whole tracked chain above the amended branch on every invocation, no opt-out — including re-parenting the stack bottom onto trunk, so a long editing session quietly follows a moving trunk and invalidates SHA-pinned check results. Re-read branch SHAs after any `gt modify`; comparing `git rev-parse "<sha>:<path>"` tree hashes tells real change from pure rebase. When part of the chain isn't yours, amend with `git commit --amend` instead and cascade by hand — `git -c rebase.updateRefs=false rebase --onto <new-parent> <old-parent> <branch>` one branch at a time — doing the trunk rebase separately and deliberately. `gt abort` fails outside interactive mode; use `git rebase --abort`, then verify every branch ref still points where expected. Before pushing any restacked branch, diff its diffstat against its base with the pre-restack diffstat: same files and insertions means clean, new files means it's based on the wrong tip.

## Worktrees split the stack silently

`gt modify` and `gt restack` skip any branch checked out in another worktree — one "Did not restack branch X because it is checked out in worktree ..." line amid dozens of "does not need to be restacked" — then record the restack as done. `gt state` reports `needs_restack: false` for the whole chain, while the amend stopped below the pinned branch and the tip is missing your change. Verify propagation by content (`git show <tip>:<file> | grep <the thing you added>`), never by exit code or `needs_restack`. Repair needs plain git, since gt believes there's nothing to do — find the old shared commit in `git log --oneline <trunk>..<tip>`, then:

```bash
git -c rebase.updateRefs=true rebase --onto <new-parent> <old-parent-sha> <tip>
```

which moves every intermediate branch ref in one pass. Prevention is cheaper: before a stack-wide `gt modify` or restack, run `git worktree list` against the chain and `git switch --detach` every worktree holding a chain branch. A stack that must stay spread across worktrees needs one scoped `gt restack --branch <b> --downstack` run inside each worktree, bottom-up.

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

`gt track --parent <branch>` records the parent edge; it moves nothing. A branch cut from an old base stays there after tracking — `gt log short` marks it `(needs restack)`, because the restack pass rebuilds descendants, not the branch onto its own parent. Check `git rev-parse HEAD~1` against `git rev-parse <parent>`; if they differ, `git rebase <parent>` in this worktree, then re-track.

A plain `git rebase` on a gt-tracked branch silently untracks it: every later gt command fails with "Cannot perform this operation on untracked branch", preceded by a warning naming *other* diverged branches, so the error reads as unrelated noise. `gt track --parent <parent>` restores it — do it right after the raw rebase, not at submit time. Note also that `gt create` names the branch from the commit subject, so a branch pre-created with `git worktree add -b` is left behind as an empty stub at the old base; delete the stub and `gt rename` to the intended name.

When someone rewrites the parent branch under yours, `gt track --parent <parent>` hard-fails ("<parent> is not in the history of <branch>") and every gt command refuses on the untracked branch. Both forcing moves add cruft — a throwaway branch at the old base to track against, or `--parent <trunk>`, which attributes the rewritten stack's commits to you. Stop and hand over plain commits; the base's owner rebases onto the live tip and `gt split`s them into the chain, which is cleaner than rebasing under uncommitted work.

A `gt submit` refusal of "fetched with Git and then tracked with Graphite" can mean the local content is actually wrong, not just a stale lease: after ref repairs, gt can't prove origin's heads are its own pushes. Treat it as a content question — capture origin's heads with `git ls-remote`, work out which chain each side holds — before anyone reaches for `--force`. "Local is newer" is not evidence local is right; the patch-id test in [merge-state.md](merge-state.md) settles it.

## Sequential lanes need a commit boundary

When stacked PRs build on each other and their lanes run sequentially in one worktree, each lane must commit on its own branch before the next starts. "Nobody commits, the orchestrator ships" destroys the boundary: the second lane's edits interleave with the first's in the same files, and no after-the-fact hunk-level split is fast or trustworthy. Stage N runs plain `git commit` on its branch; stage N+1 starts with `git checkout -b <next>`; the orchestrator still owns `gt track`, `gt submit`, and the bodies. Genuinely independent lanes get separate worktrees instead.
