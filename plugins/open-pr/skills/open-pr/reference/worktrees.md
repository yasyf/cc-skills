# Worktrees and shared checkouts

A linked worktree buys a private index and checkout, nothing more. The object store, every branch ref, every remote-tracking ref, and the hooks are shared with the main checkout and every sibling lane — most of the hazards below are one half of that sentence forgotten. Each was paid for in lost work; the commands are the detection and recovery that worked.

## `.git` is a file

In a linked worktree `.git` is a file (`gitdir: <main>/.git/worktrees/<name>`), so any probe that path-checks `.git/<something>` is vacuous — `ls -d .git/rebase-merge` reports "no rebase in flight" even mid-rebase, a check that looks passed but was never capable of failing. Resolve the real path first:

```bash
GD=$(git rev-parse --git-dir)
ls -d "$GD"/rebase-merge "$GD"/rebase-apply 2>/dev/null
```

Stale rebase state there survives `git reset --hard` and blocks the next rebase. `git rebase --abort` resets to *its* recorded `orig-head` — possibly a commit you deliberately discarded — so read `"$GD"/rebase-merge/orig-head` first; when HEAD is already where you want it, `rm -rf "$GD"/rebase-merge` plus `git checkout -B <branch> <good-sha>` is safer than an abort. A dead rebase also leaves HEAD detached while the branch ref still points at the bad commit; check both:

```bash
git symbolic-ref -q HEAD || echo detached
git rev-parse refs/heads/<branch> HEAD    # must match
```

Workspace-manager checkouts are usually linked worktrees of one main clone, not separate clones — `git rev-parse --git-common-dir` settles it. When it names another checkout's `.git`, everything in this file applies there too.

## Refs move under you

Every sibling shares `origin/*`: another session's `git fetch` advances `origin/<trunk>` mid-task, so a previously clean `git diff origin/<trunk>` suddenly shows unrelated files as reversions you'd be committing — `git add -A` would take them. Before the final commit, re-run `git diff --stat origin/<trunk>` and confirm every path is yours; when trunk moved, rebase rather than reset.

`--force-with-lease` does not protect sibling lanes: the lease compares against the **remote** ref only, so a lease pinned to an unchanged origin happily overwrites a branch a sibling rewrote locally and hadn't pushed. Before advancing origin for any branch in a shared `.git`, compare local against origin for **every** branch in the stack:

```bash
for b in <branches>; do
  printf '%-32s local=%s origin=%s\n' "$b" \
    "$(git rev-parse --short "$b")" "$(git rev-parse --short "origin/$b")"
done
```

Any branch where they differ is unlanded work belonging to someone; find the owner before pushing.

Two more ref movers:

- **`rebase.updateRefs=true`** (a common global setting) silently moves *every* branch ref inside a rebased range — even on a detached-HEAD "dry run", because the feature keys off commits, not the checked-out branch. One 27-branch dry run moved all 27 real refs. Guard any rebase meant to leave refs alone: `git -c rebase.updateRefs=false rebase ...`. Graphite is unaffected; `gt` manages its own refs.
- **Local-only tags don't survive** in a shared `.git`: a sibling's `git fetch` with tag pruning wipes every tag origin doesn't carry, minutes after `git tag -f` exited 0. Anchor to plain refs outside `refs/tags` instead — `git update-ref "refs/backup/$(date +%Y%m%d)/<name>" <sha>` — and verify by read-back (`git for-each-ref refs/backup`); a backup ref is not a backup until you read it back.

## The base a delegated worktree inherits

A worktree spawned for an agent lane branches from the main checkout's *current HEAD*, not from `origin/<trunk>`. When the main checkout sits on a deep unmerged branch, the lane comes back clean, green, and self-contained — atop dozens of foreign unmerged commits, and nothing in its report shows it. `gt submit --stack` from there pushes and opens PRs for every inherited downstack branch; `gt submit --dry-run` makes it visible, since a clean run names only your branches. Before tracking or submitting anything built in a delegated worktree:

```bash
git fetch origin <trunk>
git merge-base --is-ancestor <base-sha> origin/<trunk> && echo ok || echo "foreign base"
git rev-list --count origin/<trunk>..<base-sha>    # how much you inherited
```

Fix by rebasing onto trunk before tracking: `git rebase --onto origin/<trunk> <base-sha> <branch>`, tip last. Two gotchas: a branch checked out in another worktree cannot be rebased from this one (`fatal: already used by worktree` — run it from the worktree that owns it), and rebasing moves the parent's SHA, so the tip's rebase must still name the *old* parent SHA as its upstream limit. The base moved, so every gate that ran pre-rebase graded a different tree — run them again.

## Foreign worktrees: measure, never reset

`fatal: cannot force update the branch 'X' used by worktree` is about the ref being **attached**, whatever SHA you're moving to — resetting the branch to the target does not lift it. What frees it is detaching that worktree, `git -C <worktree> checkout --detach <sha>`, then `git branch -f X <sha>` if the ref should move; the worktree can stay.

Never blind-reset another lane's worktree on its owner's say-so — their view is nearly always stale. Measure first, save if either probe is non-empty, then detach rather than reset:

```bash
git -C <worktree> status --porcelain                # uncommitted work
git -C <worktree> diff <their-reported-sha> HEAD    # committed but unreported
git -C <worktree> format-patch --stdout <base>..HEAD > save.patch
```

A "standing down" or "FINISHED" message is a claim about the past: one lane read clean and was mid-edit; another's "nothing further" branch held three unpushed commits; a third amended and force-pushed ten minutes after handoff. Make fetch-then-measure the step immediately before any write to a shared ref, and spell the lease out — `git push --force-with-lease=refs/heads/<branch>:<expected-sha>` — so the push itself refuses if the ref moved. A dirty count that changed since the last check (0 → 1) is a stop even when the tree is "supposed to be" clean. Print the file (`git status --porcelain`), `git stash push -m <why>`, only then reset — an unstaged file destroyed by `reset --hard` has no git object, no reflog, no recovery. And force-push only the branches your own branch→SHA table names: a `local != origin` mismatch outside it can mean origin carries newer foreign work; investigate and adopt or escalate, never blanket-push mismatches.

## A dirty tree with no obvious owner

Uncommitted files are ambiguous between a live peer mid-thought and a dead lane's orphaned work, and mtime plus agent listings don't discriminate — quiet is a lane thinking, and "no claimant" is not "no owner". Asking costs one round trip and settles it; a commit landing after your measurement proves someone is home. Then:

- **Live peer** → stand down, get your own worktree (`git worktree add <path> <branch>` costs seconds), never write.
- **Dead lane** → the tree is yours, but the residue is untrusted input, not progress: an interrupted multi-file edit is routinely half-applied (a rename in the declaration but not at call sites). Read the full diff, build a per-file inventory, and never `git add -A` it.

Already wrote into a peer's tree? Don't commit to tidy up: extract your changes to a patch outside the worktree, `git checkout --` the files back to their committed state, and hand over the patch path.

Attributing dirt "by elimination" produces wrong accusations. What works: `git reflog --date=iso` names branch moves and their timestamps, file mtimes split dirty files by side, and grepping for a symbol only one lane would write beats judging by directory. A non-compiling tree full of unfamiliar staged renames is a peer mid-refactor: commit nothing, unstage nothing, revert nothing — you can't tell which staged rename is theirs, and guessing wrong destroys their work.

Two mechanical traps in shared trees: zsh doesn't word-split unquoted parameters, so `git diff -- $PATHS` silently diffs nothing — use an array and `"${paths[@]}"`; and `git stash push` takes the entire worktree, so identify your stash by its `WIP on <branch>` line before popping, or cherry-pick files back with `git restore --source='stash@{0}' -- <paths>`.

## Read the tree you think you're reading

Shell `cd` state doesn't persist between tool calls, so a bare `cd <dir> && sed -n ...` reads whatever checkout the shell last sat in. Measured: the same function at line 401 in one checkout and 801 on the lane branch, with opposite semantics — a design rested on the stale read. Prefix reads with the absolute lane path or `git -C <lane>`, verify a plan's code anchors in the exact worktree the lane will edit, and treat inherited line numbers as approximate until re-measured there.

## Adoption, teardown, and lifetime

Worktree removal is a one-way trip: removal tooling typically runs `git worktree remove --force` (ignoring a dirty tree) then `git branch -D` (taking the commits), with no archive. An **adopted** worktree is on loan: its original owner can reclaim or delete it the moment their own PR merges — observed destroying two lanes' uncommitted edits at once, with a build erroring `No such file or directory` mid-run as the tell. So: never brief a lane into a worktree the briefing session doesn't own; commit each coherent edit the moment it exists rather than holding for a final pass (an instruction to defer all git writes to the end is a data-loss risk worth raising); and capture `git diff` into your report as work completes, so a verified-then-vanished result leads with the surviving diff.

Commits are visible from every worktree sharing the `.git` and survive cleanup, so when taking over a stopped lane's tree, commit it from outside: `git -C <worktree> add <paths> && git -C <worktree> commit`. Never resume the lane instead — a resumed worktree-isolated agent whose tree was cleaned runs in the **main** working copy, where it can hijack branches and write over in-flight work. To continue stopped work, fresh-spawn at current HEAD with the full brief, and give lane prompts a first-action guard: stop unless `git rev-parse --show-toplevel` is the lane's own path.

A freshly created worktree at a predictable path can be adopted mid-task by a parallel lane racing for the same name. Check `git status --short` and `git log -1` for foreign edits before building on it; on foreign activity, wait for quiescence (HEAD and the porcelain hash stable for ~90s), then adopt the foreign commits and stack a correction on top — never amend or race a foreign commit. Re-read `git rev-parse HEAD` immediately before committing whenever a long gate ran since the last check.

Branch base age is not a disposability signal — in one measured fleet, every lane flagged "stale" by base age was live, and pruning by it would have deleted hundreds of branches whose commits no remote ref carried. The real liveness signals, in order: `git status --porcelain` empty; `git branch -r --contains HEAD` non-empty or `git rev-list --count origin/<trunk>..HEAD` returning 0; directory mtime idle past a threshold. And a fleet-wide write (backfilling a file into every worktree) resets every lane's mtime, blinding any mtime-based reaper — restore with `touch -r <newest-real-child> <worktree>` excluding build dirs and the new file, or write outside the observed directories.

## Stacks: which branch owns a change

`grep` finding a file in every descendant worktree proves nothing — every descendant contains every ancestor's files, so presence reflects where you happened to stand. Only history says who owns it:

```bash
git log --oneline --diff-filter=A -1 -- <path>
```

Run it per file before splitting a multi-branch fix, restack bottom-up, and put a shared helper in the *earliest* branch that consumes it, not the one where the bug surfaced. The mistake is silent — the fix works either way, it just lands in a PR with no business carrying it and muddies both reviews.

A review pointed at a shared worktree drifts when someone stacks the next branch on it mid-review: one worktree, one activity. Review briefs carry the commit SHA as the object of review, never "the worktree's HEAD", and the reviewer pins a detached checkout when the tree may advance.

## Stacks: hand over patches, not SHAs

A coordinator assembling a stack rebases lane branches in its own worktree; your branch can be replayed and moved while you work. The tells: `git commit` prints `[detached HEAD ...]`, or `git rebase` reports "updated detached HEAD" instead of updating the branch. Fighting the ref clobbers the coordinator's stack. The standing pattern — verify the patch applies read-only, never inside the owner's worktree:

```bash
git format-patch --stdout <base>..HEAD > /tmp/lane.patch
mkdir -p /tmp/apply-check && git archive <their-tip> | tar -x -C /tmp/apply-check
git -C /tmp/apply-check apply --check /tmp/lane.patch
```

Hand over the patch path, the SHA only as fallback. Once the coordinator has applied vN and restacked on it, follow-ups are standalone delta commits cut on top of vN (`git checkout --detach <vN>`, apply only the delta, commit, `format-patch`) — an amended "v(N+1) of everything" supersedes the applied commit and forces a redo of the restack. When your rebase and theirs raced: `git diff <their-sha> <your-sha>` — an empty tree-diff on the same parent means the replays agree; adopt their SHA as canonical and leave the ref alone.

Graphite's server does the same detach-and-replace with no coordinator involved: when a stacked PR's parent squash-merges, it rebases the child onto trunk and force-updates the child's remote ref. A local `git rebase --onto` run at the same time produces an identical tree under a different SHA — `git fetch` reports `(forced update)` and `git rev-list --left-right --count HEAD...origin/<branch>` reads `1 1`, which looks like divergence and isn't. Compare trees:

```bash
git rev-parse 'HEAD^{tree}' 'origin/<branch>^{tree}'
```

Equal trees on the same parent → `git reset --hard origin/<branch>` instead of force-pushing a duplicate; your local gate run still applies, since it graded that tree. Graphite then blocks the next submit with "fetched with Git and then tracked with Graphite" — `--force` is correct only after `git diff HEAD origin/<branch>` comes back empty.

## After a squash-merge the branch is gone

A merge-queue squash-merge deletes the remote branch: `git ls-remote --heads origin <branch>` returns empty **with exit 0**, `origin/<branch>` stops resolving, and a scripted `git reset --hard origin/<branch>` dies with "unknown revision". The content is on trunk, not on any branch — verify with `git diff origin/<trunk> HEAD -- <file>` coming back empty, and point downstream lanes at trunk. The PR meanwhile reads `CLOSED` with `mergedAt: null` — [checks.md](checks.md) covers reading that correctly.

## Hooked commits can drop content

Pre-commit machinery that stashes and restores around hooks can revert staged work when interrupted — in a shared `.git`, `index.lock` contention from a sibling mid-commit is the usual interrupter, and the damage includes new files truncated to zero bytes. Check `ls "$(git rev-parse --git-common-dir)"/worktrees/*/index.lock 2>/dev/null` before a hooked commit; after a hook-machinery failure with hooks you've already verified by hand, `git commit --no-verify` beats retrying into the same contention.

Verify after **every** hooked commit, because the drop is silent in both directions:

```bash
git diff --cached --stat    # before: what you staged
git show --stat HEAD        # after: must list the same files
git show HEAD:<file>        # content — the working tree still holds the edits, which is what makes a drop invisible
```

A commit can report every hook passed while staged files land without their hunks. `gt modify` has a skip variant: it completes "successfully" with no amend at all — staged edits end up back unstaged, HEAD untouched, and the following `gt submit` pushes the stale commit; verify `git status --short` empty and `git show HEAD:<file>` current before submitting; when it skipped, re-add and re-run — the second attempt lands it. `ccx vcs ship --only-hunk <ref>` scopes to the named hunk only *within that file* — every other modified file is committed whole; pass the intended files as positional paths alongside the hunk refs, check `git show --stat` after, and recover with `git reset --mixed HEAD~1` then re-ship with `--append`. In a linked worktree ship can also miss the Graphite config (it reads the worktree gitdir, not the commondir) and fall back to committing **detached**; recover with `git branch -f <branch> <sha> && git switch <branch>`.

## Branch names collide with refs

A branch named `<scope>/<thing>` is unpushable when a bare ref named `<scope>` already exists on the remote — a ref file and a ref directory can't share a path. Stack submits push `--atomic`, so one bad name fails every branch, and the siblings report a bare `(failed)` that reads like a permissions problem. Check before naming, and rename so Graphite's metadata follows:

```bash
git ls-remote --exit-code --heads origin <scope>    # exit 0 → the prefix is taken
gt rename <new-name>
```
