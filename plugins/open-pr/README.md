# open-pr

Reviewers decide in the first screenful whether you know the repo. `open-pr` learns the repo's title grammar, body shape, and commit conventions from recent human-authored merged PRs, opens the pull request ready with its body, and watches CI and review comments in the background.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install open-pr@skills
```

Prerequisites:

- `ccx`, the GitHub CLI `gh`, and `jq` on `PATH`
- `captain-hook` plugin (`>=12.71.1`) from the `captain-hook` marketplace
- Optional: a local `wlm` voice profile for register calibration, `slop-cop` for prose gating on the PR body, and the `cc-review` plugin to open the PR for review

## Use it

When you are ready to submit a branch to a repo with its own conventions or an upstream project you do not own, ask:

```text
Open a PR for this branch
```

`open-pr` reads the repo's contribution guidelines and PR template through `ccx vcs guidelines` and scouts a cached style card from recent human-authored merged PRs. It calibrates register against your local `wlm` voice profile, while the repo's style wins on conflicts. It writes the title, body, and commit messages to match house conventions, including commit trailer history, and omits `Co-Authored-By: Claude` when the repo has none. On repos you do not own, it checks the diff against contribution standards. It handles issue-first requirements by searching for or drafting an issue and asking for confirmation before publishing under your name.

It ships through `ccx vcs ship` across native GitHub stacks (`gh-stack`), `jj`, `git`, or legacy Graphite `gt`, filling bodies on any bodyless downstack PRs, and spawns a background watcher. Once the watcher reports the PR ready to merge, a line in your `CLAUDE.md` decides the next question. `open-pr.on-ready: offer-open`, the default, asks whether to open the PR page. `open-pr.on-ready: offer-merge` asks "merge #N now?" and enqueues or adds the repo's queue label only on a yes.

When you want to skip style scouting on a routine change and still ship a complete PR description, ask:

```text
Just ship it
```

The express lane skips the style scout, voice calibration, and prose gate. It writes what changed and why using the repo's PR template when present and matches the title grammar of recent merged PRs. It then ships through `ccx vcs ship` and hands the PR off to the background watcher.

When an existing PR has failing checks or pending bot comments and you want it driven to green, ask:

```text
Fix CI on PR 123
```

`open-pr:pr-loop` attaches to the open PR and iterates until CI is green and bots are quiet. Four tree-safety gates require a clean working copy, a local head matching the PR head, touched files already in the PR diff, and fewer than two recorded attempts at that check. Behind those gates, it applies fixes determined by the failure itself: formatter output, single-resolution compile and type errors, stale lockfiles and snapshots, review suggestion blocks, and bot nits that name the exact replacement. It ships each fix through `ccx vcs ship`, rebuts bot findings that the code refutes, and brings everything else back to you with two to four concrete options.

## What's inside

| Component | Kind | What it does |
| --- | --- | --- |
| `open-pr:open-pr` | Skill | Drafts and ships a PR matched to the repo's house style and guidelines, with express and full lanes, then starts a background watcher. |
| `open-pr:pr-loop` | Skill | Attaches to an open PR and iterates on CI failures and bot review comments behind four safety gates until the PR is green. |
| `open-pr:pr-style-scout` | Agent | Reads roughly the 50 most recent human-authored merged PRs in its own context and writes the repo's style card to the per-repo cache. |
| `open-pr:pr-watcher` | Agent | Watches an open PR in the background for CI checks, reviews, bot comments, and merge queue state, applying the fixes the failure itself determines and reporting status changes. |
| `watcher_bash_guard` | Hook | `captain-hook` hook that keeps `pr-watcher` polls running in foreground short-window form so the background watch keeps waking. |

Part of [cc-skills](../../README.md).
