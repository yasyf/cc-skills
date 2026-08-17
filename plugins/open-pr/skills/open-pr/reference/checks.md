# Reading checks and PR state

Every hazard here has the same shape: a read that returns something well-formed and wrong, with no error to prompt a second look. Absence of data and absence of failures are the same shape in jq — force them apart before deriving anything.

## An empty response reads as all-clear

`gh pr checks` and `gh api graphql` spend a GraphQL budget separate from REST core: a watcher polling a handful of PRs every 45s burns it inside an hour while `gh api rate_limit` still reports core nearly full and healthy. Exhausted calls return empty, `select(.bucket=="pending")` counts 0, and an "all settled" condition fires at the exact moment the watch is blind. Validate the payload before deriving anything (`jq -e .` on the raw response), emit an explicit `QUERY-FAILED` per item instead of `|| continue`, and never write a terminal condition a failed fetch can satisfy. One call per PR per tick, 90s or more between ticks for a stack.

## Keychain credentials fail silently off the desktop

CLI tokens live in the OS keychain, not in config files — `~/.config/gh/hosts.yml` is effectively empty on a fully authenticated machine. Any tool that reads a config file to find the token silently falls back to anonymous requests and the 60/hour budget. Probe the tool, don't inventory its files: one real API call settles it.

`gh` itself degrades the same way mid-session: every write starts returning 401 while `gh auth status` reports a healthy keyring token and `gh auth token` prints a valid one. It failed to unlock the keyring non-interactively and fell back unauthenticated — reads work, writes 401, and it looks like an SSO or permissions problem. The tell is `gh api rate_limit` reporting `limit: 60` instead of 5000. The fix is re-injection, not re-login: `export GH_TOKEN="$(gh auth token)"`.

Background and detached contexts can't unlock the keyring at all, and the failure is silent by default: one 12-hour watch emitted zero events while its PR stack went red, every `gh` call failing into a swallowed `|| continue`. Open every `gh`-shelling watch with one real call as an auth probe that exits loudly on failure — sometimes the keyring does unlock, which is exactly why the probe is load-bearing rather than a formality.

## One page of check runs is not the check state

Workflows triggered by `issue_comment` carry no head SHA, so GitHub pins those runs to the **default-branch tip** — on a busy repo with comment-triggered workflows, the commit at tip collects hundreds of check runs within hours. The check-runs API returns newest first with `per_page` defaulting to 30, so a check that passed early sinks past page 1, and a single-request reader reports a green check as missing. It behaves like a clock, not a switch: the same commit reads green minutes after push and "missing" hours later. Filter server-side and paginate; `per_page=100` alone is not a fix:

```bash
gh api --paginate "repos/<owner>/<name>/commits/<sha>/check-runs?check_name=<context>&per_page=100" \
  --jq '.check_runs[] | [.name, .status, .conclusion] | @tsv'
```

`/commits/<sha>/status` (the combined-status endpoint) defaults to 30 too and needs the same treatment — and it hides all but the latest status per context, so when delivery history matters, read the plural `/commits/<sha>/statuses`.

## A status that hasn't arrived is lag, not a missing build

"Expected — waiting for status to be reported" while the external CI shows the build green means status *delivery* is lagging — statuses land in clumps under load, and the gap can reach tens of minutes. Diagnose by comparing the build's finish time against the status's `created_at` on the plural endpoint (`gh api repos/<owner>/<name>/commits/<sha>/statuses`), and check the CI's own queue-wait numbers separately before believing a capacity story — adding workers for a delivery symptom widens nothing but the bill.

## A job that never got a runner looks like a failure

A GitHub Actions job failing at ~15m with "The job was not acquired by Runner ... even after multiple attempts" never ran: `gh run view --log-failed` returns nothing, inviting a debug of code that was never compiled. The discriminator is step count, since the `cancelled` conclusion alone doesn't distinguish "ran and failed" from "never started":

```bash
gh api repos/<owner>/<name>/actions/jobs/<job-id> --jq '"\(.conclusion) steps=\(.steps|length)"'
```

`cancelled steps=0` → rerun with `gh run rerun <run-id> --failed`; "already running" from that command is success, not an error. A whole stack failing identically at the same duration is one capacity incident, not N regressions — gate alerts on `steps>0` and rerun `steps=0` silently.

## Prefixed log lines defeat anchored greps

CI log downloads often prefix every line (Buildkite's `_bk;t=...` timestamps, for one), so `grep '^--- FAIL'` matches nothing on a red build and reads as a clean run — the same false-pass shape as the empty GraphQL response. Grep unanchored (`grep -- '--- FAIL'`) and confirm the pattern *can* match before trusting a zero.

## Graphite: pending forever, green having run nothing

`Graphite / mergeability_check` sits `pending` indefinitely on a stacked PR whose parent hasn't merged — it waits on the downstack, not on CI, and GitHub reports `mergeStateStatus=UNSTABLE` while every real check is green. A watch gated on "no pending checks" never fires on an upstack PR; gate on "every non-Graphite check settled" instead.

Graphite's CI optimizer runs the real pipelines only on a stack's **tip**: every intermediate PR shows its CI check green having executed nothing. Since Graphite merges bottom-up one PR at a time, an untested middle PR can land a commit on trunk that doesn't compile, its own PR green throughout. Distinguish a real run from a skip by opening the build the check links to — a skip contains only the optimizer's own step, no real jobs — or by checking whether the downstream pipeline ever built that branch at all: "never ran here" beats "this build was thin". The skip keys on the branch having an open PR, so to force a real build on a mid-stack SHA, push that exact SHA to a PR-less scratch ref and delete it after:

```bash
git push origin "<sha>:refs/heads/scratch-ci-<name>"
git push origin --delete "scratch-ci-<name>"
```

Judge a stack by its tip's build or the merge queue's speculative build, never by intermediate badges; when a stack merges PR-by-PR rather than atomically, coverage doesn't transfer, and a defect belongs fixed in the commit that owns it, verified to build standalone. One sound exception: a branch whose tree is byte-identical to a SHA that already got a full run.

## Closed is not abandoned

A merge-queue squash-merge (Graphite's included) leaves the PR reading `state: CLOSED` with `mergedAt: null` and no `mergeCommit` — in one measured repo, 58 of the last 69 closed PRs had actually landed. Reading state alone reports a merge as an abandonment; the close event's actor and a suffix-anchored trunk grep settle it, per [merge-state.md](merge-state.md).

## Diffs that answer a narrower question

`git diff origin/<trunk>..HEAD` (two dots) on a branch behind trunk renders trunk's own commits as reversions the branch would perform — read that way once, it produced a false "this PR reverts 29 lines of merged work". The three-dot form diffs from the merge base and is what GitHub applies; the two coincide only on a freshly rebased branch, which is why the misread appears exactly when the branch is stale:

```bash
git diff origin/<trunk>...HEAD                       # what the PR does
git merge-base --is-ancestor origin/<trunk> HEAD     # exit 0 → current; the two forms coincide
```

On a *shared* branch, `git diff <older-base>..HEAD` silently includes every commit a peer landed after that base — diff against the peer tip you branched from, after `git log --oneline <peer-tip>..HEAD` confirms the range holds only your commits.

`git reset --soft origin/<trunk>` to squash commits the same misread: trunk moved since you branched, so the resulting commit reverts everything trunk landed in between. The tell is a diffstat full of unrelated files and a deletion count in the hundreds. Squash onto the branch's real base and read the stat before committing:

```bash
git reset --soft "$(git merge-base HEAD origin/<trunk>)"
git diff origin/<trunk>...HEAD --stat    # the file list must be only your files
```

## Absence at origin has two shapes

When claimed work is not an ancestor of origin, sweep local refs before reporting which shape it is. `git branch --contains <sha>` (or grep the content across `refs/heads`) separates an **unpushed cascade** — content on a live local branch, normal mid-integration — from a **phantom completion** no ref anywhere carries, which is the only real problem. The two are identical from origin alone, and prose built on a phantom is internally flawless (line numbers match the orphaned lineage exactly), so spot-checking text can't catch it; only ancestry can. Ask lanes for origin SHAs read back *after* pushing, never the local SHAs they pushed from, and report absence as "unpushed on branch X" or "no ref carries it" — never just "not at origin".

## A peer's report is scoped to a tree it never names

A well-specified message about landed changes reads as authoritative even when it describes a different tree entirely — six accurate claims about the sender's worktree were all false in the receiver's, and migrating on the sender's word would have produced code that couldn't compile. Before acting on any claimed change, grep each claimed symbol in your own tree. Zero of N present means a different effort, not lag — report the claim-vs-observed table upward rather than reconciling it yourself, and never write code against types you cannot compile.

The inverse holds too: a worktree path names a directory, not a branch, so finding a peer's change absent in a shared tree doesn't disprove it — the tree may be checked out on a lower branch where the file legitimately predates the change. Check what their tree has checked out (`git -C <tree> rev-parse --abbrev-ref HEAD`, or read `git -C <tree> show <their-branch>:<path>`), and report absence as "not present on the branch I can see", never "you did not do it".

A wrong address doesn't make a payload wrong either: take the list of landed changes out of any peer message regardless of its addressing and diff it against your own blast radius —

```bash
git log <your-base>..origin/<trunk> -- <your changed paths>
```

— one misrouted heads-up carried the merge that had deleted the entire subsystem the receiving PR rewrote, and two green rebases shipped before anyone noticed the fixtures were gone.
