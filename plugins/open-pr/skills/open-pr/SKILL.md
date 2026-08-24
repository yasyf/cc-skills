---
name: open-pr
description: Open a pull request that reads like the repo's own maintainers wrote it — pick a depth first, where an express lane for "just open the PR" or "just ship it" orients, writes, and ships without the research passes, and the full lane orients with `ccx vcs lane` and `ccx vcs guidelines`, learns the house style from a bot-filtered sample of recent human-authored merged PRs via a cached per-repo style card, calibrate register against a local wlm voice profile, run a politeness and standards-conformance pass with issue-first handling on repos the user does not own, write the title, body, and commit messages to the repo's own conventions, ship through `ccx vcs ship`, and hand the CI and bot-comment watch to a background pr-watcher agent. Use when opening, submitting, or contributing a pull request, drafting a PR title or body, contributing a change to an open-source repo, or shipping a branch as a PR.
allowed-tools: Bash(ccx:*, gh:*, jq:*, wlm:*, slop-cop:*, mktemp:*, bash:*, git:*), Read, Write, Edit, Glob, Grep, Agent, AskUserQuestion, SendMessage
---

# Open a PR

Opening a PR well is a research problem before a git problem. A reviewer decides in the first screenful whether the author knows the repo — the title grammar, the body shape, the commit subjects all signal it — so the work is: learn what this repo's maintainers write, then write that.

## Pick the depth first

**Express** is for "just open the PR", "just ship it", "get this in" — and for any change small enough that the research would cost more than the review it saves. Orient, write, ship, hand off the watch. Skip the style scout and its card, the voice calibration, and the prose gate. Do not ask whether express is wanted: the phrasing already said so, and confirming it is the delay the user was avoiding.

```bash
ccx vcs lane --json
ccx vcs diff
ccx vcs ship -m "<subject>" --no-watch --pr-title "<title>" --pr-body-file "$BODY"
```

The body still says what changed and why, in the repo's template when it has one, and the title still matches the grammar of the last few merged PRs — one `gh pr list --state merged --limit 5 --json title` read, not a scout. Express drops research, never the body: a bodyless PR is the one thing no lane ships.

**Full** is the rest of this file, and it earns its cost on a repo you do not own, on a change a maintainer has to be persuaded of, or the first time you open a PR anywhere. On a foreign repo the conformance and politeness passes are not optional even in express — a guest who skips them wastes the maintainer's round trip, not their own.

## Orient

The path matters here: three commands whose named output everything downstream references.

```bash
ccx vcs lane --json          # lane, branch, trunk, dirty, github.mine, downstack PRs and which lack bodies
gh repo view --json isPrivate,owner,viewerPermission,pullRequestTemplates,nameWithOwner,defaultBranchRef
ccx vcs guidelines           # CONTRIBUTING, PR template, code of conduct, issue config — prints the card path
```

Then `ccx vcs diff` for the change itself.

`ccx vcs lane` reports the lane — it doesn't choose it, and neither do you. `ccx vcs ship` selects jj, git, or gt at ship time, and a repo the user doesn't control silently falls off the gt lane, so write nothing that assumes a lane before ship reports one. `github.mine` is the ownership verdict; the test behind it and everything downstream of `mine: false` live in [reference/oss.md](reference/oss.md). Read the card `ccx vcs guidelines` wrote, then separate hard requirements from preferences — [reference/guidelines.md](reference/guidelines.md) has the tells, and the `gh api` fallbacks for a repo that publishes nothing.

## Learn the house style

The house style lives in a card on disk, scouted once per repo and cached:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-cache.sh" status <owner/name>   # fresh | stale:<reason> | missing
bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-cache.sh" path <owner/name>     # prints the cache dir
```

`fresh` needs no scout — read the card and move on. `stale` or `missing` → spawn the scout, once per repo per run:

```
Agent(subagent_type: "open-pr:pr-style-scout",
      prompt: "repo: <owner/name>\ncache: <dir>\nviewer: <gh login>")
```

The scout samples recent merged PRs, filters out bots, AI-written PRs, and — on a foreign repo — the viewer's own, and writes the card to the cache dir. It returns a path, a short summary, and a confidence — read the card it wrote, not its narration.

The card carries its sample size and a per-axis confidence, because the filters that keep the sample human also shrink it. Title grammar and body structure are conventions — they hold at any sample size. Review-response tone and prose register rest on fewer signals: at `confidence: low` they're a lean, not a rule, and the PR template and guidelines carry more weight. A convention read off three samples described those three. Cache layout, card schema, and staleness live in [reference/style-cache.md](reference/style-cache.md).

## Calibrate the voice

When the user has a local wlm profile, borrow the parts of their voice that survive the format change:

```bash
wlm profile list
wlm -p <profile> stylecard show    # -p comes before the subcommand and is not optional
```

The wlm card was built for long-form blog prose: the Never list and word choice transfer whole, tone at half strength, length and openers not at all. The full transfer table and the no-profile fallback are in [reference/voice.md](reference/voice.md). Where the repo's card and the voice card disagree, the repo wins — you're a guest in their format.

## Repos you don't control

`github.mine: false` changes the register and adds two passes before writing ([reference/oss.md](reference/oss.md)):

- **The politeness register** — direct about the change, deferential about the decision: numbers carry the claims, the maintainer keeps the calls that are theirs.
- **Standards conformance** — walk `ccx vcs diff` against the guidelines card and the checklist: scope, style, tests, docs, commit hygiene, diff size against the repo's merged-PR norm. Each miss is a fix, or a sentence in the body owning the deviation.
- **Issue-first** — when the guidelines card marks an issue as a hard requirement, search before opening: `gh issue list --repo <slug> --search "<keywords>" --state all`. No match → draft the issue, then put the draft in front of the user with `AskUserQuestion` — open as drafted / edit first / link an existing issue / skip the requirement deliberately — and act on their pick. Opening an issue publishes under the user's name on someone else's project; drafting doesn't.

## Write

Title, body, and commit subjects follow the style card at its stated confidence; the PR template, where one exists, is the body's skeleton. A number wherever an adjective would otherwise grade the change: a reviewer verifies "40ms off cold boot" and discounts "much faster".

<examples>
<example label="oversells">
"This PR massively improves config loading! Now it's way more robust."
Grades itself with adjectives the reviewer can't verify, in delight the format doesn't hold.
</example>
<example label="lands">
"Config loading no longer re-reads the file per key — one read at startup, 40ms off cold boot."
States the change and its measured effect; the reviewer verifies instead of trusting.
</example>
</examples>

Write the body to a file, never a shell argument — a body with backticks, `$`, or a code fence doesn't survive a command line intact:

```bash
BODY=$(mktemp)
```

The body is prose, so it takes the prose gate before it ships: `slop-cop check "$BODY" --lang=markdown`, triaging the flags rather than accepting all of them.

Commit messages get the same treatment: the card records this repo's subject grammar, its length, and which trailers its own merged commits carry — match those trailers and no others. On a repo whose history has no `Co-Authored-By: Claude` or `Generated with Claude Code`, a trailer announcing the tooling is the single line that makes a careful PR read as automated, and maintainers filter on it; on a repo whose history does carry them, keeping them is the convention.

## One stack per session

Related work from one session goes in one chained stack, never independent
siblings off trunk — even when the diffs are provably disjoint. "The files
don't overlap" is not a reason to split: siblings erase the review order a
chain states, and forfeit the mid-stack machinery — one restack carries an
amend through every branch above it, where siblings each rebase alone.
Chain in dependency order, what consumes above what it consumes; a lane
still in flight goes at the tip, so the finished branches below it never
get re-rebased under its churn. Mechanics in
[reference/stack.md](reference/stack.md).

## Ship

The flag combination is load-bearing:

```bash
ccx vcs ship -m "<subject>" --no-watch --pr-title "<title>" --pr-body-file "$BODY"
```

`--no-watch` because ship's built-in watch blocks until CI concludes — right when you're waiting, wrong when the watch is about to be handed off. Ship owns PR create-and-update in every lane: a hand-rolled `gh pr create` alongside it produces a PR the gt lane cannot reconcile with its own stack.

On the gt lane one submit can create several PRs, and every PR in the submit that has no body yet gets one. `--pr-title` and `--pr-body-file` repeat and scope by branch (`--pr-body-file <branch>=<path>`; a bare value applies to the tip); `ccx vcs lane --json` is what tells you which downstack PRs are bodyless.

A refusal is information, not a retry: `nothing to commit` means a prior ship already landed this; a gt `needs_restack` means `ccx vcs stack restack` first, then re-run.

Ship reports the branch, the PR number and URL, and the head sha. The handoff needs all four.

## Hand off the watch

```
Agent(subagent_type: "open-pr:pr-watcher", run_in_background: true,
      name: "pr-watch-<number>",
      prompt: "pr: <number>\nurl: <url>\nrepo: <owner/name>\nhead: <sha>\n"
              "branch: <branch>\nlane: <gt|jj|git>\ncache: <dir>\nownership: <mine|foreign>")
```

One watcher per PR — a second on the same PR would push over the first. The watcher sends exactly one message as its last action and stops, so a watcher that has reported is idle, not dead; `SendMessage` by name resumes it for the next round. Its report arrives as a notification mid-task — keep working until it does. When it reports a judgment call, it brings 2–4 options: route them to the user through `AskUserQuestion`, because the watcher runs with no user attached — that's exactly why it hands the question to you.

<success_criteria>
The PR is open when ship has reported branch, PR number, URL, and head sha, and exactly one watcher holds the watch. It's done well when the title, body, and commit subjects match the style card at its stated confidence; every hard requirement in the guidelines card has a disposition — met, or skipped by the user's explicit pick; the body fills the repo's template and passed a triaged slop-cop run; and on a foreign repo, the diff cleared the conformance pass before the body was written.
</success_criteria>

## Reference

- [reference/guidelines.md](reference/guidelines.md) — the guidelines card: what it aggregates, hard requirement vs preference, `gh api` fallbacks.
- [reference/style-cache.md](reference/style-cache.md) — cache layout, `style.md` frontmatter schema, staleness rules.
- [reference/voice.md](reference/voice.md) — the wlm transfer table and the no-profile fallback.
- [reference/oss.md](reference/oss.md) — ownership matrix, politeness register, CLA/DCO, the conformance checklist.
- [reference/stack.md](reference/stack.md) — one stack per session: chain order, sibling costs, mid-stack amend mechanics.
- [reference/merge-state.md](reference/merge-state.md) — merged vs abandoned on a queue lane: the closer-actor recipe and the trunk-history confirm.
- [reference/worktrees.md](reference/worktrees.md) — worktree isolation for parallel PR lanes.
- [reference/rebase.md](reference/rebase.md) — restack and rebase mechanics: conflicts and keeping the stack current.
- [reference/checks.md](reference/checks.md) — reading check state: buckets, required vs optional, rerun semantics.
