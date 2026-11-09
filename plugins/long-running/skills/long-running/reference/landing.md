# Land a change

A builder owns its PR from the first push to the proof that the change is live, lands it the way its
repository says below, and returns `DONE <proof>`. The driver draws his PR card from the PR itself.

## Every PR

- One PR is one feature-sized unit; review fixes and follow-ups amend it (`ccx vcs ship --amend`). Open a
  second PR only when another job waits on part of the work or a review asks for the split.
- Title: `Fix:`, `New:`, `Cleanup:` or `Port:`, then what changes for a user, in plain words. A repository
  whose title check demands `fix:` or `feat:` keeps it; the card reads either.
- Body sections, in this order. His card is drawn from them: fix the body, never the card.
  - `## What changes for you`: what a user sees change, in one short paragraph.
  - `## ELI5`: the change explained to someone who has never seen the code.
  - `## Show me`: a screenshot, still or diagram, uploaded so its markdown image link opens for him.
  - `## Risk`: only when the risk is real: what could break, who it hits, and how you would see it.
  - `## Against the plan`: only on a departure: what the plan said, what you did, and why.
  - `## Port`: only on a port: the old PR it ports, linked, and anything it does past it.
- Register the PR the moment it opens: `drive pr add <url> --job <j>`, plus `--tap` when it needs his
  tap. Return `READY <url>` when only his tap is left, `WAITING <what>` while CI or a queue runs.

## yasyf/* repositories

No tap: by his standing rule a `yasyf/*` PR merges without asking, green gate off until he lifts it.

1. Ship with `ccx vcs ship` (jj in colocated repos); the `open-pr` skill writes the body.
2. Merge at once: `gh pr merge <n> -R <repo> --squash --admin`. Wait only for a check red on the PR's own
   change: fix it, push, merge. A mid-stack PR runs no CI; never wait on one.
3. Install from the merge sha (`gh pr view <n> -R <repo> --json mergeCommit`). A Claude Code plugin:
   `drive install <plugin>@<marketplace> --sha <merge sha>` (such as `long-running@skills`) waits for the
   release that contains the merge, updates the plugin and prints the installed version. A tool this Mac
   runs: follow its repository's release flow, install the release that contains the merge, read the
   installed version back. Never patch an installed copy by hand: the next release overwrites it.

## Forge-AI/monorepo

Ship through the repository's own `submit-pr` skill. Branch off `dev`; never build on, push to, or
restack his own in-flight branches. A PR lands only when all of these hold at its current head:

- every required check is green;
- an approving review from the reviewer bot (`forge-pr-reviewer`) is in force, not dismissed;
- the latest `ai-review` run passed. When it holds the PR, fix each blocking finding or accept a wrong one
  with `.agents/skills/submit-pr/scripts/pr-reviews accept <slug> --why "<why>"`; one on his design is `ASK`;
- nothing holds it: no Hold tap, no `hold` label, no conflict with `dev`;
- he tapped Approve on its card, or it is an exempt class.

**Exempt classes land with no tap.** Record one with `drive pr set <url> --exempt "<class>"`; the landing
guard reads it. **Pure port**: a `Port:` PR that does what its old-tree PR does, line for line, with that
PR's tests, and nothing more. **Test-only**: only test and fixture files. **CI-only**: only CI
configuration and CI tooling. **Flake fix**: the whole change fixes one named flaking test, in test or
fixture code, and adds no timeout. Anything else waits for his tap.

**His tap counts once per PR.** It carries across every new head: rebase, restack, review fix, retitle.
Only a push that changes what he approved ends it: `drive pr set <url> --fundamental "<what changed>"`
clears the tap and puts the PR back on his card with that line. Two PRs that carry out one decision of
his get one card: fold one into the other first.

**Enqueue** into the Trunk merge queue with the repository's script, naming every PR that may land:

    .agents/skills/submit-pr/scripts/stack-enqueue <PR>...

It enqueues the largest green, approved, unheld bottom of the stack in one call, never an unnamed PR, and
reads the queue back. `--check` previews the gate; `--watch` keeps enqueueing as the stack turns green.
Enqueue a green stack together, never its first PR alone; once part lands, `ccx vcs stack submit` the rest.

**Never** `--admin`, `gh pr merge` around the queue, a hand-added `merge` or `merge-fast` label, or
`tools/ci trunk submit --direct` (that needs his written ruling naming the PR and its full head sha). An
incident fix lands as [incidents.md](incidents.md) says.

**Every red on your PR is yours**, whether your change or `dev` caused it. Fix your own; for an inherited
one, restack onto a `dev` that has the fix or fix it forward in its own PR. On a conflict or a queue
ejection, restack, read the entry's history and verification build, fix the cause, enqueue once more. A
test that keeps failing is fixed at its root, never retried past with `--flake` or a rerun.

**Readers first.** A PR that changes a format another build reads (a stored file, row, journal, or
manifest) lands only after the PR teaching its readers is live on every client. Its body links that PR.

## Read-only repositories

Customer and forward-deployed engineers' repositories are read only: clone and read, never push, comment,
or merge. He merges those himself. A change that belongs there comes back as `ASK` with the patch.

## After merge

Merged is not live. Before `DONE`, prove it: a plugin or tool's installed version contains the merge; a
monorepo change's release is live on the targets it touches, shown by its card in #releases. Start a
regular release (`tools/ci release start`) only when his order covers releasing; otherwise, it rides the
next one and the job returns `WAITING release`. Then rerun the reporter's exact case on the live build.

Return `DONE <proof>`: one line on what changed for him, and the link or version that proves it.
