# Spike register-rulings-tool: findings

## 1. Question

Can plan 1.9's register tool be `rulings.py slice`, `classify`, `check-pr` (the six refusals of C221 and C222) and `summary` over a cc-notes ledger, with pytest tests? Which cc-notes ledger primitives does it sit on, what is missing, how does `check-pr` read `## Rulings`, and how many lines is it?

## 2. What I built

- Repo `yasyf/cc-skills`, branch `spike/register-rulings-tool` off `main` at `eaababf4`. Box: Namespace `6gdp8ne1fgo36`, ccn v0.71.0.
- `d5283258`: `plugins/long-running/skills/long-running/scripts/rulings.py`, 168 to 425 lines (+257, 207 non-blank). Of those, 7 are usage lines, about 45 are argparse, and the rest is the tool: a `Stub` (JSON file) and a `Notes` (ccn) store with the same four methods, plus the slice, classify, grade and summary functions.
- `124ba7f3`: `plugins/long-running/tests/test_rulings_register.py`, 252 lines, 30 tests. `uv run --group dev pytest -q tests/test_rulings_register.py` gives 30 passed in 0.07 s.
- I also ran each verb by hand once against real `ccn` in a scratch repo on the box. Classify, refuse, slice, summary and check-pr all behaved as they did on the stub.

## 3. What worked

- The ledger kind fits the register. `ccn ledger list --label register:<slug> --json` finds it, and `ledger show --json` returns `rows[].fields`. `ledger row set --field=NAME=VALUE` merges fields last-write-wins, so `classify` (kind, anchors) and the graders (status) write different fields of one row without clobbering each other. Values may hold newlines and `=` (probed), so list fields are newline-joined strings.
- `ccn show <id> --json` resolves any record kind (answer, doc, note) to `title`/`body`, so the check that a row's words are a substring of its capture works against whatever record holds the capture. Whitespace is squeezed before the compare.
- `ccn answer list --label authorizes:<key> --limit 0 --json` is the authorization lookup. A `head:<sha7>` tag scopes an answer to one head, and a prefix match accepts the full head sha.
- Each of the six refusals fires alone on its own fixture. `--incident` keeps only `RULINGS-UNCITED`, `RESERVED-NO-ANSWER` and `TEST-REMOVED`. The C222 fixture (a broken row, a contested row and an open finder major) passes under `--incident` and draws all three refusals without it. `r2-d2-approval`, with `status=superseded`, never raises `RULING-CONTESTED` on step 10's data job, and the seed label on `002a374` authorizes that job.
- How `check-pr` reads a PR body: the `## Rulings` section runs to the next `## ` heading. A cite is a bullet's leading key, backticks optional (`` - `pick/fork`: honored ``). A key listed under a later section is not a cite, and a cited key the register lacks is refused as `RULINGS-UNCITED`. A row whose `paths` globs match a changed file must be cited.
- Speed on a 150-row real ledger: `ledger sync` 13 ms, `slice` 58 ms, and `check-pr` 175 ms with 22 reserved hits (one `answer list` per hit). The tool will not be the bottleneck in a hook.
- `slice` stamps `rev=<sha12>` over the rows it carries, and `--refresh BRIEF` rewrites the one slice block in place. That rev is what a stale-slice hook (C226) compares against.

## 4. What broke or does not exist

All cites are at cc-skills `eaababf4` unless named otherwise.

- **No writer provenance in cc-notes.** Every answer's `author` and every row's `updated_by` is the git identity, `yasyf <709645+yasyf@users.noreply.github.com>`, for the owner and every agent alike (probe output). An `authorizes:<key>` answer from a hand `ccn answer add` reads the same as one from a merge-card tap. So C224's "refused: ... a hand `ccn answer add` carrying an `authorizes:` label" can only be refused at write time, and cc-notes records nothing a grader could recheck.
- **The only-writer hook does not exist.** No hook in `plugins/long-running/capt-hook/hooks/` guards `ccn ledger row set`/`sync`. The cc-notes MCP server also exposes `ledger_row_set`, `ledger_sync` and `ledger_row_rm`, so the hook must cover Bash and all three MCP tools. capt-hook sees only tool calls, so `rulings.py`'s own `ccn` subprocess passes. So does `python3 -c "subprocess.run(['ccn', ...])"`.
- **JSON shapes differ.** Answer JSON names its labels `tags`; ledger JSON calls them `labels`. The adapter reads `tags`.
- **No field delete.** `row set --field status=` stores an empty string (probed). Clearing a field needs `--replace` with every other field restated, so graders must write `standing` and never clear the field.
- **Fields are flat strings.** `--where steps=4` matches only a row whose whole field is `4`, not a list member, so `slice` filters client-side over the full `ledger show`.
- **No row-level supersede.** `ccn answer supersede` works on whole answers, but `r2-d2-approval` is one pick inside answer `d5f9c22`. The row needs `status=superseded` by convention.
- **Not every capture is a record.** Owner words from chat, Slack or a board are cc-notes records only once someone writes an answer. Design rules s1 to s9 and E1 to E14 are plan text, not owner words; their capture is the plan doc.
- **Finder majors have no register source.** `FINDER-MAJOR` reads findings that live with pr-reviews and the open-PR ledger, not in register rows. The spike takes `--findings FILE`.
- **Reverts and rollbacks are mostly invisible to a diff.** Ops patterns over added lines cannot see a revert, whose diff is an ordinary inverse diff. The spike also scans the PR title (`^Revert `), which catches only GitHub's revert button and `git revert`'s default subject. A rollback through `tools/ci release` is not a PR at all.
- **Gate removal is not a PR diff.** It is a ledger edit, so `check-pr` cannot see it.
- **Seed authorizations never expire.** Labels such as `002a374` carry no head, so they authorize every future head whose added lines match the row's ops. The spike keeps them narrow only by giving `reserved/old-cloud-agents` its own ops (`deleteOldCloudAgents`), separate from `reserved/user-data` (`DeleteObject`).
- **Two things are called "register".** `rulings.py register` (rulings.py:70-75) and `capt-hook/hooks/register_injection.py:15` inject the `standing-rules:<program>` doc, a different store from the 1.9 ledger.
- **Merge paths do not consult the register yet.** `capt-hook/hooks/lane_landing.py:229` allows an incident lane's `gh pr merge` outright. `desk-runner.py:1716` (`launch_refusal`) has no slice check (C225). `desk-runner.py:316` and `:3276` enqueue a `MERGE-ASK` prefix once a root `go` record names each head, which is the record C224 refuses as authorization.
- **The monorepo PR body has no Rulings section.** Monorepo `.agents/skills/submit-pr/references/pr-body.md:55` (at `d10654981c`) fixes six sections, and `## Rulings` is not one of them.
- **Facts differ from the brief.** The box has ccn v0.71.0, not the brief's 0.72.11; the ledger verbs match. The script lives at `plugins/long-running/skills/long-running/scripts/rulings.py`. The plugin is 0.7.197, not 0.7.173, though the file is still 168 lines.

## 5. Plan deviations

- "`rulings.py` in the long-running skill is the only writer, and a hook refuses any other." Reality: no such hook exists, every write carries one identity, and three MCP tools also write. Change: name the hook (PreToolUse on Bash `ccn ledger row|sync|edit` naming the register id, plus MCP `ledger_row_set`, `ledger_sync`, `ledger_row_rm`), and state that it fails open on an unparseable command. **High.**
- C224: "refused: ... a hand `ccn answer add` carrying an `authorizes:` label". Reality: after the fact this is indistinguishable from a real authorization. Change: keep authorizations as register rows `auth/<key>/<head>` that only `rulings.py authorize` writes, with the tap ts as a field, instead of answers any lane can add. **High.**
- "Every PR body carries `## Rulings`." Reality: monorepo pr-body.md:55 allows six sections, and no cite format exists. Change: 1.9 adds the section and the format `` - `<key>`: <how honored> `` to monorepo submit-pr and cc-skills open-pr. **Medium.**
- "Reserved rows are deletes of user data and reverts or rollbacks (36a298d)." Reality: `check-pr` sees a diff and a title only, and a release rollback has neither. Change: release tooling grades rollback rows, and `check-pr` grades delete rows. **Medium.**
- "The seed labels 7ab71e2 (the base revert) and 002a374". Reality: a label with no head authorizes every future match. Change: give each seed label a narrow reserved row of its own, or a step scope that check-pr enforces. **Medium.**
- "Passing a reserved row or removing a gate takes an answer". Reality: removing a gate changes the ledger, not the PR, so `check-pr` cannot see it. Change: gate removal becomes a `rulings.py` verb that checks for the answer. **Low.**
- "steps, test ids, paths and symbols for the anchored kinds". Reality: the plan never says which kinds are anchored, and every ledger field is a string. Change: name them (the spike uses order, pick and exception) and fix the encoding as newline-joined. **Low.**
- C221 "each of the six refusals". Reality: C221 names three codes and C222 the other three. Change: list all six in C221. **Low.**

## 6. Ruling conflicts

- **42f245b against the Oct 17 owner rule that guard hooks fail open** (a standing rule with no answer id on this box). 42f245b says every ruling stays in force through tools that read the register. A check-pr hook on `gh pr merge` passes any merge command it cannot parse or that times out, so on that path the refusals are advisory.
- **3f34533 (the merge-card tap).** The desk-runner today enqueues on a root `go` record (desk-runner.py:316), which C224 refuses as authorization. Reading the tap means reading the root transcript, a mechanism cc-notes lacks, which strains a45e416 and 972c92f (no new mechanisms).

## 7. Open decisions for the owner

- Where do authorizations live?
  - (a) cc-notes answers tagged `authorizes:` and `head:`, behind a hook. Cost: any lane that slips past the hook can forge one.
  - (b) Register rows `auth/<key>/<head>`, written only by `rulings.py authorize`. Cost: one more verb, and the same identity limit.
  - (c) A cc-notes feature for source-stamped answers. Cost: a cc-notes release.
  - (d) check-pr re-reads the tap ts from the root transcript. Cost: the transcript must sit on the grading machine.
- How long does a seed authorization last?
  - (a) Forever, with one narrow reserved row per authorized action. Cost: the register grows a row per action.
  - (b) Scoped to path globs. Cost: the globs drift as code moves.
  - (c) One-shot, consumed by the first merged head. Cost: a ledger write on every merge.
- How much of each global row goes into every brief?
  - (a) The full words. Cost: about 2 lines per row, roughly 60 to 90 lines per brief for about 31 globals.
  - (b) The key and kind only, with `rulings.py show <key>` on demand. Cost: lanes no longer read the owner's words verbatim.
- What is the `## Rulings` cite format?
  - (a) One bullet per key, as the spike does. Cost: long bodies on wide PRs.
  - (b) One line, `Rulings: k1, k2`. Cost: no room to say how each row is honored.

## 8. Effort re-estimate

The pasted plan gives no number for 1.9. The core (four verbs, two stores, 30 tests) took about 25 minutes of box time at 257 plus 252 lines. What remains:

| Work | Size |
| --- | --- |
| `authorize` (C224) and its transcript or tap reader | about 80 lines |
| The writer-guard hook with inline Input/Allow cases | about 60 lines |
| The C226 slice hook, comparing `rev` | about 50 lines |
| `launch_refusal` (C225) | about 15 lines |
| check-pr wiring into `lane_landing.py` and the desk-runner landing pass (C221 to C223) | about 60 lines plus tests |
| Seed census import | about 40 lines |
| `## Rulings` in the monorepo pr-body spec | a docs PR |

The total is about 1.5 to 2 lane-days, most of it the merge-path wiring and the authorization decision, not the register itself.

## 9. Unfinished

- `authorize` (C224) is not built.
- No stale-slice check reads `rev` (C226).
- check-pr is not wired into `lane_landing.py` or the desk-runner.
- There is no writer-guard hook and no seed import.
- The `program: <slug>` drive-registry key does not exist.
- The diff parser ignores renames and binary files, and `gh pr diff` is not paged for large PRs.
