# Principles for grading

- [Read state](#read-state)
- [Grade heads and merges](#grade-heads-and-merges)
- [Check instruments and checkers](#check-instruments-and-checkers)
- [Grade plans and applies](#grade-plans-and-applies)
- [Grade deletes and access](#grade-deletes-and-access)
- [Prove landings and manage the queue](#prove-landings-and-manage-the-queue)

## Read state

**Read the trunk from the remote ref, never from a bare checkout path.** A shared working tree can sit on an unlanded branch while looking identical to a trunk read. The desk reads trunk files with `git show <remote>/<trunk>:<path>`, `git ls-tree`, or `git log`, and audits the whole session if it catches a bare-path read.

**Refuse to grade when the repository is shallow or a fetch fails.** A shallow clone (`git rev-parse --is-shallow-repository` returning `true`) truncates history traversal silently, and a failed fetch leaves stale refs in place when worktrees contend on a ref lock:

```
error: cannot lock ref 'refs/remotes/origin/dev': is at <a> but expected <b>
```

When either condition holds, the desk writes nothing and reports why.

**Settle every diff and log check against `origin/<trunk>`, never `origin/<base>`.** The merge queue deletes a stacked PR's base branch when the stack lands, so `origin/<base>` stops resolving. Diffing a missing ref aborts or reads as empty, while every stack lands on the trunk.

**Date every failure claim, grade, and hold to a specific commit SHA and build number.** A hold or grade applies to one SHA and expires when the head moves. The desk runs a refresh pass over live ledger rows before quoting a hold or grade. When comparing two plan runs, the desk compares plan-job start times and names any applies that landed between them. Before calling any refusal permanent, the desk names the plan's timestamp and checks whether an apply for that commit finished after it. If one did, the desk regrades and says nothing until the regrade completes.

**Check whether a fix lives in the tree or the live world before rebuilding a branch.** CI builds tooling and declarations from the branch under test instead of the trunk. The desk tests whether a branch contains a landed fix or row-removal squash before reporting a regression or triggering a rebuild:

```sh
git merge-base --is-ancestor <fix-sha> <pr-head> && echo has || echo lacks
```

If the branch lacks the commit, the desk routes a rebase past `<squash>` to the owning lane and warns them if clearing an early `assertPlanned` failure will surface a later diagram render error.

**Pin the plan digest when comparing two verdicts across a bar change.** Holding the plan digest constant proves the bar change caused the new verdict. A `create` op that reads `same` on the next plan confirms the resource exists live without cloud console credentials.

**Choose the gate layer that can observe the distinction.** The ledger status is identical for an intentional retirement and a stale branch deleting a resource it predates, so the import-class bar cannot separate them. Check staleness in the change-set gate by intersecting the paths touched between the plan's baseline SHA and the current trunk tip against the paths the stack reads. Printing a plan's baseline against the current tip is an observation and costs one field read because the artifact already records the SHA it was planned on.

**Prefer diff-local claims over ancestry or tree-content claims near a moving head.** A diff-local file-set intersection holds regardless of pushes, and an ancestry check holds because heads move forward. A tree-content check expires on the next push and cannot be used inside a staleness gate.

## Grade heads and merges

**Compare all three refs before certifying or labeling a head.** Concurrent restacks move a lane's branch without the lane acting. In a batch, the desk re-reads each tip immediately before grading it. The desk checks the local head, `origin/<branch>`, and the PR's `headRefOid`, grades the current head under D3 (`reference/landing.md`), and pins the tip with `--expect-head` when labeling.

**Answer a post-label force-push with file content, after pulling the label first.** Pulling the label is one reversible call, while `git merge-base --is-ancestor` fails on squashed trial merges and `git patch-id` changes on pure rebases. The desk checks which head the trial merge took by grepping the twin SHA for a distinguishing line:

```sh
git show <twin-sha>:<path> | grep -q '<line only the newer head has>' && echo took-newer
```

**Diff the old and new heads over the PR's own files to detect payload changes.** The desk restricts the two-head diff to the PR's file list against `origin/<trunk>`:

```sh
git diff --name-only $(git merge-base $NEW origin/<trunk>) $NEW > /tmp/files
while IFS= read -r f; do git diff --stat $OLD $NEW -- "$f"; done < /tmp/files
```

An empty diff proves a pure rebase that can be re-labeled immediately, while a non-empty diff requires regrading the new head.

**Check semantic compatibility with the current trunk when the trunk moves faster than the branch.** A branch's own green status, clean `mergeable_state`, and test suite only evaluate the head against its base commit. Grading the merge of the head with the current trunk is an upstream fix that isn't built. Until the desk grades the merge instead of the head, a head that falls behind a trunk landing that renamed, moved, or deleted anything is unverified. When such a landing has occurred since the branch's base, or when a large diff goes stale before grading finishes, the desk pins the gate to the graded SHA and checks whether trunk landings touched imported symbols.

**Edit the PR body before applying the label, and place a hold immediately when two PRs touch the same lines.** The squash merge takes the PR body as it stands at merge time and discards branch commit messages. When two open PRs overlap on the same lines, the desk places a hold first and asks the ownership question from behind the hold.

**Treat `ai-review` at `neutral` as a blocking hold.** `ai-review` reports a check-run conclusion of `neutral` when it holds a PR on a review comment, leaving `mergeable_state` at `blocked` while failed-check sweeps and combined status read clean. The desk requires `ai-review` `success`, reports the review comment on the diff, and never re-runs the review to clear it.

**Read a failing check's conclusion, summary body, and annotations instead of its title.** Security scanners and check runs often share a constant category title across `neutral` and `failure` runs. The desk compares a passing PR with the same title to rule out category theories, quotes the summary body when reporting, and asks the lane to tighten a false-positive pattern instead of suppressing it.

## Check instruments and checkers

**Test every verification check against a known-failing and a known-passing control before trusting its verdict.** A check that lacks both a failing test and a passing test can return a silent permissive answer. Every control must exercise the same code renderer or escape hatch as the subject. The desk states in its report that the control ran and checks where a mechanism last succeeded before debugging why it failed.

**Use only read-only controls when probing a gate.** The labeling gate applies the label as its final step and has no dry-run mode. When testing a gate or lookup fix, the desk uses a refusing input or runs the read step alone so custody never transfers to the queue.

**Write checkers that fail closed on unresolved inputs, empty lists, and shell word-splitting.** Under zsh, an unquoted newline-joined variable does not word-split, so both inline diff idioms below match no pathspec, print nothing, exit 0, and falsely report a landing:

```sh
FILES=$(gh api ".../files" --jq '.[].filename' | tr '\n' ' ')
[ -z "$(git diff origin/dev "$SHA" -- $FILES)" ] && echo LANDED
```

```sh
files=$(git diff --name-only <head>~1 <head>)      # newline-joined
git diff --name-only origin/dev <head> -- $files   # empty => landed
```

The desk verifies that an SHA exists before diffing against it, asserts that a file list is non-empty, and checks exit status instead of output emptiness. `grep -c` prints `0` and exits 1 on no match, so `c=$(... | grep -c X || echo 0)` produces `0\n0` and makes `[ "$c" = 0 ]` false; `$(cat file || echo "$VAR")` fails when `file` is empty instead of missing.

| Fragile pattern | Required replacement |
|---|---|
| Unquoted `$FILES` or `$files` in zsh | Null-delimited `xargs -0`, one path per loop call, or zsh array `${(f)files}` / `${=FILES}` with length asserted against the PR file count |
| `grep -c X \|\| echo 0` for presence | `grep -q` for presence, or structured field parsing that prints what was counted beside the count |
| `export TOK=$(cat missing-file)` or `$(cat file \|\| echo "$VAR")` | `${VAR:-$(cat file)}` |
| Unbounded assumption on paginated API lists | Loop with `--paginate` until a page returns empty; never stop at 30 files, 30 builds, or 4 pages of 100 artifacts |
| Returning an empty set on HTTP `429` | Back off on `429` inside the shared fetch, pace re-runs, and assert non-empty derived sets |

**Inspect the instrument when independent subjects fail or pass identically.** When four PRs fail with the same HTTP `404` or scan miss, or when independent consecutive landing builds fail on the same job, the desk stops grading individual heads. It fixes the grader input or walks the build list back to the last pass to date the single breaking commit.

**Audit the newest passed landing build whenever a grading defect is found.** The desk pulls every `plan/apply-<stack>.json` from the latest passed landing build and counts stacks with `"passed": false` to see whether the broken instrument already let a refused change through. It also keeps watching verdict artifacts positively when a widened bar makes the refused column mostly empty.

**Verify a bar or guard change through the fields it writes into verdict artifacts.** A guard PR has no plan surface, so a green build only proves compilation. When a bar change adds an op field such as `belowVerified`, the desk compares a stack's verdict artifact across the old and new engines and checks for a non-degenerate `true`/`false` distribution.

**Verify constants that describe the outside world against two independent upstream sources.** A unit test in the same repository that asserts `COUNT = 3` only pins the constant to itself. For wire codes, API enums, headers, severities, and resource types, the desk requires citations from two independent upstream sources, such as vendor TypeScript and Go clients, in the PR body.

## Grade plans and applies

**Grade the uploaded `plan/<env>-<domain>.json` artifact, and distinguish cache pruning from an empty plan surface.** The desk's default bar grades the plan artifact and never grades the lane's reported op counts. `infra-report` is a cache unit over `INFRA_CHECK_READS`, while `INFRA_PACKAGE_READS` excludes the CI package except for individually named files such as `modes.ts`. The desk checks the build's uploaded artifacts directly even when no PR plan comment folded, as on adoption-record diffs. It refuses a missing `infra-report` build only when the diff touched a named read, and treats a build that ran and uploaded zero verdicts as positive proof of no plan surface.

**Report "no plan surface, the landing decides" when a change schedules no preview.** When a diff schedules no plan or a retirement gate runs before apply and cannot see post-apply tag residue (`beyondTags` in `infra/lib/retire.ts`), green tests and reviews do not prove the remedy. The desk notes the unverified surface in the ledger row and label message, preserves the lane's word `probe` when flagged, and assigns two independent readers to the landing build.

**Read a job's own output instead of the build color, and track three numbers on red builds.** Jobs are per-stack and independent, while `assertPlanned` turns one stack's hard preview error into a whole-build failure. A passing job in a failing build still satisfies a stack gate if the failure shares no stack, domain, credential path, or cause:

```
:pulumi: land plat-usw2-relay-dns   state=passed  soft_failed=false  exit=0
dns/plat-usw2-relay: same=6
```

The desk tracks refused stacks, stacks with no verdict artifact, and the overall build state. A stack has no verdict artifact when an env at `adopting` or `verified` in the ledger is missing from the build's verdict artifacts. The desk reports a hard plan failure as "not gradeable" instead of "clean."

**Put the `.apply.yaml` enablement flip in the same PR as the rows it enables, and read preconditions from the graded commit.** A stack whose sidecar reads `pending-owner` and that lacks backend state exits 0 with zero ops and an empty digest:

```ts
if (written.skipped !== undefined) {
  invariant(written.skipped === PENDING_OWNER, ...)
  return { stack, outcome: "pending-owner" }
}
```

The desk distinguishes `pending-owner` from a plan that errored. `could not be previewed` with a real error is evidence of a problem and requires a hold, while `pending-owner` is evidence of nothing and requires no hold. A held stack that already has state in the backend previews normally, so the desk checks the backend before blaming the hold and never cites the hold to justify flipping a stack that has state. The desk grades an enable PR both as written and for what its `.apply.yaml` change arms, reading the sidecar from the plan artifact's `commit` and never from `origin/dev`. When a no-plan PR is unavoidable, the desk uses the fallback bar of green CI, `ai-review` `success`, and no `.apply.yaml` diff.

**Run the import-class bar only on `:pulumi: land` jobs, never on `:pulumi: apply` jobs.** Stacks whose infra-landing job verb is `apply` (such as `core-gbl-auto-buildkite`, `core-usw2-auto-ci`, and `plat-use1-prod-ci`) do not run `landingAdmits`, even if the PR plan comment claims the landing refuses the change. The desk derives the apply-path set from live job names and fails loudly if the derived set is empty.

**Read `plan/apply-<stack>.json` and the post-apply boolean from the full-SHA landing build.** A `:pulumi: land` job exits 0 even when `plan/apply-<stack>.json` records `"passed": false` and a `refused` array, and a plan taken while an apply is in flight shows pre-apply drift. The desk queries the landing build by full commit SHA because the short form returns zero builds, which reads as no evidence instead of a bad query. It then reads `<stack>: applied; post-apply preview all same` and distinguishes the pre-apply `ops` census from the post-apply boolean:

| Verdict `ops` table | Post-apply boolean | Meaning |
|---|---|---|
| Any non-`same` op | `true` | The apply ran and a post-apply re-preview confirmed convergence |
| All `same` | `true` | The engine short-circuited with nothing proposed and took no after-preview |
| Any op refused | `false` (`"passed": false`) | The whole stack was skipped; admitted ops in the same stack did not apply either |

**Grade per-resource URNs, classifier pins, and unfiltered provider lists instead of summary counts or digests.** Two plans can share identical op censuses while moving completely different URNs. The plan `digest` filters out `pulumi:providers:` ops, so planner-role CI plans and writer-role hand plans match on digest while differing on provider updates. When a lane's counts and the artifact's counts differ only on `pulumi:providers:` entries, that difference is expected; the desk never argues a lane's counts down from a digest match and compares provider ops by reading both lists. When an admitted op lists a create-time key in `diffs`, the desk checks the branch's adoption record for an `ignoreChanges` pin before refusing. It reports any stack refusal as the entire pending op census instead of the single offender.

**Require explicit resource names in every synthesized Pulumi project and reconcile created resource counts on preview errors.** `infra/engine.ts` sets `pulumi:autonaming: value: mode: disabled` (asserted in `infra/engine.test.ts`), so every resource needs an explicit name property such as `name` or `statementId` on `aws.lambda.Permission` (while `aws.lambda.FunctionUrl` has none). Because a failing preview stops at the first unnamed resource, the desk compares the creates reported in the log against the creates declared by the row to find all un-rendered resources.

## Grade deletes and access

**Clear `protect` and `retainOnDelete` in state before running a preview to grep for `[retain]`.** A resource carrying `protect` aborts the preview before any op list is printed:

```
error: Preview failed: resource "<urn>" cannot be deleted because it is protected.
Resources: 32 unchanged, 3 errored
```

The lane edits state first, re-previews, and greps the resulting preview for `[retain]`. A landing shows the same abort when an environment's rows leave while protected state entries remain, so the desk checks for `protect` when triaging a stuck `land` job before assuming drift or a bar refusal.

**Land row removals before or with a live environment destroy.** A landing build treats an enabled stack with no state as an import and fails in preview if the live resources are gone while the rows remain:

```
aws:ec2:Vpc  <env>  import error: Preview failed: resource 'vpc-0c47b1e6127bcc069' does not exist
pulumi:pulumi:Stack  network-<env>  create error: preview failed
```

When `land` jobs fail with `does not exist` on import across an environment, the desk tracks recovery by counting how many of that environment's jobs remain as row-removal PRs land.

**Grade a delete by its provider and child ops, and check internal totals before reporting.** A Pulumi component shell delete carries no `provider` and `retained: null` because there is no cloud object to retain. The desk treats a `delete` op with no `provider` as state-only, checks whether any child resource in the plan also plans `delete`, and cross-checks its own total `delete` op count across stacks before raising an alert.

**Check both literal file readers and surviving stack adoption records before labeling a retirement.** Cached test units do not track files opened at run time, and removing adoption records can orphan live resources on the next apply. Before labeling a retirement, the desk greps the PR's own head tree for every deleted file path. This grep check matches only literal paths and cannot see a test that asserts on a name derived from a deleted entry, so the desk still reads a hard red of that shape from the build. The desk also checks that the surviving stack's plan still adopts all claimed live resources, and prefers moving test fixtures into the test tree over deleting security-relevant tests. Temporal boundary and scope overlap together are strong evidence, but they are not a mechanism. The desk names them as what they are, asks the owning lane the question that decides the fix, and never narrates a causal chain from a deleted directory to an orphaned resource until someone has read one.

**Run `git log -L` on any op that touches a trust or policy document, and escalate access removals to the owner.** An `update` op that narrows trust, permissions, or policy statements looks identical whether it converges a hardening change or reverts one. The desk derives the document-input key set directly from the map the engine already uses instead of hand-copying one, and matches on the key alone instead of the key-plus-type pair the rewrite check uses. It triggers a provenance check whenever an op's `diffs` array names a key from that set or a principal/permission watchlist:

```sh
git log -L <line>,<line>:<file> <trunk>
```

The desk reads the introducing commit itself and routes any access-removing plan to the owner instead of labeling it.

**Check IAM policy grants and reader necessity when a docs-only diff names a secret path.** Prose diffs schedule no plan, but an instruction placing a secret on a shared prefix can expose release gates to CI instance profiles. The desk reads the policy file and queue policy list to verify who can read the prefix, while the owning lane verifies which jobs need read access.

## Prove landings and manage the queue

**Prove a landing with a positive trunk read, using tree equality only as corroboration.** Forge PR pages (`closed` with `merged: false`), bot closing actors, `externally-merged` or queue merged labels, and immediate post-label checks cannot prove whether a PR landed. After verifying `git rev-parse --is-shallow-repository` is `false`, the desk requires an added file existing on the trunk (`git cat-file -e origin/<trunk>:<new-path>`), a deleted directory absent (`git ls-tree -d origin/<trunk> <dir>`), a pathspec-immune ancestry check (`git merge-base --is-ancestor <head> origin/<trunk>`), or a squash commit on the trunk:

```sh
git log origin/<trunk> --oneline -400 --fixed-strings --grep="(#<n>)"
```

When a stacked child lands its parent's payload and leaves the parent to close as a no-op with no commit number, the desk corroborates with a null-delimited two-dot diff over the PR's own files. It never uses a three-dot diff because three dots diff against the merge base and show the payload as present on the branch side regardless of what the trunk received:

```sh
files=$(gh api "repos/<repo>/pulls/<n>/files?per_page=100" --jq '.[].filename')
printf '%s\n' "$files" | tr '\n' '\0' |
  xargs -0 git diff --numstat origin/<trunk> <head> --      # empty means it landed
```

Tree equality proves a landing, while a difference or a head blob missing from trunk history never proves the absence of one from content alone. If the evidence is not terminal, the headline says unknown or waits. When a label was pulled or just applied and the positive checks have not settled, the desk reports "in flight, outcome unknown" and never re-labels until the content check completes.

**Read the queue's draft twin and the "Merge activity" comment to see queue state.** On Graphite, `graphite-app[bot]` removes the `merge` label 15 to 208 seconds after application on pickup, while on Trunk, `trunk-io[bot]` keeps `merge` on landed PRs, and removes it on failure with `trunk-failed`. The desk checks the `[Graphite MQ] Draft PR GROUP:* (PRs <n>)` twin's `buildkite/test` run and the PR's "Merge activity" comment, which posts under a human account instead of the bot:

| Queue observation | Meaning and desk action |
|---|---|
| `added this pull request to the merge queue` (or green draft twin) | Enqueued or merged |
| `couldn't merge this PR because it had merge conflicts` (or no twin created) | Ejected before or at pickup; route a rebase to the owning lane |
| Draft twin `buildkite/test` red (including `Render and upload the pipeline`) | Ejected on test or semantic conflict; read the failing job, stack PRs that append to the same file into one batch, never label them one at a time, and stop committing the file when it is a generated output |
| `detected` line with no `added` line after 20 minutes | Wedged; run the 4-step un-wedge sequence below |

**Un-wedge a PR that has no `added` line 20 minutes after labeling.** The desk runs these steps in order and stops at the first that works, escalating to the owner only if all fail:

1. Check `mergeable_state` and commit distance behind trunk; if `dirty` or stale, pull the label and route a rebase onto the trunk tip (never push an empty commit).
2. Re-request the queue's mergeability check run over REST (expect HTTP `404` when another GitHub App owns the check).
3. Pull the label, re-add it once, and wait five minutes.
4. Pull the label and ask the owning lane to rebase onto the trunk tip and re-record the Graphite parent.

**Enqueue the largest green, approved, unheld bottom prefix as one batch and protect open children above it.** If a parent lands with an untracked open child, the queue deletes the parent branch and GitHub permanently closes the child:

```
state cannot be changed. The <branch> branch has been deleted.
```

The desk never waits for the top of the stack to go green before enqueueing a green bottom prefix. `.agents/skills/submit-pr/scripts/stack-enqueue` takes no hold flag and reads no holds, and its gate can refuse the prefix too, so the desk checks holds and every guard across the prefix first. Retargeting alone does not rebase a child and leaves its parent's commits in place until rebased, so the desk checks the file count on retargeted children before labeling. The enqueue checks on children and adapters are D3 in landing.md.

After the prefix lands, the desk checks whether native GitHub stacking already rebased the remaining stack. If not, the desk routes a restack of the first PR above the prefix to its owning lane by using `lr relay <lane> <text>`, which the lane runs on a Sprite with `lr landing restack` or `ccx vcs stack submit`. Before declaring a closed Graphite child lost, the desk resolves the branch SHA from the remote instead of the dead PR's stale recorded SHA, confirms the head branch still exists on the remote and merges cleanly into trunk, and checks whether it already contains the parent's squash. It then recovers the child by rebasing the remote branch tip onto `dev` and opening a replacement PR with `gt track --parent dev`.
