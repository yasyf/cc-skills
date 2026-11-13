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
  land arrived, since its recommended option never stands in for his answer. `drive pr add`,
  `drive pr land` and the `landing_guard` hook refuse a PR while a card of his is open that any job
  carrying it waits on, its own or one holding its landing, or whose `Holds:` line names a change one
  of them builds; the watch stops a lander already landing it when that card opens, and the lander
  dequeues what it queued. On a Sprite lane, which holds no copy of the drive, the hook asks this Mac
  with `drive job held --pr` first and refuses while it does not answer. Return `WAITING` naming the card, which
  the coordinator puts in front of him by [Design first](design.md). An incident's repair is the
  exception: it lands on the recommended option, the card stays open, and a different pick of his
  starts a follow-up job on the same order.
- A stack draws one card for his Approve whatever its size: the card names every PR, and the PR
  sections past the 50 blocks one Slack message holds open under its Explain more.
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
    section of his card, under the lead `drive pr add` keeps.
  - `## Refresher`: the change explained to an engineer who knows the codebase but has not read this
    code lately: which part of the system it touches, what that part did before, and what it does now.
    It follows the Summary on his card. The card also reads `## ELI5`, and a monorepo body's
    `## Context`, which its `submit-pr` template writes in this voice.
  - `## Biggest surprise`: the one change a reviewer would not expect from the title, in one or two
    sentences.
  - `## What you'll hate most`: the one thing he is most likely to dislike, said plainly: a cost, a
    behavior he did not ask for, a shortcut, or a risk he would not take.
  - `## Show me`: one concrete example of the change, which his card draws as a picture, or as Slack's
    code block when the code is one line or trivial, and the dashboard draws live: the real SQL, code, CLI call, or Sand call it changes, before and after, in a
    fence naming its language (`sql`, `go`, `sand`). An `arch` map of the components it adds or rewires,
    or a `mermaid` flow, goes beside the example, never in its place; numbers before and after get a
    `chart` fence (Vega-Lite) or a markdown table. A change to what he or a user sees, a UI, page,
    card or post, gets a before and an after screenshot of it as two markdown images, kept anywhere,
    the drive's pictures folder included, `![1 · Before, dev](<path>)` then
    `![2 · After, this PR](<path>)`, each build named in words rather than a sha, which Slack shows
    side by side as one gallery, in his words "This is a visual change so it should
    have shown before and after screenshots". In Forge-AI/monorepo CI shoots both for an Iris view
    the body's `iris-evidence` block names: its `product-dashboard-iris-evidence` job uploads the last
    dev build's shot as `iris-evidence/dev/<shot>.png` and the change's as `iris-evidence/<shot>.png`,
    so a builder takes both from that job's artifacts rather than building the app. A fence holds code, never prose, JSON,
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
    [Scope](scope.md). A flake fix quotes his ruling 532cd3d, "and ffs fix these flaky tgests how many
    times do I have to tell yoiu", beside the `path:line` that fixes the flake's cause. Any other PR that
    is not a port and that no order of his asks for returns `BLOCKED no order of his names this` instead
    of `READY`.
- Register the PR the moment it opens: `drive pr add <url> --job <j> --headline "<line>" --because
  "<line>" --does "<line>" --downstream "<line>"`, which records it waiting on his tap and opens his
  card with that lead: the headline as its title, under 90 characters, then why the change is made,
  what it does for him or its users and what else it changes once it lands, one plain line each with
  no function or file named, since the Summary names the code. `drive pr add` refuses a PR waiting on
  his tap until all four are kept, by his words on a card "has a lot of facts but is compeltely
  useless for me figuring ut if I want to meg eit or not, I have no idea why were doing this change,
  what it does, or what the downstream impacts of it are"; a later `drive pr add` with only the
  lines it changes keeps the rest and redraws his card. Only a port (`--port-of <N>`), an exempt class (`--exempt <class>`), a PR of a `Drive tooling:`
  order outside `Forge-AI`, or one added with `--no-tap --why "<reason>"` waits on none; a `Forge-AI`
  PR waits on his tap whoever opened it and whatever order it serves, unless an exempt class covers
  it, and `drive pr add` and `drive pr set` refuse `--no-tap` on one, since only his tap or an exempt
  class lands it.
- Return `PUSHED <url>` once, the moment the head you mean to ship is pushed and registered, with CI and
  review still running. `READY` counts only on a green required check: the watch holds a `PUSHED`,
  waking nobody, until its first GitHub read after the take finds green every check the rules of its repository's default
  branch name, one not yet posted holding it too, or at that read when they name none, then takes it
  as the `READY`, or sends a red one back to the builder. The watch then draws his card the
  minute GitHub reports the PR green and approved,
  once the watch's `drive pr check` checked its change at that head against his words,
  whatever its body says. Return no `WAITING` for CI, a review or a queue. A
  port's `READY` gets the coordinator's check against #N instead. Name every PR of a stack bottom first
  (`PUSHED <url> <url> …`) so he gets one card. Add a `SERVES:` line: `port of #N`,
  `Drive tooling: <what it unblocks>`, or the order of his it carries out, quoted.

## yasyf/* repositories

No tap: by his standing rule a `yasyf/*` PR merges without asking, and by his words "can merge past CI
on personal repos and fix reds after the fact" it merges without waiting on CI, but never while a check
fails at its head, by his words "tooling fixes merge early only when no check is failing on their own
change".

1. Ship with `ccx vcs ship` (jj in colocated repositories); the `open-pr` skill writes the body.
2. Register it with `drive pr add <url> --job <j>`. Taking the builder's `READY` or `PUSHED`, which
   needs its proof file kept with `drive job set <j> --repro <file>`, makes its stage read `ready` while
   its checks run or pass and `red` once one fails, and the watch starts the drive's lander on it within
   one GitHub read of `ready`: a process of its own, outliving your turn, that reads its checks from GitHub
   again and merges it with `gh pr merge <n> -R <repo> --squash --admin --match-head-commit <head>` at the
   head that read found no check failing on, records the merge sha on the PR, then runs `drive install` from it for
   each plugin installed on this Mac whose folder the merge changes, or that is its marketplace's only
   plugin. When GitHub refuses the merge with `Base branch was modified`, which another merge into
   `main` while this one ran causes, the lander merges again on the new base, as GitHub's own refusal
   asks, and never touches the PR's branch. When `gh pr merge` refuses a PR of a native GitHub stack,
   saying it must be merged using the asynchronous merge REST API, the lander merges it through
   GitHub's `merge-async` endpoint past its rules, which GitHub allows only for a stack's bottom PR,
   the one the lander lands, and reads its result until GitHub ends it. A refusal for a conflict with its base reads `red`, which
   wakes its builder to rebase. After any other refusal, a failing check included, the watch
   starts the lander again on the head its builder pushes once its stage reads `ready` there, free of
   conflicts and failing checks, so nobody registers a rebased PR again. Once every PR the job's `READY` or `PUSHED` names merged and no lander still
   runs on one, the watch starts `drive job prove <j>`, which installs each plugin a `yasyf/*` merge
   changes whose installs, binaries, Homebrew copy or Captain Hook.app lack it, its other merges taken at once, waits until each workflow a `yasyf/*` merge ran on the default branch completed its first run from the merge on that was not cancelled, then runs the job's proof file with those plugins' `bin/` first on
   `PATH`, keeps its output beside the job's report, takes `DONE` on exit 0 with output or `BLOCKED` otherwise,
   an exit 0 printing nothing among them,
   and on a `DONE` that finishes the order sends the communicator its close report. The lander writes its output to the drive's `landings.log`, ending in one line of the PR's
   key and its outcome, such as `yasyf/cc-skills#7: merged at <sha>; …`. Its outcome reaches the
   job's holder as `PR … merged at <sha>; <each install's version line or no release: line>` or
   `PR … not landed: gh pr merge --squash --admin refused it: <its answer>`, such as a conflict git
   cannot merge, and the proof's as `PR … proven live by drive job prove …` or
   `PR … drive job prove failed: …`. Its stage in `drive status` says why no lander started: a
   failing check, a draft, the `hold` label, a conflict, an open card of his, or a lander already
   running on it; a stacked PR reads `behind` until the PR below it merges, and the lander refuses one
   on a base other than the default branch. Read the checks once they finish and fix one that fails
   after the merge forward in a follow-up PR.
3. Install by hand only what the lander does not, from the merge sha
   (`gh pr view <n> -R <repo> --json mergeCommit`):
   - A Claude Code plugin: `drive install <plugin>@<marketplace> --sha <merge sha>` (such as
     `long-running@skills`), which the lander runs itself, waits for the release that holds the merge, updates the plugin, and
     prints the installed version. Before it updates anything it reads the `dependencies` floors of that release's
     `plugin.json` and installs each sibling below its floor first, waiting until its release is out: a plugin that
     reads a sibling's new field, such as long-running reading cc-slack's `closed`, crashes on the old sibling. A
     captain-hook floor, or captain-hook's own release, also needs Captain Hook.app, whose hook worker runs every
     session's hooks, at that release: while it is older, the install holds and prints his Install now card, naming
     its ASK file once a job keeps one, and installs nothing; run it again once `drive answer` records his tap, and it
     runs `capt-hook helper install` first. It updates each scope's install with its own `claude plugin update --scope`, the user
     scope's and each project or local scope's from its folder, skipping one whose folder is gone, and checks each holds the
     merge, since a session loads the install its folder's scope names and an update moves one scope. A release holds it when the merge is in the release's commit and
     in the tag of each binary the plugin pins from that repo through a `*.binrun` file, since a
     binary is built at its tag, which can predate the version sync that names it (cc-inbox 0.54.27's
     sync merged after a fix its `cci` was built without). While it waits it prints a line naming
     the newest release and what of it lacks the merge. It waits only for a release that is coming:
     when the newest release lacks the merge, it reads the repo's workflows at the merge, and a job
     calling `plugin-release-tag.yml` releases the plugin when the plugin is its marketplace's only
     one or the job names a path in the plugin's folder. Such a job tags on any merge when its
     `paths` input is empty; otherwise it tags only when a commit on the merge's first-parent line
     since the newest `v*` tag changes one of its paths, the plan `release_tag.py` makes, so a merge
     after an unreleased source change still waits for the release that carries both. When that job
     would tag none (cc-notes#276 changed only `release-tag.yml`, `AGENTS.md` and its fragment), the
     install prints `no release: <why>`, updates and proves nothing and exits 0, and the lander's outcome reads
     `merged at <sha>; no release: …` instead of a 900-second wait ending in failure. A plugin no
     such job releases, as Autobump releases `long-running`, waits as before. A plugin whose binary also has a Homebrew
     copy from `yasyf/tap` (cc-context's `ccx`, cc-inbox's `cci`, `cc-notes`, cc-orchestrate's `cco`,
     `cc-present`, `cc-review`, `cc-slack` and codex's `codex-ask`) then gets that copy upgraded. Claude Code appends plugin bin
     directories after `/opt/homebrew/bin` on PATH, so a session's bare `ccx` runs Homebrew's copy.
     The install fast-forwards the `yasyf/tap` clone alone with `git pull --ff-only`, then runs
     `brew upgrade yasyf/tap/<binary>`, `yasyf/tap/cc-orchestrate` for `cco`, with `HOMEBREW_NO_AUTO_UPDATE=1`, a few seconds, never
     `brew update`, which fetches every tap on the Mac and fails when any one of them cannot be
     fetched. With `--sha` it reads the bare binary's `--version` and repeats both every 20 seconds until that
     release's tag holds the merge, since the tap's formula commit can land after the plugin's
     release. Installs run at once take turns on the update and on each Homebrew pass under one flock
     on `plugins/.update.lock` in the Claude config directory, a few seconds each, waiting for its turn no
     longer than its deadline. Claude Code
     re-clones a stale marketplace into one fixed `<marketplace>..clone` directory, where two clones
     at once delete each other's files, and two pulls of one tap clone at once collide on its index lock. It refuses
     when the update left any scope's install at a commit without the merge, as a marketplace clone holding
     moved tags does, or running a binary whose release tag lacks it, naming that scope and its folder, the installed version,
     that commit or release, and the merge sha. It also refuses when the bare binary still runs a
     release without the merge at the timeout, naming that release. Each drive's dashboard hands itself to a newer
     long-running the moment its install replaces `installed_plugins.json` in the Claude config directory's `plugins/` folder, by `drive install` or Claude Code's own update: that install's
     drive takes the server's process on the same listening sockets, so its page answers throughout, and each watch ends before
     its next pass and its launch agent starts it on that install. A live session runs the installed version's
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
     refuses any session's `capt-hook helper install`, which installs captain-hook's newest release, and its
     `brew upgrade` or `brew reinstall` of captain-hook, until `drive answer` records his Install now on the card
     naming that release, and again once the helper runs it; the refusal names the card's ASK file.

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

**Exempt classes land with no tap** once green, but for a `flake fix` changing a file outside test and
fixture code. He never sees a PR card for one landing with no tap: `drive card` draws a
fresh card only for a PR that asks his tap, by the same check that gives a card its Approve, so a post
linking an exempt PR keeps its plain link. It is marked as its class wherever he sees it, and its
order's thread gets one line naming the class and the PR, such as "port of #N landed." The drive's
lander lands an exempt PR once it is green, after checking it really is in its
class from its changed files, title and body at its head, by his ruling that the landing tool checks
the class before it lands: no file that changes how data is stored for a `pure port`, only test and
fixture files for `test-only` and for a `flake fix` landing with no tap, only CI files for `ci-only`,
and no revert for an `incident fix`. One outside its class comes back as `PR … not landed: <why>`.

- `pure port`: a `Port:` PR that does what its old-tree PR #N does, line for line, with that PR's tests,
  and nothing more. The coordinator records it with `drive pr set <url> --port-of <N>`. A port that
  changes how data is stored, a migration, schema or `.proto` file, is not pure: its data model is a
  design part of its order, by his words "port his visuals but always do it the fundamentally right
  way when it comes to modelng the data", and `drive pr add` and `drive pr set` refuse it `--port-of`.
- `test-only`: only test and fixture files.
- `ci-only`: only CI configuration and CI tooling: files under `.github/`, `.buildkite/`, `go/ci/` and
  `infra/ci/`, whose Buildkite pipeline definitions are CI configuration, and the `tools/ci` and
  `tools/ci-ts` shims.
- `flake fix`: the whole change fixes one named flaking test at its cause, whatever files it touches,
  and adds no retry, skip, timeout or rerun, by his ruling 532cd3d, "and ffs fix these flaky tgests how
  many times do I have to tell yoiu". It serves that ruling, never the order whose PR the flake reddened,
  so the watch's check judges it against 532cd3d read whole in place of any order, and it is never
  `BLOCKED no order of his names this`, closed, or emptied to answer a `NOT READY`. Register it with
  `drive pr add <url> --job <j> --exempt "flake fix"`, which reads its files from GitHub: in test or
  fixture code alone it lands with no tap; with any other file it keeps his tap, and his card quotes the
  ruling's words, by his words "you cna also merge CI/tst-only tweaks/fixes without me, but onyl if it
  doesnt touch non-test/CI code at all".
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

**His Approve or an exempt class lands a `Forge-AI/monorepo` PR with no session turn.** The drive's
lander, a process of its own, enqueues it into the Trunk merge queue with the script as the head of
the repository's default branch holds it, run from a fresh worktree of that head made for the landing
and removed after it, never from the coordinator checkout, whose commit and uncommitted files it leaves
as they are. A landing fix merged to the default branch, such as a newly pinned toolchain, holds for
the next landing at once:

    .agents/skills/submit-pr/scripts/stack-enqueue --check <PR>...
    .agents/skills/submit-pr/scripts/stack-enqueue --watch <PR>...

The drive counts a review approval only on the PR's current head, as stack-enqueue does: an
approval GitHub still counts from an older head reads `approved on an older head`, never approved,
and draws no card. Its `--check` runs first, and on each head its `BLOCKED` lines name approved only
on an older head, which stack-enqueue names only once `ai-review` finished on that head without
approving it, the lander posts `@forge-pr-reviewer review`, once a head. Then `--watch`, which alone
tells a wait from a fix, waits out each wait in the lander's own process, such as that approval or
GitHub still computing mergeability after a push to the default branch, and queues it, or ends the
landing as `PR … not landed: <its last lines>` on the first gap that needs a fix, such as a conflict. His card tap starts it when the PR is already green, approved by the
reviewer bot, on its head or an older one, and free of conflicts, and the watch starts it within
60 seconds of reading the PR there, which it does as GitHub delivers the change. It
walks each stack his Approve touches, a native GitHub stack or PRs each on the one below's head
branch, and lands each PR he approved with the PRs under it once each of them is ready.
`stack-enqueue` queues a named PR with those below it and never one above, so a PR above that cannot land
now, such as one red, held, in conflict or still waiting on his Approve, holds none of them. While a PR
below cannot land, the PR above it reads `behind` it, and while one below conflicts with its base, every PR above it
reads `behind` it whatever its own row reads, owed by that PR's owner or the card holding it, so no builder above it
is woken: none of them lands before it, and only its restack moves them. Each other PR of his card lands in a call of its own, since `stack-enqueue`
refuses PRs named together that no stack links, such as one order's sibling PRs on `dev`. What
holds a PR he approved is its stage, as `stage.of` names it, which his card shows and whose owner the
watch wakes, and its deadline wakes that owner again.
His Approve is one field on the PR, kept on every PR of the landing unit his card drew and cleared
only by his Hold, and it lands the PR at its first green head whatever the check of its change
against his words says, whatever head he tapped it on, by his words "why the fuck would you keep eveyhting broken to make it perfect": a head that moved under his card keeps Approve showing and
lands with no second check. A `NOT READY` at the PR's head clears no tap and holds no landing: his
card keeps Approve and draws the objection whole under Where it stands, and its `PR … NOT READY`
line goes to the builder answering for the PR through `drive pr wake` only until he approves it: on a
PR he approved it wakes nobody, so no builder changes what he approved. His order to merge a PR as it
stands is his Approve. Each head gets one try, but a try that fails on the lander's own step says nothing of
the PR: one that could make no fresh worktree of the default branch, such as when GitHub did not send a blob its
checkout fetched, or whose `stack-enqueue --check` or `--watch` exited 5, its own failure, such as a `ccx` that a
Homebrew upgrade unlinked for a moment or a GitHub read that timed out. The watch says it once to the meta session
as the lander's own `LANDER the lander could not …` defect, never as the PR's not landed line, and the lander tries
again at the watch's next pass with no new tap. So does a try that ended on a failed GitHub read before it
queued or merged anything, such as a TLS handshake timeout on its diff read, said on the PR's line and named by
its `ready` stage until a try lands it. The lander reads the default branch's head with `git ls-remote` and
fetches that commit by its sha, writing no ref, since every worktree of the clone and every lander share
`refs/remotes/origin/<branch>`. A try that
comes to what the one before it on that head did is said once. A try stands only while what let it run does: once `drive pr set <url> --no-exempt --tap`
puts the PR on his card, or his tap comes after the try started, the PR reads his card or `ready` again, never
`unlanded`. Its outcome comes back as the PR's stage:
`landing` while Trunk holds it, and `unlanded`, a lander's try that ended without queueing it, with
`UNLANDED_SILENT` when its process ended before saying, which wakes the session holding its order
every 30 minutes for a builder that fixes the cause and pushes; the lander tries the new head on its own.
A merge GitHub refused for a conflict with its base, `Pull Request has merge conflicts` or `the merge
commit cannot be cleanly created`, is never `unlanded`: the PR reads `red`, owed by its builder, "it
conflicts with its base", whatever the watch last read of its mergeability, so `drive pr wake` sends its
builder the line at once and every 30 minutes, and no session starts one; its rebased head lands on its own.
His `TAP Approve` line itself wakes no session: a red PR's stage wakes its builder.

**The same lander lands every other repository the drive tracks**, such as `Forge-AI/litestream`, by
the coordinator's finding that approved `litestream` PRs had no owner: once his Approve or an exempt
class clears a PR and its stage reads `ready`, it merges the stack's bottom PR alone with
`gh pr merge --squash --match-head-commit <head>`, which GitHub merges only at the head the stage read
and only once that repository's rules pass, then the next PR once it reads `ready` on the new base. The
approving reviews a PR needs come from the pull request rule of its repository's default branch, as
GitHub's GraphQL `requiredApprovingReviewCount` reads it: `Forge-AI/monorepo` requires one, so a green
head forge-pr-reviewer approved nothing on is `red` until its findings are answered, and a repository
whose rules require none lands at green with no review. The lander runs outside any Claude session,
so no hook sees it: it checks his tap and his open cards itself, from the drive's state, and refuses a
PR the drive does not track, naming `drive pr add`.

**The queue is the only way in**, because every other way skips the checks it runs: the `landing_guard`
hook refuses `--admin`, and on a drive PR `gh pr merge`, the merge API or a `merge`/`merge-fast` label.
`tools/ci trunk submit --direct` needs his written ruling naming the PR and its full head sha. An
incident fix lands as [incidents.md](incidents.md) says.

**Every red on your PR is yours**, whether your change or `dev` caused it, and while it waits on his tap
too. Fix your own; for an
inherited one, restack onto a `dev` that has the fix, or fix it forward in its own PR, a `flake fix` when
a test flaked, whose cause may be in product code. Waiting on a fix in another PR, return `WAITING`
naming it, which puts it on his card on yours, with the fix's own card when it waits on his Approve. On a conflict or
a queue ejection, restack, read the entry's history and verification build, fix the cause, and enqueue
once more. Fix a test that keeps failing at its root; never retry past it with `--flake` or a rerun, which
the `bash_guard` hook refuses in a drive session or agent (`stack-enqueue --flake`, `tools/ci bk rerun`,
`bk job retry`, `bk build rebuild`, `gh run rerun`), naming `tools/ci bk log '<job URL>'` instead.

An ejection on a flaky test outside your diff needs no push. Fix the test in its own `flake fix` PR, or
find the one the drive already tracks, then run `drive pr set <url> --flaky <the test> <its fix PR>`.
Your PR reads `waiting` until that fix merges. Then the lander queues the same commit again with
`stack-enqueue --flake`, once, and `stack-enqueue` records the retry on the PR. A second ejection at that
commit reads `unlanded`, and so does a fix that closed unmerged: restack and fix the cause, as above.

An ejection whose record in cci's `trunk` drive gives a `GITHUB_API_` reason, such as
`GITHUB_API_MERGE_PR_FAILED`, with no failing check needs no push either: Trunk tested the stack green
and GitHub refused its merge. The watch says `PR … merge refused by GitHub …` on every PR that try
queued, never `checks failed`, and the lander lands the same PRs again one at a time, bottom first; a
second refusal at the same head reads `unlanded`. Trunk's own `Trunk Merge Queue` check says what the
queue did with a PR, never what a test found on its head, so the drive counts it as no check.

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
- His order or his pick on a card to release, or the coordinator's record of either, approves starting
  that release, never the migrations, deletes and replaces its approve hold holds. A pick whose words
  order the release approves it whatever its card asked, by his words "if I apprtove a release dont
  fuckl around kjust appriove it". Start it with `--order <his answer id>`, with no `ASK` or card
  first: nobody asks him again. A resume or re-plan of a release he
  ordered passes the same `--order` again. At the approve hold the job that started it reads each held
  migration, delete and replace against the code at the release's commit, then runs `tools/ci release
  approve <id> --via <agent>`, naming in `--leave-out` each stack it cannot vouch for, never
  cancelling the release or asking him, by his pick "Land whole stacks and read the gate: no schema
  change merges without its writer; a stack's bottom waits for its top". Jev reads his approval once:
  `drive job brief --release` refuses a Release line whose `--order <id>` Jev does not read as his
  order or pick to release, unless the answer is an incident's, labelled `incident`, and the
  `landing_guard` hook then lets the job start only that line as written. A drive session's own
  `ci release start --order <id>`, which no brief names, gets the same reading from the hook.
- A job never approves a release he has not approved, and never touches one he started himself. An
  incident's order's words approve the incident's own cut, by [incidents.md](incidents.md).
- Then rerun the reporter's exact case on the live build, and return `DONE <proof>`: one line on what
  changed for him, and the link or version that proves it.
