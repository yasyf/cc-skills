# Principles for grading

- [Read state](#read-state)
- [Grade heads and merges](#grade-heads-and-merges)
- [Check instruments and checkers](#check-instruments-and-checkers)
- [Grade plans and applies](#grade-plans-and-applies)
- [Grade deletes and access](#grade-deletes-and-access)
- [Prove landings and manage the queue](#prove-landings-and-manage-the-queue)
- [Run the grading fleet](#run-the-grading-fleet)

## Read state

**Read the trunk from the remote ref**, never from a bare checkout path: a shared working tree can sit on an unlanded branch while looking like trunk. Use `git show <remote>/<trunk>:<path>`, `git ls-tree`, or `git log`. A bare-path read means auditing the whole session.

**Refuse to grade when the repository is shallow or a fetch failed.** A shallow clone (`git rev-parse --is-shallow-repository` returns `true`) truncates history traversal silently. A failed fetch leaves stale refs when worktrees contend on a ref lock:

```
error: cannot lock ref 'refs/remotes/origin/dev': is at <a> but expected <b>
```

Write nothing and report why.

**Settle every diff and log check against `origin/<trunk>`**, never `origin/<base>`. The merge queue deletes a stacked PR's base branch when the stack lands, so `origin/<base>` stops resolving, and diffing a missing ref aborts or reads as empty.

**Tie every failure claim, grade, and hold to a commit SHA and build number.** A hold or grade applies to one SHA and expires when the head moves.

- Run a refresh pass over live ledger rows before quoting one.
- Comparing two plan runs: compare the plan-job start times and name any applies that landed between them.
- Before calling a refusal permanent, name the plan's timestamp and check whether an apply for that commit finished after it. If one did, regrade and say nothing until the regrade completes.

**Check whether a fix lives in the tree or the live world before reporting a regression or rebuilding a branch.** CI builds tooling and declarations from the branch under test, not the trunk.

```sh
git merge-base --is-ancestor <fix-sha> <pr-head> && echo has || echo lacks
```

If the branch lacks the commit, route a rebase past `<squash>` to the owning lane, and warn them that clearing an early `assertPlanned` failure may surface a later diagram render error.

**Pin the plan digest when comparing two verdicts across a bar change**: a constant digest proves the bar change caused the new verdict. A `create` op that reads `same` on the next plan confirms the resource exists live without cloud console credentials.

**Choose the gate layer that can observe the distinction.** Ledger status is identical for an intentional retirement and a stale branch deleting a resource it predates, so the import-class bar cannot separate them. Check staleness in the change-set gate: intersect the paths touched between the plan's baseline SHA and the current trunk tip with the paths the stack reads. Printing the plan's baseline against the tip is one field read, because the artifact records the SHA it was planned on.

**Prefer diff-local claims over ancestry or tree-content claims near a moving head.** A file-set intersection holds regardless of pushes, and ancestry holds because heads move forward. A tree-content check expires on the next push and cannot sit inside a staleness gate.

## Grade heads and merges

**Compare all three refs before certifying or labeling a head**: the local head, `origin/<branch>`, and the PR's `headRefOid`. Concurrent restacks move a lane's branch without the lane acting, so in a batch re-read each tip immediately before grading it. Grade the current head under D3 ([landing.md](landing.md)) and pin the tip with `--expect-head` when labeling.

**After a post-label force-push, pull the label first** (one reversible call). `git merge-base --is-ancestor` fails on squashed trial merges and `git patch-id` changes on pure rebases, so answer with file content: grep the twin SHA for a line only the newer head has.

```sh
git show <twin-sha>:<path> | grep -q '<line only the newer head has>' && echo took-newer
```

**Detect payload changes by diffing the old and new heads over the PR's own files** against `origin/<trunk>`:

```sh
git diff --name-only $(git merge-base $NEW origin/<trunk>) $NEW > /tmp/files
while IFS= read -r f; do git diff --stat $OLD $NEW -- "$f"; done < /tmp/files
```

An empty diff proves a pure rebase and the head can be re-labeled at once; a non-empty diff means regrade the new head.

**Check semantic compatibility with the current trunk when it moves faster than the branch.** A branch's green status, clean `mergeable_state`, and test suite evaluate the head only against its base commit. A head that falls behind a trunk landing that renamed, moved, or deleted anything is unverified until the desk grades the merge of the head with the current trunk. When such a landing has occurred since the branch's base, or a large diff goes stale before grading finishes, pin the gate to the graded SHA and check whether trunk landings touched imported symbols.

**Edit the PR body before applying the label.** The squash merge takes the body as it stands at merge time and discards branch commit messages.

**Hold immediately when two open PRs touch the same lines**, and ask the ownership question from behind the hold.

**Treat `ai-review` at `neutral` as a blocking hold.** It reports `neutral` when it holds a PR on a review comment, leaving `mergeable_state` at `blocked` while failed-check sweeps and combined status read clean. Require `success`, report the review comment on the diff, and never re-run the review to clear it.

**Read a failing check's conclusion, summary body, and annotations, not its title.** Security scanners often share one category title across `neutral` and `failure` runs.

- Compare a passing PR with the same title to rule out category theories.
- Quote the summary body when reporting.
- Ask the lane to tighten a false-positive pattern instead of suppressing it.

## Check instruments and checkers

**Test every verification check against a known-failing and a known-passing control before trusting its verdict.** Without both, a check can return a silent permissive answer. Every control exercises the same code renderer or escape hatch as the subject. State in the report that the control ran, and check where a mechanism last succeeded before debugging why it failed.

**Use only read-only controls when probing a gate.** The labeling gate applies the label as its final step and has no dry-run mode. Test a gate or lookup fix with a refusing input or the read step alone, so custody never transfers to the queue.

**Write checkers that fail closed** on unresolved inputs, empty lists, and shell word-splitting. Under zsh an unquoted newline-joined variable does not word-split, so both idioms below match no pathspec, print nothing, exit 0, and falsely report a landing:

```sh
FILES=$(gh api ".../files" --jq '.[].filename' | tr '\n' ' ')
[ -z "$(git diff origin/dev "$SHA" -- $FILES)" ] && echo LANDED
```

```sh
files=$(git diff --name-only <head>~1 <head>)      # newline-joined
git diff --name-only origin/dev <head> -- $files   # empty => landed
```

Verify a SHA exists before diffing against it, assert a file list is non-empty, and check exit status rather than output emptiness. `grep -c` prints `0` and exits 1 on no match, so `c=$(... | grep -c X || echo 0)` yields `0\n0` and makes `[ "$c" = 0 ]` false. `$(cat file || echo "$VAR")` fails when `file` is empty instead of missing.

| Fragile pattern | Required replacement |
|---|---|
| Unquoted `$FILES` or `$files` in zsh | Null-delimited `xargs -0`, one path per loop call, or zsh array `${(f)files}` / `${=FILES}` with length asserted against the PR file count |
| `grep -c X \|\| echo 0` for presence | `grep -q` for presence, or structured field parsing that prints what was counted beside the count |
| `export TOK=$(cat missing-file)` or `$(cat file \|\| echo "$VAR")` | `${VAR:-$(cat file)}` |
| Unbounded assumption on paginated API lists | Loop with `--paginate` until a page returns empty; never stop at 30 files, 30 builds, or 4 pages of 100 artifacts |
| Returning an empty set on HTTP `429` | Back off on `429` inside the shared fetch, pace re-runs, and assert non-empty derived sets |

**Inspect the instrument when independent subjects fail or pass identically** (four PRs fail with the same HTTP `404` or scan miss; consecutive landing builds fail on the same job). Stop grading individual heads; fix the grader input, or walk the build list back to the last pass to date the single breaking commit.

**Audit the newest passed landing build whenever a grading defect is found.** Pull every `plan/apply-<stack>.json` from the latest passed landing build and count stacks with `"passed": false`, to see whether the broken instrument already let a refused change through. Keep watching verdict artifacts positively when a widened bar leaves the refused column mostly empty.

**Verify a bar or guard change through the fields it writes into verdict artifacts.** A guard PR has no plan surface, so green only proves compilation. When a bar change adds an op field such as `belowVerified`, compare a stack's verdict artifact across the old and new engines and check for a non-degenerate `true`/`false` distribution.

**Verify constants that describe the outside world against two independent upstream sources.** For wire codes, API enums, headers, severities, and resource types, require citations from two independent upstream sources (such as vendor TypeScript and Go clients) in the PR body. A unit test in the same repository asserting `COUNT = 3` only pins the constant to itself.

**Count the shards a green mirror build ran.** A green mirror build counts only if the shards that test your code ran: count the shards, grep the test's PASS line, and diff the trees against the build that ran them. A mirror can be green with no shard of the changed package in it.

**Read the commit a mirror build actually ran** (Buildkite's commit) before relaying its result. Use an explicit sha with `--force-with-lease`, and `ls-remote` before posting a head.

**A red proof counts only when it fails with the expected error.** Quote that error. The proof's subject must still be broken when the proof runs: a red run can mean the test is wrong rather than the code, and a proof that passes only because someone closed the state by hand proves nothing. Keep a control that must stay in the old state.

**Make a regression test drive the production wiring.** If the rig already chooses the fixed path, the test passes on the parent. Drive the real entry point and wait on events, not sleeps.

**Read every input of a condition before calling a finding or row real.** A blocker called real without reading what its counter counted turned out to count `MAX(seq)`.

**Back a compile-fix claim with the build log's full error list.** A claim that a PR is the only compile fix needs every error the log lists; a revert that leaves a second error turns dev red.

## Grade plans and applies

**Grade the uploaded `plan/<env>-<domain>.json` artifact**, never the lane's reported op counts, and tell cache pruning from an empty plan surface.

- `infra-report` is a cache unit over `INFRA_CHECK_READS`; `INFRA_PACKAGE_READS` excludes the CI package except individually named files such as `modes.ts`.
- Check the build's uploaded artifacts directly even when no PR plan comment folded (as on adoption-record diffs).
- Refuse a missing `infra-report` build only when the diff touched a named read.
- A build that ran and uploaded zero verdicts is positive proof of no plan surface.

**Report "no plan surface, the landing decides" when a change schedules no preview.** The same holds when a retirement gate runs before apply and cannot see post-apply tag residue (`beyondTags` in `infra/lib/retire.ts`): green tests and reviews do not prove the remedy. Note the unverified surface in the ledger row and label message, preserve the lane's word `probe` when flagged, and assign two independent readers to the landing build.

**Read a job's own output, not the build color, and track three numbers on red builds**: refused stacks, stacks with no verdict artifact, and the overall build state. Jobs are per-stack and independent, while `assertPlanned` turns one stack's hard preview error into a whole-build failure. A passing job in a failing build still satisfies a stack gate if the failure shares no stack, domain, credential path, or cause. A stack has no verdict artifact when an env at `adopting` or `verified` in the ledger is missing from the build's verdict artifacts. Report a hard plan failure as not gradeable, never as clean.

**Put the `.apply.yaml` enablement flip in the same PR as the rows it enables, and read preconditions from the graded commit.** A stack whose sidecar reads `pending-owner` and that lacks backend state exits 0 with zero ops and an empty digest.

- `could not be previewed` with a real error is evidence of a problem and requires a hold; `pending-owner` is evidence of nothing and requires none.
- A held stack that already has backend state previews normally: check the backend before blaming the hold, and never cite the hold to justify flipping a stack that has state.
- Grade an enable PR both as written and for what its `.apply.yaml` change arms, reading the sidecar from the plan artifact's `commit`, never `origin/dev`.
- When a no-plan PR is unavoidable, the fallback bar is green CI, `ai-review` `success`, and no `.apply.yaml` diff.

**Run the import-class bar only on `:pulumi: land` jobs, never `:pulumi: apply` jobs.** Stacks whose infra-landing job verb is `apply` (such as `core-gbl-auto-buildkite`, `core-usw2-auto-ci`, `plat-use1-prod-ci`) do not run `landingAdmits`, even if the PR plan comment says the landing refuses the change. Derive the apply-path set from live job names and fail loudly if it is empty.

**Read `plan/apply-<stack>.json` and the post-apply boolean from the landing build, queried by full commit SHA.** A short SHA returns zero builds, which reads as no evidence instead of a bad query. A `:pulumi: land` job exits 0 even when `plan/apply-<stack>.json` records `"passed": false` and a `refused` array, and a plan taken while an apply is in flight shows pre-apply drift. Read `<stack>: applied; post-apply preview all same`, and distinguish the pre-apply `ops` census from the post-apply boolean:

| Verdict `ops` table | Post-apply boolean | Meaning |
|---|---|---|
| Any non-`same` op | `true` | The apply ran and a post-apply re-preview confirmed convergence |
| All `same` | `true` | The engine short-circuited with nothing proposed and took no after-preview |
| Any op refused | `false` (`"passed": false`) | The whole stack was skipped; admitted ops in the same stack did not apply either |

**Grade per-resource URNs, classifier pins and unfiltered provider lists**, not summary counts or digests. Two plans can share an op census while moving different URNs.

- The plan `digest` filters out `pulumi:providers:` ops, so planner-role CI plans and writer-role hand plans can match on digest while differing on provider updates. A lane-versus-artifact count difference confined to `pulumi:providers:` entries is expected.
- Never argue a lane's counts down from a digest match; compare provider ops by reading both lists.
- When an admitted op lists a create-time key in `diffs`, check the branch's adoption record for an `ignoreChanges` pin before refusing.
- Report any stack refusal as the entire pending op census, not the single offender.

**Require explicit resource names in every synthesized Pulumi project.** `infra/engine.ts` sets `pulumi:autonaming: value: mode: disabled` (asserted in `infra/engine.test.ts`), so every resource needs an explicit name property such as `name`, or `statementId` on `aws.lambda.Permission` (`aws.lambda.FunctionUrl` has none). A failing preview stops at the first unnamed resource, so compare the creates in the log against the creates the row declares to find every un-rendered resource.

## Grade deletes and access

**Clear `protect` and `retainOnDelete` in state before previewing to grep for `[retain]`.** A resource carrying `protect` aborts the preview before any op list prints:

```
error: Preview failed: resource "<urn>" cannot be deleted because it is protected.
Resources: 32 unchanged, 3 errored
```

The lane edits state first, re-previews, and greps the preview for `[retain]`. A landing shows the same abort when an environment's rows leave while protected state entries remain, so check for `protect` when triaging a stuck `land` job before assuming drift or a bar refusal.

**Land row removals before or with a live environment destroy.** A landing build treats an enabled stack with no state as an import, and its preview fails when the live resources are gone while the rows remain:

```
aws:ec2:Vpc  <env>  import error: Preview failed: resource 'vpc-0c47b1e6127bcc069' does not exist
```

When `land` jobs fail with `does not exist` on import across an environment, track recovery by counting how many of that environment's jobs remain as row-removal PRs land.

**Grade a delete by its provider and child ops, and check internal totals before reporting.** A Pulumi component shell delete carries no `provider` and `retained: null`, because there is no cloud object to retain. Treat a `delete` op with no `provider` as state-only, check whether any child resource in the plan also plans `delete`, and cross-check your own total `delete` count across stacks before raising an alert.

**Before labeling a retirement, check literal file readers and surviving stack adoption records.** Cached test units do not track files opened at run time, and removing adoption records can orphan live resources on the next apply.

- Grep the PR's own head tree for every deleted file path. This matches only literal paths and cannot see a test that asserts on a name derived from a deleted entry, so still read a hard red of that shape from the build.
- Check that the surviving stack's plan still adopts all claimed live resources.
- Prefer moving test fixtures into the test tree over deleting security-relevant tests.
- Temporal boundary plus scope overlap is strong evidence but not a mechanism: name it as such, ask the owning lane the question that decides the fix, and never narrate a causal chain from a deleted directory to an orphaned resource until someone has read one.

**List a class by its methods before naming what a deletion loses.** Tallying records alone misses methods the class carries (a live bug in a withdrawn path went unnamed this way).

**Run `git log -L` on any op that touches a trust or policy document, and escalate access removals to the owner.** An `update` op that narrows trust, permissions, or policy statements looks identical whether it converges a hardening change or reverts one. Trigger the provenance check whenever an op's `diffs` array names a key from the document-input key set or a principal/permission watchlist. Derive the key set from the map the engine already uses instead of hand-copying one, and match on the key alone, not the key-plus-type pair the rewrite check uses.

```sh
git log -L <line>,<line>:<file> <trunk>
```

Read the introducing commit yourself, and route any access-removing plan to the owner instead of labeling it.

**Check IAM policy grants and reader necessity when a docs-only diff names a secret path.** Prose diffs schedule no plan, but an instruction placing a secret on a shared prefix can expose release gates to CI instance profiles. The desk reads the policy file and queue policy list to verify who can read the prefix; the owning lane verifies which jobs need read access.

**Read the cited lines before accepting or rejecting a finder verdict.** Accepting a wrong finder major, or banning a table-stakes fallback, without reading the code is a defect.

**Grade a contract against the register's owner picks and read the cited code before any ok.** Grep the register's picks first; an ok given without that is withdrawn.

## Prove landings and manage the queue

**Prove a landing with a positive trunk read; tree equality only corroborates.** Forge PR pages (`closed` with `merged: false`), bot closing actors, `externally-merged` or queue merged labels, and immediate post-label checks cannot prove whether a PR landed. After verifying `git rev-parse --is-shallow-repository` is `false`, require one of:

- an added file existing on trunk: `git cat-file -e origin/<trunk>:<new-path>`
- a deleted directory absent: `git ls-tree -d origin/<trunk> <dir>`
- a pathspec-immune ancestry check: `git merge-base --is-ancestor <head> origin/<trunk>`
- a squash commit on trunk:

  ```sh
  git log origin/<trunk> --oneline -400 --fixed-strings --grep="(#<n>)"
  ```

**When a stacked child lands its parent's payload and the parent closes as a no-op without a commit number**, corroborate with a null-delimited two-dot diff over the PR's own files. Never use a three-dot diff: it diffs against the merge base and shows the payload as present on the branch side regardless of what trunk received.

```sh
files=$(gh api "repos/<repo>/pulls/<n>/files?per_page=100" --jq '.[].filename')
printf '%s\n' "$files" | tr '\n' '\0' |
  xargs -0 git diff --numstat origin/<trunk> <head> --      # empty means it landed
```

**Tree equality proves a landing; a difference or a head blob missing from trunk history never proves absence from content alone.** If the evidence is not terminal, the headline says unknown or waits. When a label was pulled or just applied and the positive checks have not settled, report "in flight, outcome unknown" and never re-label until the content check completes.

**Read the queue's draft twin and the "Merge activity" comment to see queue state.** On Graphite, `graphite-app[bot]` removes the `merge` label 15 to 208 seconds after application on pickup. On Trunk, `trunk-io[bot]` keeps `merge` on landed PRs and removes it on failure with `trunk-failed`. Check the `[Graphite MQ] Draft PR GROUP:* (PRs <n>)` twin's `buildkite/test` run and the PR's "Merge activity" comment, which posts under a human account instead of the bot:

| Queue observation | Meaning and desk action |
|---|---|
| `added this pull request to the merge queue` (or green draft twin) | Enqueued or merged |
| `couldn't merge this PR because it had merge conflicts` (or no twin created) | Ejected before or at pickup; route a rebase to the owning lane |
| Draft twin `buildkite/test` red (including `Render and upload the pipeline`) | Ejected on test or semantic conflict; read the failing job, stack PRs that append to the same file into one batch, never label them one at a time, and stop committing the file when it is a generated output |
| `detected` line with no `added` line after 20 minutes | Wedged; run the un-wedge sequence below |

**Un-wedge a PR that has no `added` line 20 minutes after labeling.** Run these steps in order, stop at the first that works, and escalate to the owner only if all fail:

1. Check `mergeable_state` and commit distance behind trunk; if `dirty` or stale, pull the label and route a rebase onto the trunk tip (never push an empty commit).
2. Re-request the queue's mergeability check run over REST (expect HTTP `404` when another GitHub App owns the check).
3. Pull the label, re-add it once, and wait five minutes.
4. Pull the label and ask the owning lane to rebase onto the trunk tip and re-record the Graphite parent.

**Enqueue the largest green, approved, unheld bottom prefix as one batch, and protect open children above it.** If a parent lands with an untracked open child, the queue deletes the parent branch and GitHub permanently closes the child (`state cannot be changed. The <branch> branch has been deleted.`).

- Never wait for the top of the stack to go green before enqueueing a green bottom prefix.
- `.agents/skills/submit-pr/scripts/stack-enqueue` takes no hold flag and reads no holds, and its gate can refuse the prefix too: check holds and every guard across the prefix first. The enqueue checks on children and adapters are D3 in [landing.md](landing.md).
- Retargeting alone does not rebase a child and leaves its parent's commits in place until rebased: check the file count on retargeted children before labeling.

**After the prefix lands**, check whether native GitHub stacking already rebased the remaining stack. If not, route a restack of the first PR above the prefix to its owning lane with `lr relay <lane> <text>`; the lane runs it on a Sprite with `lr landing restack` or `ccx vcs stack submit`.

**Before declaring a closed Graphite child lost**, resolve the branch SHA from the remote (not the dead PR's stale recorded SHA), confirm the head branch still exists on the remote and merges cleanly into trunk, and check whether it already contains the parent's squash. Recover it by rebasing the remote branch tip onto `dev` and opening a replacement PR with `gt track --parent dev`.

## Run the grading fleet

Grading is the last wait between a green, approved PR and the owner's tap, and capacity is never a reason for it. Graders scale with the number of PRs waiting, without asking: every head's finder and fidelity passes start within two minutes of its head record and finish within ten, and the coordinator adds shards whenever a pass queues.

More passes on one Sprite do not always add speed: its home disk is the limit per box, so add boxes before adding passes per box, and raise a disk stall to the meta desk rather than adding either.

The owner's critical path goes first. The pool ranks a coordinator `ask` or `decide` naming the PRs (`#<n> first`, or `first: #<n>, #<n>`) above everything, then incident lanes, then rows green and approved, then the ledger's priority and tapped rows. A row that waits on a grade alone for 30 minutes alarms; see [Row waits](landing.md#row-waits).

Fidelity grading runs on Sprites like the finder, and may use a smaller model or lower effort when sampled results stay sound.

Dead workers surface within minutes. A live pass on a sick Sprite home disk stops writing, and the pool kills it after 15 minutes of no progress and reruns it elsewhere, so nobody relaunches one by hand. A pass still writing runs to its deadline.
