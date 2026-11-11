# Land a change

A builder owns its PR from first push until the change is live, lands it as its repository requires below, and returns `DONE <proof>`. The communicator draws his PR card from the PR body.

## Every PR

- One PR is one feature-sized unit; amend review fixes and follow-ups (`ccx vcs ship --amend`). Split into a second PR only when another job waits on part of the work or a review asks for it.
- **The default branch stays releasable.** A change lands only when a release cut from the default branch at its merge commit is safe alone, with targets deploying in any order and no manual command, backfill, or follow-up migration required afterward. Split changes that need multiple phases so readers land and go live before writers, and any backfill runs automatically in code; never offer him a design option that needs a post-release step.
- Nothing lands while a question card for its change is open. Do not land, enqueue, or treat the recommended option as his answer: return `WAITING` naming the card so the coordinator puts it before him, by [Design first](design.md). `drive pr add` also refuses a PR whose job has an open question card.
- Keep a stack to at most four PRs so his card can show each PR in its own section with its pictures; split larger changes into independent stacks or fold them into fewer PRs. `drive pr set --tap` and `drive card` refuse a stack of more than four PRs.
- Title: `Fix:`, `New:`, `Cleanup:` or `Port:`, followed by what changes for a user in plain words (or `fix:` / `feat:` where a repository check requires it).
- Body sections, in this order. Fix the PR body rather than the card, because his card is drawn directly from it. Write plain words for him with no record ids, hashes, file paths, or code identifiers (sentences with record ids are dropped). Make each section stand alone without external threads, findings, or PRs: explain any mechanism in one sentence before using it, and give any number, fraction, size, or ratio its unit, reason, and one concrete example (for instance: "to open a run's page the browser fetches the last one-sixteenth of each journal file—about 6 KiB of a 97 KiB file, capped at 64 KiB—instead of always fetching 64 KiB").
  - `## The stack`: on the bottom PR of a stack only. Start with a one-line stack title, then a short paragraph in the Refresher's voice explaining the whole stack. `drive pr set --tap` and `drive card` refuse a stack card without them.
  - `## Refresher`: which part of the system the PR touches, what it did before, and what it does now, written for an engineer familiar with the codebase who has not read this area recently. It leads the PR's card section (which also accepts legacy `## ELI5` or monorepo `## Context` written by `submit-pr`).
  - `## Biggest surprise`: one or two sentences on the change a reviewer would least expect from the title.
  - `## What you'll hate most`: the cost, unrequested behavior, shortcut, or risk he is most likely to dislike, stated plainly. `drive pr set --tap` and `drive card` refuse a tap PR without it before any `ready` record is sent.
  - `## Releasable alone`: what a release at this merge commit does on its own and what users see across every target deploy order. State when nothing needs to run after release, or name only live checks (such as viewing a page) or objects the PR builds (such as a `backfill-runner` account); `drive pr set --tap` and `drive card` refuse a PR missing this section or scheduling a post-release action in its body, risk, install, or prove lines.
  - `## Show me`: a concrete visual example rendered on his card and live on the dashboard, with at most one short caption sentence and any diagram placed beside—never replacing—the example. Use a `sql`, `go`, or `sand` fence for before-and-after code or queries (never prose, JSON, YAML, logs, terminal output, or stack traces), `mermaid` or `arch` for flows, `chart` (Vega-Lite) for metrics, and a linked image or gif for UI changes. On public repositories such as `yasyf/*`, show only demo or public data under a `demo-` or `public-` path and never name a private repository's PR; `drive pr add` and `drive card` refuse public PRs that break this, and fixing a leak after opening also requires deleting prior body revisions and the image branch.
  - `## Against the plan`: on a plan departure only—what the plan said, what changed, and why.
  - `## Port`: on a port only—link the source PR and note any target-tree adaptations; any extra behavior makes it not a port.
  - `## His words, step by step`: each step from his brief quoted beside its implementing `path:line` (checked by the coordinator and omitted from his card). Drive tooling PRs write none and get their card from their own order by [Scope](scope.md); any other non-port PR without his words is not his order and returns `BLOCKED no order of his names this` instead of `READY`, or `drive card` refuses it. Never quote his words from another order that a tool fix merely unblocks.
- Register every PR as soon as it opens with `drive pr add <url> --job <j>` (adding `--tap` when it needs his tap). Return `WAITING <what>` while CI or a queue runs. When only his tap or a port check remains, return `READY <url>` (or `READY <url> <url> …` bottom-first for a stack so it draws one card) with a `SERVES:` line naming `port of #N`, `Drive tooling: <what it unblocks>`, or his quoted order.

## yasyf/* repositories

`yasyf/*` PRs merge without his tap, with the green gate off until he restores it.

1. Ship with `ccx vcs ship`; the `open-pr` skill writes the body.
2. Merge immediately with `gh pr merge <n> -R <repo> --squash --admin`. Wait only if a check fails on the PR's own change—fix, push, and merge—and never wait on mid-stack CI.
3. Verify the merge sha (`gh pr view <n> -R <repo> --json mergeCommit`) is installed, never patching an installed copy by hand:
   - Claude Code plugin: run `drive install <plugin>@<marketplace> --sha <merge sha>` (for example `long-running@skills`) to wait for the release, update, and print the version; hooks update immediately, while skills, agents, and plugin tools need `/reload-plugins` in live sessions.
   - Local Mac tool: follow the repository's release flow, install the release containing the merge, and read back the version.
   - Compiled tool (cci, ccn, cc-slack, ccx, codex-ask, cc-transcript): build the merge sha directly in scratch (`go build -o ~/.vcache/scratch/<lane>/<binary> <main>` using `<main>` from `.goreleaser.yaml`, or `cargo build --release` for cc-transcript) and rerun the case against that binary while the release rolls out.

## Forge-AI/monorepo

Ship with `submit-pr` on a branch off `dev`; never build on, push to, or restack his in-flight branches. A PR may land only when at its current head:

- all required checks are green;
- `forge-pr-reviewer` holds an active approving review;
- the latest `ai-review` passed (fix blocking findings, accept false positives with `.agents/skills/submit-pr/scripts/pr-reviews accept <slug> --why "<why>"`, or return `ASK` if a finding challenges his design);
- no Hold tap, `hold` label, or `dev` conflict blocks it;
- he tapped Approve on its card, or it belongs to an exempt class. The `landing_guard` hook denies landing calls that miss these gates.

**Exempt classes** need no tap card, land once green, and post one line in the order's thread when landed (such as "port of #N landed"):
- **Pure port**: a line-for-line `Port:` PR with the original tests and no extra behavior, verified against #N's diff by the coordinator and recorded with `drive pr set <url> --port-of <N>`.
- **Test-only**: test and fixture files only.
- **CI-only**: CI configuration and tooling only.
- **Flake fix**: fixes one named flaking test in test or fixture code without adding a timeout.
- **Incident fix**: the root fix shipped under [incidents.md](incidents.md) (never a revert, which requires his tap).

Record non-port exemptions with `drive pr set <url> --exempt "<class>"` (clear with `--no-exempt`). Everything else waits for his tap.

**His tap persists** across rebases, restacks, review fixes, and retitles. If a push changes what he approved, run `drive pr set <url> --fundamental "<what changed>"` to clear the tap or exemption and redraw his card. Fold two PRs implementing one decision into a single PR so he gets one card.

**Enqueue** green stacks together—never the bottom PR alone—using:

    .agents/skills/submit-pr/scripts/stack-enqueue <PR>...

Pass `--check` to preview gates or `--watch` to enqueue as checks turn green; after part of a stack lands, run `ccx vcs stack submit` for the remainder. Do not bypass the queue with `--admin`, `gh pr merge`, manual `merge` or `merge-fast` labels, or `tools/ci trunk submit --direct` (which requires his written ruling naming the PR and full head sha); incident fixes follow [incidents.md](incidents.md).

**Fix every red check or queue ejection on your PR.** Fix regressions your change caused, restack onto a fixed `dev` or fix forward in a separate PR for inherited reds, and fix flaky tests at their root rather than retrying with `--flake` or reruns.

**Readers first.** Land and deploy any PR that teaches readers a new stored file, row, journal, or manifest format across all clients before landing the writer PR, and link the reader PR in the writer's body.

## Read-only repositories

Customer and forward-deployed engineer repositories are read-only: clone and inspect only, never push, comment, or merge. Return `ASK` with the patch so he can merge it himself.

## After merge

Before returning `DONE`, prove the change is live on the reporter's exact case. For `yasyf/*`, verify via step 3 above. For the monorepo, confirm the release card in #releases shows the change live on its targets. If his order or card answer includes releasing, start the release (`tools/ci release start` with `--order <his answer id>`) and pass its approve gate immediately with `tools/ci release approve <id> --via <agent>`—reusing the same `--order` on any resume or re-plan—without asking him again; otherwise return `WAITING release` to ride the next release. Never approve an unapproved release, never touch a release he started himself, and handle incident releases per [incidents.md](incidents.md).

Return `DONE <proof>` with one line stating what changed for him and the version or link proving it is live.
