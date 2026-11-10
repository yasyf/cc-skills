# Land a change

A builder owns its PR from the first push to the proof that the change is live, lands it the way its
repository says below, and returns `DONE <proof>`. The communicator draws his PR card from the PR
itself.

## Every PR

- One PR is one feature-sized unit; review fixes and follow-ups amend it (`ccx vcs ship --amend`). Open a
  second PR only when another job waits on part of the work or a review asks for the split.
- A stack is at most four PRs, since his card shows every PR of a stack in its own section with its
  pictures. A change that needs more is split into stacks that land on their own, or folded into fewer PRs.
- Title: `Fix:`, `New:`, `Cleanup:` or `Port:`, then what changes for a user, in plain words. A repository
  whose title check demands `fix:` or `feat:` keeps it; the card reads either.
- Body sections, in this order. His card is drawn from them: fix the body, never the card. Each is
  plain words for him: no record ids (cc-notes, cci, job or order ids), hashes, file paths or code
  identifiers; a sentence carrying a record id is dropped from his card.
  - `## The stack`: only on the bottom PR of a stack. Its first line is the stack's title, one readable
    line saying what the whole stack does; then a short paragraph explaining the whole stack in the
    Refresher's register. They head his stack card, and `drive card` refuses a stack card waiting on his
    tap without them.
  - `## Refresher`: the change explained to an engineer who knows the codebase but has not read this
    code lately: which part of the system it touches, what that part did before, and what it does now.
    It leads the PR's section of his card. The card also reads an older `## ELI5`, and a monorepo
    body's `## Context`, which its `submit-pr` template now writes in this voice.
  - `## Biggest surprise`: the one change a reviewer would not expect from the title, in one or two
    sentences.
  - `## What you'll hate most`: the one thing about this change he is most likely to dislike, said
    plainly: a cost, a behavior he did not ask for, a shortcut, a risk he would not take. A PR that
    waits on his tap draws no card without it: `drive card` refuses the card and the coordinator
    hears `EVENT refused`.
  - `## Show me`: a concrete example of the change, never a diagram alone: the real SQL, code, CLI call
    or Sand call it changes, with what happened before and after, in a fenced block tagged with its
    language (`sql`, `go`, `sh`), which his card draws as a syntax-highlighted picture; for a UI change, a
    screenshot or gif linked as a markdown image. Add a before-and-after `arch` (arch/v1 JSON) or `dot`
    diagram beside the example when a mechanism needs one. At most one short sentence of caption: the
    card shows the pictures, never prose.
  - `## Against the plan`: only on a departure: what the plan said, what you did, and why.
  - `## Port`: only on a port: the old PR it ports, linked, and anything it does past it.
  - `## His words, step by step`: each step his words in the brief name, quoted, beside the
    file and line that does it (`path:line`). The coordinator checks this list against his words
    before his card is drawn, so a step with no code returns the PR to you. It stays off his card.
- Register the PR the moment it opens: `drive pr add <url> --job <j>`, plus `--tap` when it needs his
  tap. Return `READY <url>` when only his tap is left, naming every PR of a stack bottom first
  (`READY <url> <url> …`) so he gets one card for it, and `WAITING <what>` while CI or a queue runs.

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
   A compiled tool such as cci, ccn, cc-slack, ccx, codex-ask, or cc-transcript need not wait for its
   release. Build the merge sha into your scratch directory with
   `go build -o ~/.vcache/scratch/<lane>/<binary> <main>`, taking `<main>` from the build its
   `.goreleaser.yaml` names; cc-transcript builds with `cargo build --release`. Rerun the case against
   that binary. The release reaches the plugin on its own.

## Forge-AI/monorepo

Ship through the repository's own `submit-pr` skill. Branch off `dev`; never build on, push to, or
restack his own in-flight branches. A PR lands only when all of these hold at its current head:

- every required check is green;
- an approving review from the reviewer bot (`forge-pr-reviewer`) is in force, not dismissed;
- the latest `ai-review` run passed. When it holds the PR, fix each blocking finding or accept a wrong one
  with `.agents/skills/submit-pr/scripts/pr-reviews accept <slug> --why "<why>"`; one on his design is `ASK`;
- nothing holds it: no Hold tap, no `hold` label, no conflict with `dev`;
- he tapped Approve on its card, or it is an exempt class.

**Exempt classes land with no tap.** Record one with `drive pr set <url> --exempt "<class>"` and clear it
with `--no-exempt`; the landing guard reads it. **Pure port**: a `Port:` PR that does what its old-tree PR does, line for line, with that
PR's tests, and nothing more. **Test-only**: only test and fixture files. **CI-only**: only CI
configuration and CI tooling. **Flake fix**: the whole change fixes one named flaking test, in test or
fixture code, and adds no timeout. Anything else waits for his tap.

**His tap counts once per PR.** It carries across every new head: rebase, restack, review fix, retitle.
Only a push that changes what he approved ends it: `drive pr set <url> --fundamental "<what changed>"`
clears the tap and any exemption, and puts the PR back on his card with that line. Two PRs that carry out one decision of
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

Merged is not live. Before `DONE`, prove it. A plugin or tool's installed version contains the merge, or
a compiled tool's local build of the merge sha reruns the case, as step 3 for yasyf/* repositories says.
A monorepo change's release is live on the targets it touches, shown by its card in #releases. Start a
regular release (`tools/ci release start`) only when his order covers releasing; otherwise, it rides the
next one and the job returns `WAITING release`. His order or pick to release authorizes the start only:
never pass `--order`, which answers the approve gate by itself. A gate that holds a migration, delete
or replace comes back as `ASK` with what it holds, and only his answer to that gate approves it. An
incident's own cut is the one exception, approved by its order's words as [incidents.md](incidents.md) says. Then rerun the reporter's exact case on the live build.

Return `DONE <proof>`: one line on what changed for him, and the link or version that proves it.
