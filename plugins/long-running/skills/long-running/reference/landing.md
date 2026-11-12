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
  its pictures; `drive pr set --serves` refuses a longer one and says how to split or fold it. Exempt and
  `yasyf/*` PRs, which land with no tap, are not counted.
- Title: `Fix:`, `New:`, `Cleanup:` or `Port:`, then what changes for a user, in plain words. A
  repository whose title check demands `fix:` or `feat:` keeps that prefix; the card reads either.
  `drive pr add` refuses a title the card cannot name the kind of, `drive pr set --tap` one retitled
  since, and `drive pr set --port-of` one not opening `Port:`, each giving the `gh pr edit --title`
  that fixes it.
- Write the body for him in plain words that name each function, syscall, flag, file, path, service or
  table by its proper term, never a vague stand-in, in his words "use the proper fucking terms for
  things". Leave out ids he cannot open (cc-notes, cci, job, order, or waits ids) and hashes: a sentence
  carrying a record id is dropped from his card.
- Each section stands alone, because he reads it with no other context. A number, fraction, size, or
  ratio says what it measures, why that value, and gives one example. A mechanism gets one plain
  sentence before it is used. Nothing points at a list, finding, thread, or PR the card does not show.
  Not "reads each journal file's page index sized to the file (a sixteenth of it, at most 64 KiB)", but
  "to open a run's page the browser first fetches a small index from the end of every journal file: once
  its last 64 KiB, now its last sixteenth (about 6 KiB of a 97 KiB file), never more than 64 KiB."
- Write these body sections, in this order. Fix the body, never the card: his card is redrawn from it.
  A body is fixed beside the landing, never before it: nothing in it holds a card, a tap, an enqueue
  or a merge, and a section it lacks is left off his card.
  - `## The stack`: only on a stack's bottom PR. Its first line is the stack's title, one readable line
    on what the whole stack does, then a short paragraph explains the stack in the Refresher's voice.
    They head his stack card.
  - `## Summary`: one or two sentences saying what the PR changes, naming each function, table, flag,
    file or service it touches by its proper term, with no background, in his words "a summary of wtf
    the PR is doing, and use exact technical terms rather than being roundabout". It opens the PR's
    section of his card.
  - `## Refresher`: the change explained to an engineer who knows the codebase but has not read this
    code lately: which part of the system it touches, what that part did before, and what it does now.
    It follows the Summary on his card. The card also reads `## ELI5`, and a monorepo body's
    `## Context`, which its `submit-pr` template writes in this voice.
  - `## Biggest surprise`: the one change a reviewer would not expect from the title, in one or two
    sentences.
  - `## What you'll hate most`: the one thing he is most likely to dislike, said plainly: a cost, a
    behavior he did not ask for, a shortcut, or a risk he would not take.
  - `## Show me`: one concrete example of the change, which his card draws as a picture and the
    dashboard draws live: the real SQL, code, CLI call, or Sand call it changes, before and after, in a
    fence naming its language (`sql`, `go`, `sand`). An `arch` map of the components it adds or rewires,
    or a `mermaid` flow, goes beside the example, never in its place; numbers before and after get a
    `chart` fence (Vega-Lite) or a markdown table. A change to what he or a user sees, a UI, page,
    card or post, gets a before and an after screenshot of it as two markdown images, kept anywhere,
    the drive's pictures folder included, `![1 · Before, dev](<path>)` then
    `![2 · After, this PR](<path>)`, each build named in words rather than a sha, which Slack shows
    side by side as one gallery, in his words "This is a visual change so it should
    have shown before and after screenshots". A fence holds code, never prose, JSON,
    YAML, logs, terminal output, an HTTP exchange, or a stack trace: a request alone is the call, and
    what it answered goes in a markdown table or a `mermaid` sequenceDiagram. Put a caption line just
    above each fence and alt text on each image, since Slack titles each picture
    with it; a fence with none is titled by its kind, such as "go code", and a title another picture
    on the card already has gets a number, as "Diagram (2)". His card carries each `chart` fence and
    markdown table as written, and Slack draws them as its own chart and data table, so fit a chart to
    Slack's limits in [Messages](messages.md), which `drive card` leaves off the card otherwise, and give
    a table two or more columns with real values in each cell. A stack's card draws at most 2 charts in all,
    the lowest PRs' first. Open with at most one short sentence: the card shows pictures, not prose.
  - On a public repository, such as every `yasyf/*` one, pictures show only a demo drive or public data,
    under a `demo-` or `public-` file or folder name, and the body names no private repository's PR and
    no teammate, by first name, full name, Slack handle or email. The live drive, private code, Slack, or
    a customer's or teammate's data or name goes only into a private repository's PR or a direct message
    to him. `ccx vcs ship` runs the `public-pr-body` hook over the body before it commits or pushes, and
    the `public-names` hook over each line a commit adds; the `landing_guard` hook refuses such a body in
    `gh pr create`/`edit`. After a fix, delete each earlier revision
    and the pictures' branch, since both stay readable.
  - `## Against the plan`: only when you departed from the plan: what it said, what you did, and why.
  - `## Port`: only on a port: the old PR it ports, linked, and anything the target tree needed to run
    it. A PR with behavior of its own beyond that is not a port.
  - `## His words, step by step`: each step his words in your brief name, quoted, beside the `path:line`
    that does it; it stays off his card. Quote only the order this PR carries out, never one whose jobs
    it only unblocks. Drive tooling says none and gets its card from its own order by
    [Scope](scope.md). A PR that is not a port and that no order of his asks for returns
    `BLOCKED no order of his names this` instead of `READY`.
- Register the PR the moment it opens: `drive pr add <url> --job <j>`, which records it waiting on his
  tap. Only a port (`--port-of <N>`), an exempt class (`--exempt <class>`), a PR of a `Drive tooling:`
  order outside `Forge-AI`, or one added with `--no-tap --why "<reason>"` waits on none; a `Forge-AI`
  PR waits on his tap whoever opened it and whatever order it serves.
- Return `PUSHED <url>` once, the moment the head you mean to ship is pushed and registered, with CI and
  review still running. `READY` counts only on a green required check: the watch holds a `PUSHED`,
  waking nobody, until its minute read finds every check the base branch requires green, then takes
  it as the `READY`, or sends a red one back to the builder. The watch then draws his card the
  minute GitHub reports the PR green and approved,
  once `drive job report` or `drive pr set --serves` checked its change at that head against his words,
  whatever its body says. Return no `WAITING` for CI, a review or a queue. A
  port's `READY` gets the coordinator's check against #N instead. Name every PR of a stack bottom first
  (`PUSHED <url> <url> …`) so he gets one card. Add a `SERVES:` line: `port of #N`,
  `Drive tooling: <what it unblocks>`, or the order of his it carries out, quoted.

## yasyf/* repositories

No tap and no green gate: by his standing rule a `yasyf/*` PR merges without asking, and by his words
"can merge past CI on personal repos and fix reds after the fact" it merges without waiting on CI.

1. Ship with `ccx vcs ship` (jj in colocated repositories); the `open-pr` skill writes the body.
2. Register it with `drive pr add <url> --job <j>`, which starts the drive's lander on it: a process
   of its own, outliving your turn, that merges it with `gh pr merge <n> -R <repo> --squash --admin`,
   whatever its checks show, then runs `drive install` from the merge sha for each plugin installed on
   this Mac whose folder the merge changes, or that is its marketplace's only plugin. It writes its
   output to the drive's `landings.log`, and its outcome reaches the job's holder as one line,
   `PR … merged at <sha>; <each install's version line>` or `PR … not landed: <why>`. `drive pr add`
   prints why it started none: a draft, the `hold` label, a lander already running on it, or a base
   other than the default branch, so a stacked PR is registered again once the PR below it merges.
   Read the checks once they finish and fix a red one forward in a follow-up PR.
3. Install by hand only what the lander does not, from the merge sha
   (`gh pr view <n> -R <repo> --json mergeCommit`):
   - A Claude Code plugin: `drive install <plugin>@<marketplace> --sha <merge sha>` (such as
     `long-running@skills`), which the lander runs itself, waits for the release that holds the merge, updates the plugin, and
     prints the installed version. It updates each scope's install with its own `claude plugin update --scope`, the user
     scope's and each project or local scope's from its folder, skipping one whose folder is gone, and checks each holds the
     merge, since a session loads the install its folder's scope names and an update moves one scope. A release holds it when the merge is in the release's commit and
     in the tag of each binary the plugin pins from that repo through a `*.binrun` file, since a
     binary is built at its tag, which can predate the version sync that names it (cc-inbox 0.54.27's
     sync merged after a fix its `cci` was built without). While it waits it prints a line naming
     the newest release and what of it lacks the merge. A plugin whose binary also has a Homebrew
     copy from `yasyf/tap` (cc-context's `ccx`, cc-inbox's `cci`, `cc-notes`, `cc-present`,
     `cc-review` and `cc-slack`) then gets that copy upgraded. Claude Code appends plugin bin
     directories after `/opt/homebrew/bin` on PATH, so a session's bare `ccx` runs Homebrew's copy.
     The install fast-forwards the `yasyf/tap` clone alone with `git pull --ff-only`, then runs
     `brew upgrade yasyf/tap/<binary>` with `HOMEBREW_NO_AUTO_UPDATE=1`, a few seconds, never
     `brew update`, which fetches every tap on the Mac and fails when any one of them cannot be
     fetched. With `--sha` it reads the bare binary's `--version` and repeats both every 20 seconds until that
     release's tag holds the merge, since the tap's formula commit can land after the plugin's
     release. Installs run at once take turns on the update and on each Homebrew pass under one flock
     on `plugins/.update.lock` in the Claude config directory, a few seconds each. Claude Code
     re-clones a stale marketplace into one fixed `<marketplace>..clone` directory, where two clones
     at once delete each other's files, and two pulls of one tap clone at once collide on its index lock. It refuses
     when the update left any scope's install at a commit without the merge, as a marketplace clone holding
     moved tags does, or running a binary whose release tag lacks it, naming that scope and its folder, the installed version,
     that commit or release, and the merge sha. It also refuses when the bare binary still runs a
     release without the merge at the timeout, naming that release. Installing long-running also restarts each drive's dashboard still serving other code, a
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
     shares, such as capt-hook's: he approved one such restart, not all of them. The `bash_guard` hook
     refuses `capt-hook helper install`, which installs captain-hook's newest release, until `drive answer`
     records his Install now on the card naming that release, and again once the helper runs it.

## Forge-AI/monorepo

Ship through the repository's own `submit-pr` skill, on a branch off `dev`. Never build on, push to, or
restack his own in-flight branches, and never land a PR someone else opened unless his order names it,
by [Neither](scope.md#the-four-kinds-of-pr). A PR lands only when all of these hold at its current head:

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
  and nothing more. The coordinator records it with `drive pr set <url> --port-of <N>`. A port that
  changes how data is stored, a migration, schema or `.proto` file, is not pure: its data model is a
  design part of its order, by his words "port his visuals but always do it the fundamentally right
  way when it comes to modelng the data", and `drive pr add` and `drive pr set` refuse it `--port-of`.
- `test-only`: only test and fixture files.
- `ci-only`: only CI configuration and CI tooling.
- `flake fix`: the whole change fixes one named flaking test, in test or fixture code, and adds no
  timeout.
- `incident fix`: the immediate fix, such as the stop, or the root fix an incident's order ships, by [incidents.md](incidents.md). A revert is
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

**His Approve lands a `Forge-AI/monorepo` PR with no session turn.** The drive's lander, a process of
its own, enqueues it into the Trunk merge queue with the coordinator checkout's script:

    .agents/skills/submit-pr/scripts/stack-enqueue --check <PR>...
    .agents/skills/submit-pr/scripts/stack-enqueue --watch <PR>...

The drive counts a review approval only on the PR's current head, as stack-enqueue does: an
approval GitHub still counts from an older head reads `approved on an older head`, never approved,
and draws no card. Its `--check` runs first, and a `BLOCKED` line it prints ends the landing as
`PR … not landed: <that line>`, unless each gap it names is an approval on an older head, which
stack-enqueue names only once `ai-review` finished on that head without approving it. Then the
lander posts `@forge-pr-reviewer review` on that PR, once a head, and `--watch` waits for the
approvals and queues it. His card tap starts it when the PR is already green, approved by the
reviewer bot, on its head or an older one, and free of conflicts, and the watch starts it within
one GitHub read, 60 seconds, of the PR getting there. It
names the PRs of his card's stack bottom first, up to the first he has not approved, and starts on
none while a card of his that the PR or its job waits on is open, or the PR is a draft, held or in
conflict. Each head gets one try. Its outcome comes back as the PR's line: `PR … queued for merge`
wakes no one, while `PR … not landed: <why>` and `PR … ejected from the merge queue` wake the job's
holder for a builder that fixes the cause and pushes, and the lander tries the new head on its own.
His `TAP Approve` line itself wakes the coordinator only when the PR is red or conflicts. An exempt
class, and a PR of another `Forge-AI` repository, is enqueued by its handoff builder:

    .agents/skills/submit-pr/scripts/stack-enqueue <PR>...

It enqueues the largest green, approved, unheld bottom of the stack in one call, never an unnamed PR,
and reads the queue back. `--check` previews the gate; `--watch` keeps enqueueing as the stack turns
green. The `landing_guard` hook refuses an enqueue missing an open PR of his card's stack; once part
lands, run `ccx vcs stack submit` for the rest. The lander runs outside any Claude session, so no hook
sees it: it checks his tap and his open cards itself, from the drive's state.

**The queue is the only way in**, because every other way skips the checks it runs: the `landing_guard`
hook refuses `--admin`, and on a drive PR `gh pr merge`, the merge API or a `merge`/`merge-fast` label.
`tools/ci trunk submit --direct` needs his written ruling naming the PR and its full head sha. An
incident fix lands as [incidents.md](incidents.md) says.

**Every red on your PR is yours**, whether your change or `dev` caused it. Fix your own; for an
inherited one, restack onto a `dev` that has the fix, or fix it forward in its own PR. On a conflict or
a queue ejection, restack, read the entry's history and verification build, fix the cause, and enqueue
once more. Fix a test that keeps failing at its root; never retry past it with `--flake` or a rerun, which
the `bash_guard` hook refuses in a drive session or agent (`stack-enqueue --flake`, `tools/ci bk rerun`,
`bk job retry`, `bk build rebuild`, `gh run rerun`), naming `tools/ci bk log <job>` instead.

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
  `--order` again. The `landing_guard` hook refuses `ci release start --order <id>` in a drive session
  or its agents when Jev does not read that answer as his order or card pick to release, unless the
  answer is an incident's, labelled `incident`.
- A job never approves a release he has not approved, and never touches one he started himself. An
  incident's order's words approve the incident's own cut, by [incidents.md](incidents.md).
- Then rerun the reporter's exact case on the live build, and return `DONE <proof>`: one line on what
  changed for him, and the link or version that proves it.
