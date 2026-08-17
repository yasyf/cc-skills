# Rebase gates

A rebase that reports success has proven only that git's textual machinery finished. Each gate here caught a rebase that was clean by exit code and wrong in content. The classes: clean-but-broken merges, hunks landed in the wrong copy, conflict markers committed as content, wrong-side resolutions, and shell mechanics that ran the rebase somewhere other than where you thought.

## Clean is not correct

Delta comparison — pre-rebase diff against post-rebase diff, every difference accounted for — proves preservation, not correctness. It's blind to two sides that each merge cleanly and then contradict each other: nothing was lost, the halves are incompatible, and only a build finds it. Two real shapes: the branch's bottom commit deletes a now-unused dependency while a later trunk commit adds code that uses it (both merge clean; the build fails on the missing dep); trunk adds a test against the old shape of a structure the branch refactored (clean merge, type errors). Build every branch in a rebased stack, not just the ends — the middle is where a clean-merge contradiction hides because nobody looks there. And when a rename "fix" relocates an error instead of removing it, the diagnosis is wrong: the real fix belongs in the commit that changes the shape, not the one doing renames.

A textually clean rebase can also fail on a symbol collision: your branch renames or introduces an identifier, and a commit that landed on trunk while you were out added the same name in the same scope. Neither side's text changed, so nothing textual catches it — not `git merge-tree`, not a cherry-pick replay, not `git patch-id --stable` comparison. After rebasing onto a moved trunk, grep the newly landed trunk commits for the identifiers your branch renames or introduces (a rename is the likelier collision, since renames get chosen to be generic), then build. Resolve by renaming the newcomer, never trunk's symbol; attribute trunk's side first with `git log --diff-filter=A -- <file>`.

## Hunks land in the wrong copy

When a function body exists twice — kept verbatim as a test oracle, duplicated ahead of a deletion — a rebase can match an added line against the old copy's context and land the hunk there, reporting success. Rename detection being right about the file doesn't protect the hunk. After any clean rebase over code with duplicated bodies, grep for each added line and verify which copy it landed in.

## Conflict markers ship silently

`git add -u && git rebase --continue` stages unresolved `<<<<<<<` markers as content and commits them without complaint. Gate the continue:

```bash
git add -u
test -z "$(git status --porcelain | grep -E '^(UU|AA|DD)')" && git rebase --continue
```

Never pipe a rebase's conflict list through `tail`: a truncated list means resolving only what scrolled into view. Pipe through `grep -E "CONFLICT|error:|Successfully"` so every path is visible regardless of count. A committed marker can surface far away and unrecognizably — a compiler reporting the conflict label's characters as an illegal token, buried in unrelated parse errors. After a stack rebase, audit every branch at once:

```bash
for b in <branches>; do git grep -l '^<<<<<<< ' "$b" -- <paths>; done
```

## Choosing sides

Taking the superset side of a conflict is not the safe default. A resolver sees two texts, not two intents: when the base deleted a code path and its test together, that pairing was a decision. Resolving toward the more-code side re-adds the path into a tree where nothing asserts it — dead code introduced by the resolution itself. On any hunk where the sides disagree about whether a code path exists, grep the base for the test that covers it before choosing; no test on the base side means the deletion was deliberate — follow the base.

The mirror image is a hunk whose trunk side is empty: trunk deleted a block, your branch adds beside it, the hunk reads "mine adds, theirs removes", and taking your side wholesale resurrects what trunk deliberately moved. It compiles — definitions are self-contained — leaving two homes for one type.

## Counted tables and rerere

Git's merge driver, and `rerere` replaying it, merges the union of two sides' new entries in an array-like table while keeping only one side's declared length. A sized declaration catches it at compile time; unsized sibling tables silently lose one side's row, compile fine, and fail only at a runtime count assertion — so one fixed table proves nothing about its siblings. After any auto-resolution touching a counted or table-shaped roster, hand-verify every related site — the count and entries, any paired golden table, each sibling's row set against the source enum. Then run the actual table tests; a build alone misses the unsized ones. Never drop an entry to make a count fit; fix the count. And `rerere.enabled=false` set locally over a shared `rr-cache` re-arms on a fresh clone.

## Predicting conflicts

`git merge-tree <base> <branch1> <branch2>` (the three-argument form) predicts a rebase's conflicts without writing anything — no refs, index, objects, or worktree change; `--write-tree`, the newer form, does create loose objects. Compute against the real `git merge-base`, never a direct diff against trunk, which conflates trunk's changes with yours. Caveats that turn its output from answer into noise:

- Against a stacked branch's tip it includes every commit below yours; intersect conflicted paths with `git show --name-only --format= <your-sha>` before attributing any conflict to your diff.
- For a whole stack it over-counts: each branch's cumulative tree merges independently, so one conflicting hunk low in the stack reports once per descendant. Replay with `git cherry-pick` in a detached worktree for the real topology — mid-stack branches often come clean once the bottom resolves.
- One side renaming a file while the other edits the old path can silently drop or duplicate content in a clean merge; compare the two blob hashes (`git rev-parse "<ref>:<path>"`) to close the class.
- A conflict map is a fact about two specific commits and goes stale in minutes under a moving trunk. Hand a lane where to look and how to measure, never what it will find.

## Auditing for a silent revert

To check whether rebases silently reverted a cleanup that had already landed, scope by landing lane, not branch count. A squash-merged PR lands one patch through GitHub and is never exposed to `rerere`; only commits landed by local rebase-and-push are — spot them by the missing `(#N)` subject suffix. The sharp per-branch test is `git merge-base --is-ancestor <cleanup-sha> <branch>` combined with the deleted path still present: a squash-merged tip is never an ancestor of trunk, but a branch rebased past the cleanup does carry its SHA. Without the ancestry test, every un-rebased branch reads as a false finding. Match declared symbols, not raw lines — line matching drowns in boilerplate. Pin the tip ref before starting (`origin/*` moves mid-audit), and give every detector a positive control that must fire.

## One command per call

Never chain `git worktree add`, `cd`, and a history-rewriting command in one shell call. When the `worktree add` fails — branch already checked out elsewhere — and a pipe to `tail` masks its status (a pipe reports the last command's exit status, so `set -e` never trips), the `cd` fails too and the rebase runs in the original checkout, moving whatever branch is checked out there. Trunk included: `git rebase --onto <sha> <sha>` has rebased a repo's trunk onto a feature branch this way. One command per call for anything that rewrites history; verify `git rev-parse --abbrev-ref HEAD` reports the expected branch in a separate call before the rebase; snapshot the stack's refs (`git rev-parse --short` over each branch) before any wide restack so damage is detectable; recover from `git reflog show <branch>`.

## Colocated jj repos

In a colocated jj/git repo, any bare `jj` invocation snapshots the working copy and imports git refs — mid-`git rebase`, that wipes `.git/rebase-merge` and abandons the rebase, losing unstaged conflict resolutions. It triggers indirectly: build and codegen tooling that resolves the repo root by shelling out to jj rewrites VCS state with nothing in its name suggesting it would. Mid-rebase, save conflict resolutions outside the repo first (`git show :<path> > /tmp/<name>`), and run jj only with `--ignore-working-copy`, which neither snapshots nor updates the working copy.
