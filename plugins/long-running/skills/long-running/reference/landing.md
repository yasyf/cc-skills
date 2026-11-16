# Land a change

You own your PR from its first push until the proof that the change is live. Land it the way the
section for its repository says, then return `DONE <proof>`. The drive's watch and its lander do the
waiting: you decide, fix what is red, and name what you chose in your return.

- [Every PR](#every-pr)
- [yasyf/* repositories](#yasyf-repositories)
- [The monorepo](#the-monorepo)
- [Staging tests in the monorepo](#staging-tests-in-the-monorepo)
- [Before any change FDEs could see](#before-any-change-fdes-could-see)
- [Read-only repositories](#read-only-repositories)
- [After merge](#after-merge)

## Every PR

- One PR is one feature-sized unit. Amend review fixes and follow-ups into it with
  `ccx vcs ship --amend`. Open a second PR when another job waits on part of the work or a review
  asks for the split.
- Open PRs that change the same files land as a train, oldest first. Find them before you open
  yours (`gh pr list`, `gh pr diff <n> --name-only`). A change that must ship with one of them is
  amended into it. Each later one gets `drive pr set <url> --waits <the earlier PR>` and rebases
  once, onto the base that merge left, since every push restarts each gate it waits on.
- The default branch stays releasable. That is a goal you hold, with no check, card or body section
  behind it.
- A PR that changes a format another build reads (a stored file, row, journal or manifest) lands
  after the PR teaching its readers is live on every client, and its body links that PR.
- A stack draws one card for his Approve whatever its size. The card names every PR, and sections
  past the 50 blocks one Slack message holds open under its Explain more.

### Cards and tests that hold a PR

A question card of his holds a PR when its `Holds:` line names the PR by link, `#N` or whole branch
name, or its `DESIGN:` line cites it. The card is one a job on the PR's orders asked, or the staging
tester of a part the PR waits on, or a designer or reader naming a change your job builds from. A
card a job only waits on wakes that job and holds no PR. No card holds a `flake fix` or a
`main fix`.

- While such a card is open, `drive pr add`, `drive pr land` and the `landing_guard` hook refuse the
  PR, because the card's recommended option is not his answer. The watch stops a lander already
  running and the lander dequeues what it queued. A Sprite lane holds no copy of the drive, so its
  hook asks this Mac with `drive job held --pr` and refuses while that goes unanswered. Return
  `WAITING <the PR> waits on <the card>` and work on something the card does not hold.
- Outside an incident a held PR draws no PR card until he answers each card holding it.
- An incident's repair lands on the recommended option with the card left open. His other pick
  starts a follow-up job on the same order. His Approve on the PR answers each card holding it with
  its recommended option, and the lander closes those cards before it lands. A card asking him to
  lift a ruling of his, which `drive job ask` marks, holds the PR on an incident too.
- His Approve stands until his words rework the PR or he taps Hold. A card that comes to hold the PR
  after his Approve holds it, owed by him, until he answers that card.
- A started staging test holds its own PR and no other PR of its job or stack. While an order's
  `staging test of #N before it lands` part is undelivered and has a job on it, or the PR waits on
  it, the PR reads waiting on that run. It reads `red`, owed by its builder, while no tester runs it.
- `drive pr add`, `drive pr set` and the lander put that part on each PR waiting on his tap whose
  paths reach a released component, whatever its exempt class. Paths on `<ci>`'s dev list, such as
  `<ci>` and its source, reach none. A part his answer took off, `drive answer <id> --dropped <o>:<part>`,
  holds nothing until a builder's `STAGING: #N <cases>` line asks for the test, which puts it back:
  until then that PR merges first and gets its staging pass after the merge. `drive order set
  --drop-part` refuses an open PR's staging part, by his words a9c9edc, "remember everything has to be tested on sandbox first/ staging in detail before merging".

### Title and body

Title: `Fix:`, `New:`, `Cleanup:` or `Port:`, then what changes for a user in plain words. A
repository whose title check demands `fix:` or `feat:` keeps that prefix, and the card reads either.
`drive pr add` refuses a title whose kind the card cannot name, `drive pr set --tap` one retitled
since, and `drive pr set --port-of` one not opening `Port:`. Each refusal gives the
`gh pr edit --title` that fixes it.

He reads the body on his card with no other context, so write it for him:

- Name each function, syscall, flag, file, path, service or table by its proper term.
- Leave out hashes and ids he cannot open (cc-notes, cci, job, order or waits ids). A sentence
  carrying a record id is dropped from his card.
- Each section stands alone. A number says what it measures, why that value, and gives one example.
  A mechanism gets one plain sentence before it is used. Nothing points at a list, thread or PR the
  card does not show. Not "reads each journal file's page index sized to the file (a sixteenth of
  it, at most 64 KiB)", but "to open a run's page the browser first fetches a small index from the
  end of every journal file: once its last 64 KiB, now its last sixteenth (about 6 KiB of a 97 KiB
  file), never more than 64 KiB."

His card is redrawn from the body, so fix the body to fix the card. No body section gates a card, a
tap or a merge. A section the body lacks is left off the card. The sections, in order:

- `## The stack`: on a stack's bottom PR. Its first line is the stack's title, then a short
  paragraph on what the whole stack does. They head his stack card.
- `## Summary`: one or two sentences on what the PR changes, in proper terms, with no background. It
  opens the PR's section of his card under the lead `drive pr add` keeps.
- `## Refresher`: the change for an engineer who knows the codebase but has not read this code
  lately: which part it touches, what that part did before and what it does now, each term defined
  where it first appears. The card also reads `## ELI5`, and a monorepo body's `## Context`, which
  its `submit-pr` template writes in this voice.
- `## Biggest surprise`: the one change a reviewer would not expect from the title.
- `## What you'll hate most`: the one thing he is most likely to dislike: a cost, a behavior he did
  not ask for, a shortcut or a risk. Name a cost as the concrete things the change adds, such as a
  new message kind or a call that now waits on another.
- `## How old data moves`: on a PR changing how data is stored (a migration, schema or wire format,
  by `land.stored`): how stored data moves to the new shape and what keeps readers and writers
  working meanwhile. His card draws it as Stored data, and reads a monorepo body's
  `## Migration risk` and `## Data-location changes` the same way. Until the body has one the card
  says so and the builder hears an `EVENT undrawn`.
- `## Show me`: one concrete example, which his card draws as a picture and the dashboard draws
  live. Open with at most one short sentence.
  - Show the real SQL, code, CLI call or Sand call before and after, in a fence naming its language
    (`sql`, `go`, `sand`), with one plain sentence after it on what happens when it runs, as
    *A post that explains* in [Messages](messages.md) shows. One-line or trivial code draws as
    Slack's code block.
  - An `arch` map of the components it adds or rewires, or a `mermaid` flow, goes beside the
    example. Numbers before and after get a `chart` fence (Vega-Lite) or a markdown table with two
    or more columns and real values. Slack draws both itself, so fit a chart to Slack's limits in
    [Messages](messages.md), or `drive card` leaves it off. A stack's card draws at most 2 charts,
    the lowest PRs' first.
  - A fence holds code. JSON, YAML, logs, terminal output, an HTTP exchange and stack traces go in
    a markdown table or a `mermaid` sequenceDiagram; a request alone is the call.
  - Put a caption line above each fence and alt text on each image, since Slack titles each picture
    with it. A fence with none is titled by its kind, such as "go code", and a repeated title gets
    a number, as "Diagram (2)".
  - A change to what he or a user sees gets a before and an after screenshot as two markdown images,
    `![1 · Before, dev](<path>)` then `![2 · After, this PR](<path>)`, each build named in words.
    Slack shows them side by side. Keep them anywhere, a Sprite lane's path included: `drive pr add`
    copies them to this Mac. Where the repository's CI shoots both, link the images its job posted
    or download them with `<ci> bk api` into the drive's pictures folder (your scratch directory on
    a Sprite) instead of building the app. A `buildkite.com` artifact link draws nowhere (HTTP 403
    for his card and for GitHub), and `drive pr add` refuses a Show me image that does not download.
- `## Against the plan`: when you departed from the plan: what it said, what you did and why.
- `## Port`: on a port: the old PR, linked, and anything the target tree needed to run it. A PR
  with behavior of its own beyond that is not a port.

On a public repository, such as every `yasyf/*` one, pictures show a demo drive or public data
under a `demo-` or `public-` file or folder name, and the body names no private repository's PR and
no teammate by name, handle or email. The live drive, private code, Slack and customer or teammate
data belong in a private repository's PR or a direct message to him. `ccx vcs ship` runs the
`public-pr-body` hook over the body and `public-names` over each added line, and the
`landing_guard` hook refuses such a body in `gh pr create`/`edit`. After a fix, delete each earlier
revision and the pictures' branch, since both stay readable.

### Register and return

- Register the PR the moment it opens: `drive pr add <url> --job <j> --headline "<line>" --because
  "<line>" --does "<line>" --downstream "<line>"`. It records the PR waiting on his tap and opens
  his card with that lead: the headline as its title, under 90 characters, then why the change is
  made, what it does for him or its users, and what else changes once it lands. Write one plain
  line each with no function or file named, since the Summary names the code. `drive pr add` needs
  all four for a PR waiting on his tap, because they are what tells him whether to merge. A later
  `drive pr add` with only the lines it changes keeps the rest and redraws the card.
- These wait on no tap: a port (`--port-of <N>`), an exempt class (`--exempt <class>`), a PR of a
  `Drive tooling:` order outside `<guarded org>`, and one added with `--no-tap --why "<reason>"`.
  `drive pr add` and `drive pr set` refuse `--no-tap` on a `<guarded org>` PR.
- His standing approval is the mark `drive answer <id> --approves <order or key>...` keeps on his
  answer. A PR first registered on an order it names, or one Opus reads as inside its words and
  Notes, takes his Approve from that answer's link: no card draws, and the dashboard shows the
  Approve as progress under `{answer:<id>}`, with no line to him. A check against his words that objects takes that Approve
  back and his card asks him. His Hold on a covered PR, the close of its orders, or a newer answer
  superseding it ends it.
- Return `PUSHED <url>` once, when the head you mean to ship is pushed and registered, with CI and
  review still running. The watch holds a `PUSHED`, waking nobody, until its first GitHub read
  after the take finds every check the default branch's rules name green (at once when they name
  none), then takes it as `READY`, or sends a red one back to you. So you wait for no CI, review or
  queue yourself. The watch draws his card the minute GitHub reports the PR green and approved,
  once its `drive pr check` checked the change at that head against his words. A port's `READY`
  gets the coordinator's check against #N instead.
- Name every PR of a stack bottom first (`PUSHED <url> <url> …`) so he gets one card. Add a
  `SERVES:` line: `port of #N`, `Drive tooling: <what it unblocks>`, or the order it carries out.

## yasyf/* repositories

A `yasyf/*` PR merges with no tap, once every check at its head finished with none failing on its
own change, because a check that fails after the merge turns `main` red for every PR behind it. It
also waits for each open `<guarded org>/*` PR of its job to merge or close, unless that PR's waits
name it, so tooling does not land ahead of the code it calls.

A failed check is `main`'s, not the PR's, when `main`'s run of it, at the commit the PR's run
merged its head onto, failed every test the PR's run failed, as each job's log names them (pytest's
`FAILED` and `ERROR` summary lines, go test's `--- FAIL:`). One check is one verdict over a whole
job, so `main` failing one test says nothing of another. A failed check whose log names no failing
test, such as a lint step, is the PR's own.

1. Ship with `ccx vcs ship --no-watch` (jj in colocated repositories). The `open-pr` skill writes
   the body and the watch follows CI.
2. Register it with `drive pr add <url> --job <j>` and return `READY` or `PUSHED`, which needs the
   job's proof file kept with `drive job set <j> --repro <file>`. The watch and the lander do the
   rest, as below.
3. Install by hand only what the lander does not, from the merge sha
   (`gh pr view <n> -R <repo> --json mergeCommit`), as [Installs](#installs) says.

### The lander on a yasyf/* PR

- Stages: `grading` while a check at its head runs, `ready` once every one finished. A check
  failing on its own change reads `red`, owed by you, `its own change fails …`, and starts no
  lander. While `main`'s run of a failing check still runs, it stays `ready` for the lander to
  judge.
- The watch starts the lander within one GitHub read of `ready`. The lander is a process of its
  own that outlives your turn. It reads the checks again, waits for `main`'s run of each failing
  one, and merges with `gh pr merge <n> -R <repo> --<method> --admin --match-head-commit <head>`,
  or says `not landed: its own change fails …`. `<method>` is `squash`, else `rebase`, else
  `merge`: the first the repository's settings and its default branch's pull request rules allow.
  It records the merge sha on the PR, then runs `drive install` for each plugin installed on this
  Mac whose folder the merge changes, or that is its marketplace's only plugin.
- Refusals it handles itself: on `Base branch was modified` it merges again on the new base and
  leaves the branch alone. For a native GitHub stack, where `gh pr merge` says to use the
  asynchronous merge REST API, it merges the stack's bottom PR through GitHub's `merge-async`
  endpoint and reads the result until GitHub ends it. On a squash conflict with the base it rebases
  the PR onto it as one commit in the drive's own clone, merges each conflicting markdown page
  clause by clause with `git merge-file`, and pushes with a lease on the head it fetched. A
  conflict in any other file, or in a clause both sides changed, reads `red` and wakes its builder.
- After any other refusal the watch starts the lander again once the stage reads `ready` on a new
  head, so nobody registers a rebased PR twice.
- Its output goes to the drive's `landings.log`, ending in one line such as
  `yasyf/cc-skills#7: merged at <sha>; …`. The job's holder hears
  `PR … merged at <sha>; <each install's version line or no release: line>`, with each failing
  check it merged past named as `main`'s (`merged at <sha> past <check> red at <head>, which main
  failed on the same <n> tests at <commit>; …`, shown beside `checks fail` in `drive status`), or
  `PR … not landed: gh pr merge --<method> --admin refused it: <its answer>`.
- Once every PR the job's `READY` or `PUSHED` names merged and no lander runs on one, the watch
  starts `drive job prove <j>`. It installs each plugin a `yasyf/*` merge changes that an install,
  binary, Homebrew copy or Captain Hook.app lacks, then runs the proof file with those plugins'
  `bin/` first on `PATH` and `python3` the drive's own interpreter. Other merges go to the prover
  that proves them on their release. When the file fails while a `yasyf/*` merge's CI on the
  default branch still runs, it follows that run to its end and runs the file once more. It keeps
  the output beside the job's report and runs each drive command the brief's `After install:` line
  names. Exit 0 with output takes `DONE`. Anything else takes `BLOCKED`, quoting the last `FAIL`
  line, an exit 0 that prints nothing included. A nonzero exit ending on a `WAIT` line with no
  `FAIL` line takes no result and runs again once he is next heard. The holder hears
  `PR … proven live by drive job prove …`, `PR … drive job prove failed: …` or
  `PR … drive job prove waits on his next word: …`. A `DONE` that finishes the order sends the
  communicator its close report. A drive tooling order a session opened closes on its proof with
  no line to him.
- `drive status` says why no lander started: a failing check, a draft, the `hold` label, a
  conflict, an open card of his, or a lander already running. Other stages:
  - `behind`: a stacked PR until the one below merges. Also a PR on the head branch of an open PR
    the drive does not track, owed by someone outside the drive. Also a red PR whose builder's
    `WAITING` or `ASK` answered that red and named an open drive PR it waits on. Also a PR on the
    branch of a job that has opened no PR there yet, owed by him while that job waits on his cards
    and by that job otherwise; his card offers no Approve on it until that PR opens. None of these
    has a deadline or a wake.
  - `reviewing`: it waits on a teammate's GitHub review, a `review:<login>` wait, until the watch
    finds that review at its head. No deadline, no wake.
  - `red` for a PR on a branch that is no landing base and that no open PR heads: open that
    branch's PR while it holds commits the default branch lacks, else restack onto the default
    branch.
- A landing base is a branch the repository lands PRs on: its default branch, or one the
  `[landing]` table of `.claude/long-running.toml` on the default branch names in `bases`, such as
  a release line. A PR on one lands there and never reads as stacked. The lander refuses any other
  base.
- Read the checks once they finish, and fix one that fails after the merge forward in a follow-up
  PR.

### Installs

- A Claude Code plugin: `drive install <plugin>@<marketplace> --sha <merge sha>` (such as
  `long-running@skills`), which the lander runs itself. It waits for the release that holds the
  merge, updates the plugin in each scope with `claude plugin update --scope`, checks each holds
  the merge, and prints the installed version.
  - A release holds the merge when the merge is in the release's commit and in the tag of each
    binary the plugin pins through a `*.binrun` file, since a binary is built at its tag. While it
    waits it prints the newest release and what of it lacks the merge.
  - It waits only for a release that is coming. It reads the repository's workflows at the merge:
    a job calling `plugin-release-tag.yml` tags on any merge when its `paths` input is empty, else
    only when a commit since the newest `v*` tag changes one of its paths. When no release will
    come it prints `no release: <why>`, installs nothing and exits 0, and the lander's outcome
    reads `merged at <sha>; no release: …`. A plugin Autobump releases, as `long-running` is,
    waits as before.
  - It reads the `dependencies` floors of the release's `plugin.json` and installs each sibling
    below its floor first, because a plugin reading a sibling's new field crashes on the old one.
  - For long-running it runs the new install's `drive migrate` before any session reloads, which
    makes each one-time change the new code owes a drive's files.
  - A plugin whose binary has a Homebrew copy from `yasyf/tap` (`ccx`, `cci`, `cc-notes`, `cco`,
    `cc-present`, `cc-review`, `cc-slack`, `codex-ask`) gets that copy upgraded, since a session's
    bare `ccx` runs Homebrew's copy: `/opt/homebrew/bin` comes before plugin bin directories on
    PATH. It fast-forwards the tap clone with `git pull --ff-only`, runs
    `brew upgrade yasyf/tap/<binary>` (`yasyf/tap/cc-orchestrate` for `cco`) with
    `HOMEBREW_NO_AUTO_UPDATE=1`, and with `--sha` repeats every 20 seconds until the bare binary's
    `--version` is a release whose tag holds the merge. A process the drive starts runs each
    plugin's newest installed copy first.
  - Installs take turns under one flock on `plugins/.update.lock` in the Claude config directory,
    because two marketplace clones or two tap pulls at once corrupt each other.
  - It refuses, naming the scope, folder, version, commit and merge sha, when the update left an
    install at a commit without the merge or a binary at a release without it.
  - Claude Code treats a repository's checkout and its worktrees as one project, so the
    repository's first entry in `installed_plugins.json` is the one loaded and checked.
- Captain Hook.app runs every session's hooks. A captain-hook floor or release needs the app at
  that release, and restarting its helper touches every session on this Mac, which is his call each
  time. While the app is older the install holds and prints his Install now card, naming its ASK
  file once a job keeps one. Run it again once his tap or `drive answer` records his pick, and it
  runs `capt-hook helper install` first. The `bash_guard` hook refuses `capt-hook helper install`
  and a `brew upgrade` or `brew reinstall` of captain-hook until then, naming the card's ASK file.
- Reloads: a live session keeps running the installed scripts of hooks it registered at start, but
  loads new hooks, skills, agents and tools only on `/reload-plugins`. The install types that
  through Orca into each idle drive session that has not reloaded since the newest `lastUpdated`
  in `installed_plugins.json`. One detached `drive reload --seat` per terminal holds the
  terminal's lock in the state folder's `reloads/`, waits for the session's `sessions/<pid>.json`
  to read idle, types once, and waits for the transcript's `Reloaded:` line. It logs to the
  drive's `reloads.log`. An unanswered send is retried under the same
  `orca terminal send --retry-request <id>`, which Orca replays without typing twice. One Orca
  refused, or left unanswered for 30 seconds, is printed as the `orca terminal send` command and
  left for the next install. A session running outside the drive's checkout, the `drive-<drive>`
  worktree, gets `/exit` instead, so cci resumes it in the checkout; with no tmux session of cci's
  the reload opens the `orca terminal create` line of [Start or resume](start.md). `drive reload`
  does the same with no install. A reload draws no role block, so the `role_rules` check hands a
  session its rules whole at its next tool call when the installed digest differs.
- Each drive's dashboard hands itself to a newer long-running the moment an install replaces
  `installed_plugins.json`: the new install's drive takes the same listening sockets, and each
  watch ends before its next pass and its launch agent starts it on the new install.
- A tool this Mac runs: follow its repository's release flow, install the release holding the
  merge, and read the installed version back. Fix the source and release it, since a hand patch to
  an installed copy is lost at the next release.
- A compiled tool (cci, ccn, cc-slack, ccx, codex-ask, cc-transcript) need not wait for its
  release: build the merge sha with `go build -o ~/.vcache/scratch/<lane>/<binary> <main>`, taking
  `<main>` from its `.goreleaser.yaml` (cc-transcript: `cargo build --release`), and rerun the case
  against that binary.

## The monorepo

Ship through the repository's own `submit-pr` skill, on a branch off `dev`. Leave his own in-flight
branches alone, since he is working on them. The `landing_guard` hook refuses landing a PR someone
else opened that no order of his names, by [Neither](scope.md#the-four-kinds-of-pr).

A `<guarded org>` PR outside an exempt class lands on his Approve tap. That is how the lander works:
it lands a PR when all of these hold at its current head.

- Every required check is green.
- An approving review from the reviewer bot (`forge-pr-reviewer`) stands on that head.
- The latest `ai-review` run passed. When it holds the PR, fix each blocking finding, or accept a
  wrong one with `<pr scripts>/pr-reviews accept <slug> --why "<why>"`. A finding against his
  design is an `ASK`.
- Nothing holds it: no Hold tap, no `hold` label, no conflict with `dev`, no card or staging test.
- He tapped Approve on its card, or it is an exempt class. His approval in words, his order to
  merge a PR as it stands, and his pick on a card asking whether to land are his Approve too. The
  coordinator judges that and records it with
  `drive pr set <url> --tapped Approve --tap-source <his message's permalink>`, since
  `landing_guard` reads the recorded tap.
- **The presence limit.** A dangerous change merges or releases only while he is present: his word
  or tap within the last 15 minutes. Dangerous is what the root `AGENTS.md` lists (a migration, a
  backfill, a delete of user data, storage, consistency or write paths, a builtin, the parser,
  checker or VM, the runtime API), outside tests, and any change with a
  `staging test of #N before it lands` part. The reason is that these can break customer runs or
  lose data, and he wants to be there. While he is away the lander records
  `not landed while he is away` and lands it at his next word, his Approve standing. A unit Trunk
  queued before he went quiet is dequeued, and the `landing_guard` hook refuses a hand merge. A
  rollback, and a hotfix changing nothing dangerous, run without him.

### Exempt classes

An exempt PR lands with no tap once green. He sees no PR card for it: it is marked as its class
wherever he sees it, and its order's thread gets one line, such as "port of #N landed." The lander
first checks the class from the changed files, title and body at the head. A PR outside its class
comes back as `PR … not landed: <why>`, and a `ci-only` one loses its class, so his card asks his
Approve.

- `pure port`: a `Port:` PR that does what its old-tree PR #N does, line for line, with that PR's
  tests and nothing more. Record it with `drive pr set <url> --port-of <N>`. It lands after #N:
  while the drive tracks #N unmerged and without his Approve, the port reads `blocked` on his card
  on #N. `--port-of` refuses an #N the drive does not track unless GitHub reads it merged. A port
  that changes how data is stored (a migration, schema or `.proto` file) is not pure, because its
  data model is a design part of its order, and `drive pr add` and `drive pr set` refuse it.
- `test-only`: only test and fixture files.
- `ci-only`: only CI configuration and CI tooling: `.github/`, `.buildkite/`, and the CI tool's
  source, pipeline definitions and shims the repository lists under `## In this repository`. The
  class comes from the paths: the first registration of a PR he opened, by `drive pr add`, a
  builder's `READY` or `PUSHED` or a report's `FIX` line, keeps it `ci-only` when no class or port
  is named and every changed file is CI. A revert on an open incident's order keeps his tap.
- `flake fix`: the whole change fixes one named flaking test at its cause and adds no retry, skip,
  timeout or rerun. It serves his standing order to fix flaky tests, so the watch judges it against
  that order, whatever PR the flake reddened. Register it with
  `drive pr add <url> --job <j> --exempt "flake fix"` in the Bash call whose `ccx vcs ship` opens
  it, since the `bash_guard` hook reads that registration. In test or fixture code alone it lands
  with no tap. With any other file it keeps his tap.
- `main fix`: the change fixes the default branch's red CI forward, such as a test a merge broke.
  Register it with `drive pr add <url> --job <j> --exempt "main fix"` in the Bash call whose
  `ccx vcs ship` opens it. The class check reads the newest completed run of each GitHub Actions
  workflow on the default branch and refuses the class when none failed. No card holds it, so it
  lands at once, but a `<guarded org>` one keeps the tap its PR would wait on.
- `incident fix`: the immediate fix, such as the stop, or the root fix an incident's order ships,
  by [incidents.md](incidents.md). `drive pr set` refuses the class to a PR whose title or body
  says it reverts a change, which waits for his tap.

Record any other class with `drive pr set <url> --exempt "<class>"` and clear it with
`--no-exempt`.

### His tap

- It counts once per PR and carries across every new head: rebase, restack, review fix, retitle,
  and a push carrying out his own pick on a card. When a push changes what he approved, run
  `drive pr set <url> --fundamental "<what changed>"`, which clears the tap and any exemption and
  puts the PR back on his card with that line. Fold two PRs that carry out one decision of his into
  one, so he gets one card.
- His Approve is one field on every PR of the landing unit his card drew. His Hold clears it, and
  so does any reply of his in the card's thread, after which the card draws Approve again.
- It lands what the card he pressed listed then, whole, bottom first: the PRs of his card that
  share a stack (a native GitHub stack, or PRs each on the one below's head branch), with every PR
  under them, once each is ready. A PR that joined the card after his press holds nothing below it
  and gets asked alone, on the stack's card, which cc-slack redraws in place. A PR stacked above on a card of its own holds none of them, and his Approve
  on one card does not pass to another. An Approve in words or on the dashboard keeps its PR alone
  with the PRs under it. Other PRs of his card land in calls of their own, since `stack-enqueue`
  refuses PRs no stack links.
- It lands the PR at its first green head, a dangerous one while he is present, whatever head he
  tapped and whatever the check against his words says. A `NOT READY` at the head clears no tap and
  holds no landing: its `PR … NOT READY` line goes through `drive pr wake` to the builder of each
  PR it faults, once a head, only while neither his Approve nor an open card of his holds the
  stack. His `TAP Approve` line wakes no session.
- A review approval counts on the PR's current head. One GitHub still counts from an older head
  reads `approved on an older head` and draws no card. A repository's required approving reviews
  come from its default branch's pull request rule (GraphQL `requiredApprovingReviewCount`):
  `<monorepo>` requires one, and a repository requiring none lands at green.

### The lander on a monorepo PR

- His Approve or an exempt class lands the PR with no session turn. The watch's landing loop, a
  process of its own, reads GitHub, stages, wakes and lands every 60 seconds. His card tap starts
  the lander at once when the PR is green, bot-approved and free of conflicts.
- The lander enqueues into the Trunk merge queue from a fresh worktree of the default branch's
  head, made for the landing and removed after. It leaves the coordinator checkout alone. The
  worktree comes from the one clone this Mac keeps in the state folder's `clones/`, with no blob
  filter and LFS files as pointers, fetched under the clone's lock. It sets
  `MISE_TRUSTED_CONFIG_PATHS` to that worktree so the script's `uv` shim runs. A landing fix merged
  to the default branch holds for the next landing at once.

      <pr scripts>/stack-enqueue --check <PR>...
      <pr scripts>/stack-enqueue --watch <PR>...

- `--check` runs first. When its `BLOCKED` lines name an approval on an older head, the lander
  posts `@forge-pr-reviewer review`, once a head. `--watch` then waits out each wait in the
  lander's own process, such as that approval or GitHub computing mergeability, and queues the PR,
  or ends as `PR … not landed: <its last lines>` on the first gap that needs a fix.
- Whatever stops a landing (his Hold, the `hold` label, a card, a wait of the PR, a rework, his
  Approve coming off, his absence from a dangerous PR), the landing loop's next pass ends the
  running lander, which dequeues what it queued. `drive pr land --dequeue` takes out a unit Trunk
  still holds, by `stack-enqueue --dequeue` from a fresh worktree.
- Each head and base gets one try. A try that fails on the lander's own step says nothing of the
  PR: a `stack-enqueue` exit 5, a failure before any answer (such as mise refusing the worktree's
  config), a GitHub call that never answered (a TLS timeout, an HTTP 5xx) or one ccx refused. The
  watch sends it to the meta session once a head as `LANDER the lander could not …`, and the
  lander tries again at the next pass with no new tap. A try stands only at the head and base it
  ran on, so a push, or GitHub moving the PR onto the default branch, reads it `ready` again.
- Stages a try leaves:
  - `landing`: Trunk holds it and nothing stops it.
  - `merged`: owed by nobody, once the try merged it and read it back merged.
  - `waiting`: on a wait of its own.
  - `unlanded`: a try ended without queueing it, or it was dequeued with no wait holding. It wakes
    the session holding its order every 30 minutes for a builder to fix the cause and push; the
    lander tries the new head on its own. `UNLANDED_SILENT` when the process ended before saying.
    Trunk's `trunk-cancelled` label newer than the try records
    `dequeued: Trunk cancelled its merge queue entry`.
  - `blocked`: its own job waits on his open question cards. Owed by him, waking no one.
  - `red` on a conflict (`Pull Request has merge conflicts`, `the merge commit cannot be cleanly
    created`, or a queued PR GitHub reads conflicting): owed by its builder, "it conflicts with its
    base <branch>". `drive pr wake` tells the builder at once and every 30 minutes, and the rebased
    head lands on its own. In a native GitHub stack it reads "it conflicts with <trunk>, the trunk
    of its native stack: restack the whole stack onto <trunk> with ccx vcs stack rebase", since
    GitHub test-merges each member onto the stack's trunk.
  - `behind`: a ready PR while one his Approve lands with it cannot land, and every PR above a
    conflicting one. Owed by that PR's owner, so no builder above is woken.
- The same lander lands every other repository the drive tracks. Once his Approve or an exempt
  class clears a PR and it reads `ready`, it merges the stack's bottom PR with
  `gh pr merge --<method> --match-head-commit <head>`, then the next once it reads `ready` on the
  new base, by the method a `yasyf/*` PR merges by. After a rebase merge GitHub restacks the PR
  above as a new head, and his tap carries across. The lander runs outside any session, so it
  checks his tap and open cards itself and refuses a PR the drive does not track, naming
  `drive pr add`.
- The queue is the way in for a drive PR, because any other way skips the checks it runs. The
  `landing_guard` hook refuses `--admin`, `gh pr merge`, the merge API and a `merge`/`merge-fast`
  label on one. `<ci> trunk submit --direct` passes only on his ruling naming the PR and its full
  head sha. An incident fix lands as [incidents.md](incidents.md) says.

### Red checks and flakes

- A red on your PR is yours to clear, whether your change or `dev` caused it, and while it waits
  on his tap too. The one exception: a GitHub Actions job that failed at checkout on its first
  attempt, before any step ran the PR's code. The watch reruns that once with
  `gh run rerun --failed`.
- Read the same check on `dev`'s newest run first. A red `dev` shows too is inherited. The first
  job to meet it names the merge on `dev` whose run first failed it, then finds the PR fixing it
  (`drive status`, `cci who --pr`) or opens that fix itself, so one owner serves every PR it
  blocks. Restack onto a `dev` that has the fix, or fix it forward in its own PR, a `flake fix`
  when a test flaked.
- On a `yasyf/*` main the drive keeps that owner by test name: the first job to claim a failing
  test owns it until main passes it, and `drive job held --exempt "main fix"`, a main fix's ship
  and its `drive pr add` refuse a second job, naming the owner. A test main fails since a drive PR
  merged whose own run failed it belongs to that merge's builder, whom the watch wakes.
- Waiting on a fix in another PR, return `WAITING` naming it, which puts it on your PR's stage
  line.
- On a conflict or a queue ejection: restack, read the entry's history and verification build, fix
  the cause, and enqueue again.
- Fix a failing test at its root. A rerun grades the same merge commit, so it proves nothing. The
  `bash_guard` hook refuses `stack-enqueue --flake` in a drive session and a rerun of a build that
  did not pass (`<ci> bk rerun`, `bk build rebuild`, `bk job retry`, a `bk api` retry or rebuild,
  `gh run rerun`), naming `<ci> bk log '<job URL>'`. A rerun of a build that passed, such as a
  forced uncached regrade, runs. The watchdog's agent may rerun a failed build of a PR in a
  stall's chain once.
- A flaky test outside your diff needs no push while its fix is open. Fix it in its own
  `flake fix` PR, or find the one the drive tracks, then run
  `drive pr set <url> --flaky <the test> <its fix PR>`. Your PR reads `waiting`, waking nobody,
  until that fix merges.
  - Red at your own head: it waits only while every failing check is one the record kept. Once the
    fix merges it reads `red` and wakes you once to restack onto the fix. A fix closed unmerged
    reads `red` too.
  - Ejected by Trunk with your head green: the lander queues the same commit again with
    `stack-enqueue --flake` once the fix merges, once. A second ejection reads `unlanded`.
  - Ejected on a flake a requeue alone passes, such as a test failing only as the date changes:
    `drive pr set <url> --flaky <the test> "<its cause>"` names the cause in words and the lander
    requeues at once, once. Its fix still goes up the same day as a `flake fix` PR. A head whose
    own checks fail is refused this.
- An ejection whose record in cci's `trunk` drive gives a `GITHUB_API_` reason, such as
  `GITHUB_API_MERGE_PR_FAILED`, with no failing check needs no push: Trunk tested green and GitHub
  refused the merge. The watch says `PR … merge refused by GitHub …` and the lander lands the same
  PRs again one at a time, bottom first; a second refusal at that head reads `unlanded`. Trunk's
  own `Trunk Merge Queue` check counts as no check.

## Staging tests in the monorepo

A dangerous monorepo change lands once a short staging test of its head has passed, because
untested dangerous changes have caused outages. Infra and blue-green changes count above all. This
is prose the drive keeps with local tooling: add no ruleset, commit status or PR check that every
PR hits, and the monorepo hooks into the drive through its `.claude/long-running.toml`.

- **Which changes.** Each stack's `<ci> release plan` decides: a change that plans like dev passes
  untested. When `<ci> release select --pr` names none of a PR's releases as `staged` (an env
  marked `stage: staging`), and none is the new tree's Sand CLI or the desktop app, which
  `<ci> mac-box prove` signs in to staging, staging cannot test it (Buildkite, the identity
  account, Datadog). The tester then runs each stack's release dry run from this Mac and asks his
  exception with the counts each preview plans. His yes drops the part and the PR lands on his
  Approve.
- **How it runs.** Short, aggressive and agent-led, exercising every scenario that could break on
  production, then a 2-hour soak after the merge. Changes touching different files share one cut,
  one freeze and one claim. The drive starts each test on staging with no card and no `--order`.
- **Its tools.** The steps are the monorepo `AGENTS.md`'s **Landing** rule. `<ci> release soak`,
  once on `dev`, freezes staging, deploys the branches, waits for their tests and unfreezes. It
  stamps the database's staging rollout with the tested head, and its `--fail` redeploys the
  soaked stacks at the latest green release candidate. `<ci> staging-smoke soak` runs your
  scenarios on staging as the smoke-test account.
- **Its restore.** Staging goes back to the commit it served before the test with
  `<ci> release start <each soaked target> --from <restore commit> --emergency --env <staging env>`.
  The restore commit is an empty commit on the base, `git commit-tree <base>^{tree} -p <base>`,
  pushed on a branch of its own such as `<job>-staging-restore-<base>`, so `buildkite/test` grades
  it. Naming the base itself fails: the soaked cut descends from it, so `<ci>` reads it as already
  shipped and applies nothing.
- **Its claim.** The test claims staging as the monorepo's `staging-claim`
  [repo rule](concepts.md) says, in a claim whose `until` the watch's `DUE` line follows, by
  [the records page](records.md). Its freeze names its job:
  `<ci> release freeze --with-staging --holder <drive>/<job>`, once on `dev`. While another drive
  on this Mac holds a live claim, the `landing_guard` hook and `drive job held --cluster` refuse a
  Job on `<staging env>`, naming that claim. Wait behind it with
  `WAITING staging:<what you test>/<staging env>@<its commit>`, then claim staging yourself.
- **What holds the PR.** The pass is the drive's, not GitHub's. Three things put a PR on its test:
  - The builder's `READY` or `PUSHED` carries `STAGING: #N <cases>`, which `drive job report` turns
    into the part `<order>:staging test of #N before it lands`, the PR's wait on it and its tester.
    When another staging part already names the PR it becomes a wait on that test.
    `STAGING: none: <why AGENTS.md's dangerous rule misses it>` says no test is owed.
  - `drive pr add` and the watch, on each new head, read the changed files against the
    `[[dangerous]]` tables of the monorepo's `.claude/long-running.toml` (a class and its path
    globs, less test files and `[skip]` globs) and give a matching PR the same part, wait and
    tester before his card draws.
  - `drive pr add`, `drive pr set` and the lander do the same for each PR waiting on his tap whose
    paths reach a component `<ci> release select --pr` lists, as
    [Cards and tests that hold a PR](#cards-and-tests-that-hold-a-pr) says.
- A later `STAGING: #N` line reaches the running tester as held text. `STAGING: none: <why>` or
  `STAGING: #N none: <why>` lifts a test no job runs now that the release reach did not give, its
  why shown on his card. A running test goes to its end.
- The lander lands the PR once the tester's `DONE` keeps the test's proof as its first `CHECK:`
  line, by [the jobs page](jobs.md). The pass keeps each Approve he gave before it, so one Approve
  lands the PR once its test passes, under [the presence limit](#the-monorepo).

## Before any change FDEs could see

Before writing a change to anything forward-deployed engineers' or customers' code can observe (a
builtin, an API or SDK call, a consistency level, a default, a schema, a wire or storage format),
run `drive fde-scan -- --symbol <name>`, with one `--symbol` for each builtin, function or table
method the change touches. Their code breaks silently otherwise, so this holds whatever the order
says. It runs the monorepo's `<ci> fde-scan` in the drive's checkout at the head of `dev`, on the
coordinator's Mac for a Sprite lane too, since an older branch has no scan to run. Its report is
the PR body's `## FDE impact` section, whose first paragraph his card draws under the Summary.
`drive pr add` refuses a body saying the scan comes later. Fix each caller it finds in the same
stack, or put it on a card.

## Read-only repositories

Customer and forward-deployed engineers' repositories are read only: clone and read. He merges
those himself, so a change that belongs there comes back as `ASK` with the patch.

## After merge

Merged is not live. Prove it before `DONE`:

- A plugin or tool: its installed version contains the merge, or a compiled tool's local build of
  the merge sha reruns the case, by [Installs](#installs).
- A monorepo change: its release is live on the targets it touches, shown by its card in
  #releases.
- Then rerun the reporter's exact case on the live build and return `DONE <proof>`: one line on
  what changed for him, and the link or version that proves it.

### Releases

The top coordinator starts a release his words cover, on its own reading of them. It judges
whether his order, his pick on a card or his plain words cover a release, starts it, and says in
one line what it started and why. His next word steers it. Nobody waits for a quote, a second
session's confirmation or a card first. A change no words of his cover rides the next release.

- **How it starts.** One call from the top coordinator:
  `drive job add builder --order <order> --goal "<goal>" --release "<ci> release start <target> --from <commit> --order <his answer>"`.
  It starts the release from the top's checkout in a process of its own, keeps its build, and
  writes the job's brief, whose `Release:` and `Release build:` lines name the command and build.
  The job's agent starts once the watch's FREE line names it. `--order` names his answer when one
  exists. Opus reads that answer; when Opus is unsure, add
  `--reading "<why his words order it>"`, which the job keeps for the release's resume, approve,
  rerun and rollback. The verb starts the release in the same call, so run the job tool's
  `dry_run` first. The `landing_guard` hook refuses a `<ci> release start` or `hotfix` a session
  runs itself.
- **The release job works out order and holds.** It reads the `<ci> release plan`, picks the
  smallest release that meets his goal, and proceeds: a `<ci> release hotfix` of the fix his order
  names, else the cut `--without` each stack holding a dangerous change no staging test passed,
  else the whole cut. It names its choice in its return. Order does not block the start.
- **Scope.** A release ships every target his words do not exclude. Leave a target out only for a
  failing test, written `<target> fails: <the failing test>`, or because his words exclude it,
  written `<target> left out by his <id>: "<his words>"`. The `landing_guard` hook reads the
  release's `status` record on approve and resume and refuses a production release carrying more
  than half the platform's targets that leaves one out with neither line in the job's goal or
  brief, and `drive job ask` refuses a card naming such a start. The repository names its release tool in the `[release]` table of
  `.claude/long-running.toml`: `targets` (a command printing the JSON list of targets), `status`
  (a command with `{release}` printing a JSON object of `name`, `build`, `order`, `commit` and the
  targets it `carries`), `whole` (the start's word for every target), `components` (a command with
  `{pr}` that reads a PR's changed paths on its stdin and prints the JSON of the components it
  reaches, which staging slots pack passes by), and `holder` and `join` (the flag naming a job,
  `{holder}` as `<drive>/<job>`, as a freeze's holder, and the command adding one to the freeze
  `{freeze}`, which a staging slot's `GO` and `RIDE` lines name for every job of a packed cut).
- **A green, approved PR does not hold a cut.** When only its staging test holds such a PR on an
  order a production cut serves, the watch's `release_lines` takes the test off and the lander
  queues it within minutes. The cut waits on that landing, carries the component, and its staging
  retest reruns only the cases that PR touches. A dangerous PR waits on the presence limit; his
  pick to merge it is recorded with
  `drive answer <his answer> --dropped <order>:staging test of #N before it lands`.
- **Drive tooling does not hold a release.** A release-path PR whose staging cases passed while
  the drive refused or never kept their `DONE` lands once that `DONE` is kept, the turn the defect
  goes to meta, since `drive order set --drop-part` refuses an open PR's staging part.
- **The one wait.** A release carrying a dangerous change starts, resumes and is approved only
  while he is present, by [the presence limit](#the-monorepo). A rollback runs at any hour. A
  hotfix of pull requests that change nothing dangerous, carrying no other change, runs
  without him, on any order. So does a release whose every place is off production. A cut of the
  default branch is held while he is away, because the gate cannot read what it carries past the
  live commit, and so is every approve hold, which holds a migration, delete or replace: ship the
  pull requests that change nothing dangerous as a hotfix, and the rest returns `WAITING his word`.
- **The approve hold.** The job's agent reads each held migration, delete and replace against the
  code at the release's commit, then runs `<ci> release approve <id> --via <agent>`, naming in
  `--leave-out` each stack it cannot vouch for. It decides this itself and keeps the release
  going. An incident's order covers the incident's own cut, by [incidents.md](incidents.md).
- **Staging and sandbox need no word.** A release whose every place, its `--env` places and its
  stacks' envs, is `<staging env>` or `<sandbox env>`, and that is no `--for-production` start,
  starts, resumes, approves and reruns with no `--order` and no card, the repository's
  staging-claim post going first on staging. `drive job ask` keeps a card whose recommended option
  names only such releases decided on that option and draws no card. Each `--stack <project>/<env>` reads as its project
  among the targets and its env among the places, and `--commit` as `--from`; a release naming no
  place runs on production. A staging start with `--for-production` runs on both staging envs
  through every freeze, so it counts as the production release it tests.
- **His hold.** His answer labelled `release-hold` that no later answer supersedes stops every
  release past staging and sandbox until an answer of his after it allows one. That is his word,
  so it stands. The top coordinator judges whether a later answer lifts it or covers a release.
  Record a new hold with `drive answer --label release-hold`, and once he says releases can go,
  `ccn answer supersede <hold> --by <id>`. While it stands `drive job add --release` and the
  `landing_guard` hook refuse other release calls and quote the hold: `<ci> release resume`,
  `approve` and `rollback`, a `<ci> bk rerun release`, a `<ci> bk api` write to a release cut, and
  a forge/release build whose env holds `RELEASE_START`. A `<ci> bk api -X POST` creating such a
  build counts as a cut unless its inline `-d` JSON sets a `CLUSTER_JOB` env alone.
- **Cluster jobs under the hold.** A `<ci> cluster-job` build is no release cut, but the hold
  covers a Job it starts on any cluster past staging and sandbox: starting it, creating its build,
  unblocking its gate and rerunning it. Name his answer's id in `--reason`, which the build keeps
  in `CLUSTER_JOB`. When Opus is unsure, the top coordinator starts it on its own reading with
  `--reason "<answer id> reading: <why his words order it>"`. While he is away the hook refuses
  that start on a production cluster. A Sprite lane asks with
  `drive job held --cluster <env> --cronjob <name> --reason <why>`, and for a release with
  `drive job held --release`. The hold reaches a drive session, its agents, a Sprite lane, a codex
  job (through the hook `drive job launch` keeps in `~/.codex/hooks.json`) and the watchdog, and a
  wrapper such as `drive break-glass`, `timeout` or `xargs` changes nothing.
- **Freezes.** `<ci> release freezes` reads each freeze standing now. A job's brief carries them and his hold on a `Release freezes:` line, and `drive status` adds
  the line when a shown job or PR waits on a release. `say` and `record` refuse words saying a
  release may start that leave out a standing freeze or hold.
- **One cut at a time.** One cut carries every project's changes, so the top coordinator starts,
  approves and rolls back production cuts. A sub-coordinator lands and installs its project's
  changes. Its job returns `WAITING release` naming the merged changes the cut must carry, and
  the sub-coordinator sends the top a `release` record at `root@<drive>` naming them. The top
  reads it and starts the cut on the record as given, without asking the sub-coordinator again.
  `drive job add --release` reads his answer itself, and its refusal goes back to the
  sub-coordinator as the record's answer. The release's own job follows the cut to its approve
  step and approves it there, with no round trip to the top. The `landing_guard` hook refuses
  `ci release start`, `resume`, `approve` and `rollback` and a cut's rerun or unblock from a
  sub-coordinator's session and its agents; `--plan`, `--dry-run`, `--help`, `status` and `watch`
  run. A job with a `Release:` line is the top's on any order.
- **Release-order notes.** A release-order rule is a cc-notes note labelled `drive:<drive>` and
  `release-order` or `release`, whose title names the PR or commit it turns on. Nothing refuses on
  one. `drive job ask` and `drive job add --release`, `--check` and `dry_run` included, have Jev
  read each note in a start's range and print whole each one not silent on that start, with what
  git finds for the PRs and commits its title names. Follow it: take a target it forbids off the
  start, or name its hazard in your report.
- Leave a release he started himself alone, since he is driving it.
