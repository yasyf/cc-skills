# Land a change

You own your PR from its first push until the proof that the change is live. Land it the way the section
for its repository says, then return `DONE <proof>`.

- [Every PR](#every-pr)
- [yasyf/* repositories](#yasyf-repositories)
- [Forge-AI/monorepo](#forge-aimonorepo)
- [Read-only repositories](#read-only-repositories)
- [After merge](#after-merge)

## Every PR

- One PR is one feature-sized unit: amend review fixes and follow-ups into it with
  `ccx vcs ship --amend`. Open a second PR only when another job waits on part of the work or a review
  asks for the split.
- The default branch stays releasable, in his words "dev mustr always be releasable". No body section,
  card or refusal checks it, by his words "Get rid of the fucking releasable check no one asked for that
  remove it entirely".
- A PR that changes a format another build reads (a stored file, row, journal, or manifest) lands only
  after the PR teaching its readers is live on every client, and its body links that PR.
- While a question card of his on the change is unanswered, land and enqueue nothing, whatever order to
  land arrived, since its recommended option never stands in for his answer. `drive pr add` and the
  `landing_guard` hook refuse a PR waiting on his open card: return `WAITING` naming the card, which
  the coordinator puts in front of him by [Design first](design.md). An incident's repair is the
  exception: it lands on the recommended option, the card stays open, and a different pick of his
  starts a follow-up job on the same order.
- Keep a stack to four PRs at most, because his card shows each PR of a stack in its own section with
  its pictures; `drive pr set --tap` refuses a longer one and says how to split or fold it. Exempt and
  `yasyf/*` PRs, which land with no tap, are not counted.
- Title: `Fix:`, `New:`, `Cleanup:` or `Port:`, then what changes for a user, in plain words. A
  repository whose title check demands `fix:` or `feat:` keeps that prefix; the card reads either.
  `drive pr add` refuses a title the card cannot name the kind of, `drive pr set --tap` one retitled
  since, and `drive pr set --port-of` one not opening `Port:`, each giving the `gh pr edit --title`
  that fixes it.
- Write the body for him in plain words that name each function, syscall, flag, file, path, service or
  table by its proper term, never a vague stand-in, in his words "use the proper fucking terms for
  things". Leave out ids he cannot open (cc-notes, cci, job, order, or waits ids) and hashes: a sentence
  carrying a record id is dropped from his card, and `drive pr set --tap` refuses a body naming one
  outside its code and links, telling you to say what each is in plain words.
- Each section stands alone, because he reads it with no other context. A number, fraction, size, or
  ratio says what it measures, why that value, and gives one example. A mechanism gets one plain
  sentence before it is used. Nothing points at a list, finding, thread, or PR the card does not show.
  Not "reads each journal file's page index sized to the file (a sixteenth of it, at most 64 KiB)", but
  "to open a run's page the browser first fetches a small index from the end of every journal file: once
  its last 64 KiB, now its last sixteenth (about 6 KiB of a 97 KiB file), never more than 64 KiB."
- Write these body sections, in this order. Fix the body, never the card: his card is redrawn from it.
  - `## The stack`: only on a stack's bottom PR. Its first line is the stack's title, one readable line
    on what the whole stack does, then a short paragraph explains the stack in the Refresher's voice.
    They head his stack card; `drive card` refuses a stack card waiting on his tap without them.
  - `## Summary`: one or two sentences saying what the PR changes, naming each function, table, flag,
    file or service it touches by its proper term, with no background, in his words "a summary of wtf
    the PR is doing, and use exact technical terms rather than being roundabout". It opens the PR's
    section of his card. `drive pr set --tap` and a builder's `READY` refuse a PR without it; a card
    already drawn keeps redrawing without one.
  - `## Refresher`: the change explained to an engineer who knows the codebase but has not read this
    code lately: which part of the system it touches, what that part did before, and what it does now.
    It follows the Summary on his card. The card also reads `## ELI5`, and a monorepo body's
    `## Context`, which its `submit-pr` template writes in this voice.
  - `## Biggest surprise`: the one change a reviewer would not expect from the title, in one or two
    sentences.
  - `## What you'll hate most`: the one thing he is most likely to dislike, said plainly: a cost, a
    behavior he did not ask for, a shortcut, or a risk he would not take. Before any `ready` record
    goes out, `drive pr set --tap` refuses a PR without it.
  - `## Show me`: one concrete example of the change, which his card draws as a picture and the
    dashboard draws live: the real SQL, code, CLI call, or Sand call it changes, before and after, in a
    fence naming its language (`sql`, `go`, `sand`). An `arch` map of the components it adds or rewires,
    or a `mermaid` flow, goes beside the example, never in its place; numbers before and after get a
    `chart` fence (Vega-Lite) or a markdown table; a UI change gets a screenshot or gif as a markdown
    image. A fence holds code, never prose, JSON, YAML, logs, terminal output, or a stack trace. Put a
    caption line just above each fence and alt text on each image, since Slack titles each picture
    with it; a fence with none is titled by its kind, such as "go code", and a title another picture
    on the card already has gets a number, as "Diagram (2)". His card carries each `chart` fence and
    markdown table as written, and Slack draws them as its own chart and data table, so `drive card`
    refuses a chart past Slack's limits in [Messages](messages.md), a table whose header names one
    column, and a table with one value in every cell. A stack's card draws at most 2 charts in all,
    the lowest PRs' first. Open with at most one short sentence: the card shows pictures, not prose.
  - On a public repository, such as every `yasyf/*` one, pictures show only a demo drive or public data,
    under a `demo-` or `public-` file or folder name, and the body names no private repository's PR and
    no teammate, by first name, full name, Slack handle or email. The live drive, private code, Slack, or
    a customer's or teammate's data or name goes only into a private repository's PR or a direct message
    to him. `ccx vcs ship` runs the `public-pr-body` hook over the body before it commits or pushes, and
    the `public-names` hook over each line a commit adds; the `landing_guard` hook refuses such a body in
    `gh pr create`/`edit`, and `drive pr add` a PR with one. After a fix, delete each earlier revision
    and the pictures' branch, since both stay readable.
  - `## Against the plan`: only when you departed from the plan: what it said, what you did, and why.
  - `## Port`: only on a port: the old PR it ports, linked, and anything the target tree needed to run
    it. A PR with behavior of its own beyond that is not a port.
  - `## His words, step by step`: each step his words in your brief name, quoted, beside the `path:line`
    that does it; it stays off his card. The coordinator checks it against his words before his card is
    drawn, and a step with no code returns the PR to you. Quote only the order this PR carries out,
    never one whose jobs it only unblocks.
  - When the PR is not a port and this list would be empty or say none, no order of his asks for it.
    Drive tooling says none and gets its card from its own order by [Scope](scope.md); anything else
    returns `BLOCKED no order of his names this` instead of `READY`, and `drive card` refuses its card.
- Register the PR the moment it opens: `drive pr add <url> --job <j>`, plus `--tap` when it needs his
  tap.
- Return `WAITING <what>` while CI, a review, or a queue runs. Return `READY <url>` when only his tap is
  left, or for a port only the coordinator's check against #N. Name every PR of a stack bottom first
  (`READY <url> <url> …`) so he gets one card. Add a `SERVES:` line: `port of #N`,
  `Drive tooling: <what it unblocks>`, or the order of his it carries out, quoted.

## yasyf/* repositories

No tap: by his standing rule a `yasyf/*` PR merges without asking. His plan put the green gate back on
for cc-skills, whose CI takes about a minute; every other `yasyf/*` repository keeps it off
until he lifts it.

1. Ship with `ccx vcs ship` (jj in colocated repositories); the `open-pr` skill writes the body.
2. Merge with `gh pr merge <n> -R <repo> --squash --admin`. In cc-skills, merge once every check on
   the PR's head is green, which `ccx vcs ship` waits for and prints; the landing guard refuses the
   merge while one still runs or is red. When GitHub does not answer the guard's read, the merge runs
   and a note beside its output says so: read its checks once GitHub answers and fix a red one
   forward. A merge before its check ran once pushed the skill body past
   its 8,000-character cap and left main red. Any other
   `yasyf/*` PR merges at once, waiting only for a check that is red on the PR's own change: fix it,
   push, merge. A mid-stack PR runs no CI, so never wait on one.
3. Install from the merge sha (`gh pr view <n> -R <repo> --json mergeCommit`):
   - A Claude Code plugin: `drive install <plugin>@<marketplace> --sha <merge sha>` (such as
     `long-running@skills`) waits for the release that holds the merge, updates the plugin, and
     prints the installed version. A release holds it when the merge is in the release's commit and
     in the tag of each binary the plugin pins from that repo through a `*.binrun` file, since a
     binary is built at its tag, which can predate the version sync that names it (cc-inbox 0.54.27's
     sync merged after a fix its `cci` was built without). While it waits it prints a line naming
     the newest release and what of it lacks the merge. Installs run at once take turns on the
     update under one flock on `plugins/.update.lock` in the Claude config directory, a few seconds
     each, since Claude Code re-clones a stale marketplace into one fixed `<marketplace>..clone`
     directory and two clones at once delete each other's files. It refuses when the update left the
     plugin at a commit without the merge, as a marketplace clone holding moved tags does, or running
     a binary whose release tag lacks it, naming the installed version, that commit or release, and
     the merge sha. Installing long-running also restarts each drive's dashboard still serving other code, a
     line each, so no page keeps serving old code until someone restarts it; one it cannot restart names
     the `drive dashboard --drive <name>` command to run. A live session runs the installed version's
     script for each hook it registered at start, but loads a hook added since, and its skills, agents,
     and any plugin's tools, only on `/reload-plugins`. So the install then types that through Orca into
     every drive session's terminal on this Mac, the communicator's, the top coordinator's and each
     sub-coordinator's, once each is idle. A session still
     busy after 30 seconds gets it typed into its prompt, where Claude Code holds it until the turn ends,
     since a turn can run for 20 minutes. It prints a line per session. A session another install
     already reloaded after this update is not sent it again, so installs run at once reload a session
     once. One showing a menu or refused by Orca names the `orca terminal send` command that types it:
     run that once the session is idle. `drive reload` does the same for every drive session with no
     install, whatever installs reloaded before. No post asks him to type `/reload-plugins`.
   - A tool this Mac runs: follow its repository's release flow, install the release that contains the
     merge, and read the installed version back. Fix the source and release it; a hand patch to an
     installed copy is lost at the next release.
   - A compiled tool such as cci, ccn, cc-slack, ccx, codex-ask, or cc-transcript need not wait for its
     release: build the merge sha with `go build -o ~/.vcache/scratch/<lane>/<binary> <main>`, taking
     `<main>` from the build its `.goreleaser.yaml` names (cc-transcript: `cargo build --release`), and
     rerun the case against that binary.
   - Ask him on a card before each install that restarts the hook helper every session on this Mac
     shares, such as capt-hook's: he approved one such restart, not all of them.

## Forge-AI/monorepo

Ship through the repository's own `submit-pr` skill, on a branch off `dev`. Never build on, push to, or
restack his own in-flight branches. A PR lands only when all of these hold at its current head:

- every required check is green;
- an approving review from the reviewer bot (`forge-pr-reviewer`) is in force, not dismissed;
- the latest `ai-review` run passed. When it holds the PR, fix each blocking finding, or accept a wrong
  one with `.agents/skills/submit-pr/scripts/pr-reviews accept <slug> --why "<why>"`. A finding on his
  design is an `ASK`;
- nothing holds it: no Hold tap, no `hold` label, no conflict with `dev`;
- he tapped Approve on its card, or it is an exempt class. His approval in words, or his pick on a card
  asking whether to land, is his tap too. The coordinator records it for exactly the PRs his words name
  with `drive pr set <url> --tapped Approve --tap-source <his message's permalink>`, because
  `landing_guard` refuses landing a drive PR with no recorded tap.

**Exempt classes land with no tap** once green. He never sees a PR card for one: it is marked as its
class wherever he sees it, and its order's thread gets one line naming the class and the PR, such as
"port of #N landed."

- `pure port`: a `Port:` PR that does what its old-tree PR #N does, line for line, with that PR's tests,
  and nothing more. The coordinator records it with `drive pr set <url> --port-of <N>`.
- `test-only`: only test and fixture files.
- `ci-only`: only CI configuration and CI tooling.
- `flake fix`: the whole change fixes one named flaking test, in test or fixture code, and adds no
  timeout.
- `incident fix`: the root fix an incident's order ships, by [incidents.md](incidents.md). A revert is
  not one and waits for his tap: `drive pr set` refuses the class to a PR whose title or body says it
  reverts a change.

Record any other class by its name with `drive pr set <url> --exempt "<class>"` and clear it with
`--no-exempt`; the landing guard reads it. Anything else waits for his tap.

**His tap counts once per PR.** It carries across every new head: rebase, restack, review fix, retitle,
and a push that carries out his own pick on a card the PR waited on, since his pick is what he
approved.
When a push changes what he approved, run `drive pr set <url> --fundamental "<what changed>"`, which
clears the tap and any exemption and puts the PR back on his card with that line. Fold two PRs that
carry out one decision of his into one first, so he gets one card.

**Enqueue** into the Trunk merge queue with the repository's script, naming every PR that may land:

    .agents/skills/submit-pr/scripts/stack-enqueue <PR>...

It enqueues the largest green, approved, unheld bottom of the stack in one call, never an unnamed PR,
and reads the queue back. `--check` previews the gate; `--watch` keeps enqueueing as the stack turns
green. The `landing_guard` hook refuses an enqueue missing an open PR of his card's stack; once part
lands, run `ccx vcs stack submit` for the rest.

**The queue is the only way in**, because every other way skips the checks it runs: the `landing_guard`
hook refuses `--admin`, and on a drive PR `gh pr merge`, the merge API or a `merge`/`merge-fast` label.
`tools/ci trunk submit --direct` needs his written ruling naming the PR and its full head sha. An
incident fix lands as [incidents.md](incidents.md) says.

**Every red on your PR is yours**, whether your change or `dev` caused it. Fix your own; for an
inherited one, restack onto a `dev` that has the fix, or fix it forward in its own PR. On a conflict or
a queue ejection, restack, read the entry's history and verification build, fix the cause, and enqueue
once more. Fix a test that keeps failing at its root; never retry past it with `--flake` or a rerun.

## Read-only repositories

Customer and forward-deployed engineers' repositories are read only: clone and read, never push,
comment, or merge, since he merges those himself. A change that belongs there comes back as `ASK` with
the patch.

## After merge

- Merged is not live: prove it before `DONE`. A plugin or tool's installed version contains the merge,
  or a compiled tool's local build of the merge sha reruns the case, by step 3 of
  [yasyf/* repositories](#yasyf-repositories). A monorepo change is live when its release is live on the
  targets it touches, shown by its card in #releases.
- Start a regular release (`tools/ci release start`) only when his order covers releasing; otherwise,
  the change rides the next release and the job returns `WAITING release`.
- A sub-coordinator, the coordinator session for one project, lands and installs its project's
  changes; the top coordinator alone starts, approves and rolls back production cuts, one at a time,
  since one cut carries every project's changes. The `landing_guard` hook refuses `ci release start`,
  `approve` and `rollback` from a sub-coordinator's session and the agents it starts, naming the top's
  session; `--plan`, `--dry-run`, `status` and `watch` still run. A job of a sub-coordinator returns
  `WAITING release` naming the merged changes the cut must carry, and its sub-coordinator asks the top
  for the cut with a `release` record at `root@<drive>`.
- His order or his pick on a card to release, or the coordinator's record of either, approves that
  release, its approve gate included, whatever migration, delete, or replace it holds. A pick on a card
  that did not ask whether to release approves nothing in one. Start it with `--order <his answer id>`,
  or run `tools/ci release approve <id> --via <agent>` the moment it holds, with no `ASK`, card, or
  check in between: nobody asks him again. A resume or re-plan of a release he ordered passes the same
  `--order` again.
- A job never approves a release he has not approved, and never touches one he started himself. An
  incident's order's words approve the incident's own cut, by [incidents.md](incidents.md).
- Then rerun the reporter's exact case on the live build, and return `DONE <proof>`: one line on what
  changed for him, and the link or version that proves it.
