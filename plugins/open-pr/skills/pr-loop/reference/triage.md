# Triage: fix now or bring it back

One question sorts every event: does the failure itself determine the fix?
When the tool prints the corrected content, when exactly one edit resolves
the error, when the reviewer wrote the replacement text — the fix is
determined, and applying it is mechanical. When fixing requires choosing
what the code should do — which behaviour is right, which approach wins,
whose consent is needed — the choice belongs to the human, framed as 2–4
concrete options with a recommendation.

Every fix, however determined, passes the four gates in SKILL.md first.

## Fix now

### A formatter or linter that prints the corrected file

`gofmt -l` names the files and `gofmt -w` rewrites them; `ruff format`,
`ruff check --fix`, `prettier --write`, `eslint --fix`, and
`golangci-lint run --fix` are the same shape. Run the fixing form, confirm
the check's local equivalent passes, ship.

<example label="determined">
CI: `ruff format --check` fails on `src/api/routes.py`. Run
`ruff format src/api/routes.py`, re-run the check form, ship. The
formatter's output is the fix.
</example>
<example label="looks similar, is a decision">
CI: `ruff check` flags `PLR0912` (too many branches) with no `--fix`
support. Restructuring a function is a design choice — bring back "split
into helpers, add a scoped noqa with a reason, or raise the limit?"
</example>

### A compile or type error with exactly one resolution

A missing import, a field the diff renamed at every other call site, a
signature the diff already changed elsewhere — the rest of the diff has
already decided the answer; the error marks the one spot it missed.

<example label="determined">
The diff renamed `Config.timeout` to `Config.deadline` in four files; the
build fails on a fifth still reading `.timeout`. The rename is the diff's
own decision — apply it to the fifth site.
</example>
<example label="looks similar, is a decision">
`cannot use x (type int) as type Duration` on a line the diff added —
multiplying by `time.Second` and casting both compile, with different
meanings. Two resolutions means a decision.
</example>

### A stale generated artifact

A lockfile, a generated client, a snapshot the diff was supposed to
regenerate. The generator lives in the repo; run it and ship when the
regenerated output is the mechanical consequence of the PR's own edits.

<example label="determined">
`go.sum` out of sync after the diff added an import — `go mod tidy`
regenerates it; the delta covers exactly the new dependency.
</example>
<example label="looks similar, is a decision">
A snapshot diff showing a rendering change the PR never intended — updating
the snapshot would bless a regression. Bring back the before/after.
</example>

### A review comment carrying a ```suggestion block

The suggested text is the fix, already written by the reviewer. Apply it
exactly at the anchored lines, ship, and post the drafted reply on the
thread once it lands.

### A bot nit naming the exact replacement

"use `errors.Is(err, os.ErrNotExist)` here" — the replacement is quoted;
apply it. A bot comment naming a problem without its replacement ("this
could be simplified") is a review opinion, and review opinions come back as
options.

## Bring it back

Each of these comes back as one message: what is red or being asked, what
was already tried, and 2–4 options with a recommendation.

### A test failing on behaviour

The test and the code disagree about what should happen; the failure alone
can't say which is wrong.

> `test_retry_backoff` expects 3 retries; the diff's new rate limiter caps
> at 2. Options: (a) update the test — the cap is the PR's point
> (recommended; the PR description says "reduce retry pressure"); (b) exempt
> retries from the cap; (c) raise the cap to 3 and document the interaction.

### A reviewer asking why, proposing a different approach, or questioning scope

Answering commits the author to a position, and that voice is the human's.
Bring the question with what's needed to answer it in one read — the thread,
the code it points at, and the plausible answers as options.

> Reviewer: "why a new retry helper instead of the one in `pkg/backoff`?"
> Options: (a) switch to `pkg/backoff.Retry` — it lacks jitter, which the PR
> adds, so this means extending it first; (b) keep the new helper and reply
> explaining the jitter gap (recommended — smaller diff, reply drafted);
> (c) add jitter to `pkg/backoff` in a follow-up and use it here.

### A CLA, DCO, or issue-first requirement

Signing needs the user's identity; consent isn't delegable. Report the exact
requirement and its link. A DCO failure on the user's own commits has a
mechanical half — `git commit --amend -s` and a re-push — but the sign-off
statement is the user's to make, so present that as the single option and
wait for the go-ahead.

### A fix that would touch a file outside the PR's diff

Gate 3 blocks the edit; the bring-back explains it. The fix may still be
right — as a follow-up PR, or as a scope expansion the user approves.

> The failing test imports a helper, and the bug is in the helper — a file
> outside the diff. Options: (a) expand this PR to include the helper fix —
> one more file, keeps this PR green; (b) open a follow-up PR for the helper
> and report this check as blocked on it.

### An infra flake on a repo the user doesn't control

A runner timeout, a 502 from a package registry, a canceled job. On the
user's own repo, `gh run rerun <run-id> --failed` is theirs to run. On
someone else's, the rerun belongs to the maintainers — report the flake with
its evidence and draft the ask ("would a maintainer mind re-running CI?")
for the user to post.

### The same check red after two attempts

Two entries in the `attempts` map means two shipped fixes and the check is
still red — the working model of the failure is wrong, and a third attempt
is a guess. Bring back what each attempt changed, what the check said each
time, and where a human eye should look first.

## State file

`<cache>/pr/<number>.json`, where the cache dir comes from
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-cache.sh" path <owner/repo>`:

```json
{
  "pr": 123,
  "repo": "acme/widgets",
  "head_at_last_pass": "4f2a91c0e8b7d6a5c4f3e2d1b0a9f8e7d6c5b4a3",
  "watermarks": {
    "issue_comments": "2026-07-26T04:11:09Z",
    "review_comments": "2026-07-26T04:13:52Z"
  },
  "checks_seen": { "test (3.12)": "failure", "lint": "success" },
  "attempts": { "test (3.12)": 1 },
  "applied": [
    {
      "at": "2026-07-26T04:20:31Z",
      "check": "lint",
      "action": "ruff format src/api/routes.py",
      "commit": "9be04d7"
    }
  ]
}
```

- `head_at_last_pass` — the PR head the last time every check was green; a
  differing current head means commits landed since.
- `watermarks` — ISO timestamps passed as `?since=` to the comment
  endpoints so each comment surfaces once; `pr-poll.sh` advances them as it
  emits.
- `checks_seen` — last known bucket per check; `pr-poll.sh` emits a `CHECK`
  line only on change.
- `attempts` — per check, the count of shipped fixes while it stayed red.
  Increment after each ship targeting the check; clear the entry when the
  check goes green, so a fresh failure on new commits starts a fresh count.
  This map is what carries the two-attempt cap across a restart or session
  boundary — an in-memory count evaporates with the session.
- `applied` — one record per shipped fix: timestamp, target check, the
  action taken, the commit. The loop's audit trail, and the material for
  the bring-back after a second failed attempt.

Update `attempts` and `applied` in the same step as the ship, before
returning to the watch — a fix shipped but unrecorded invites a third
attempt from the next session.

## Delegation recipes

Logs and review threads are unbounded; the two triage agents read them in
their own context and return bounded conclusions.

### Red check → cc-context:ci-triage

The run id comes from the `CHECK` line's link (`/actions/runs/<run-id>/…`)
or `gh run list --commit <headRefOid> --json databaseId,name`.

```
Agent(subagent_type: "cc-context:ci-triage",
      prompt: "Run <run-id> on <owner/repo> (PR #<pr>, check '<name>').
               Return the root cause, the minimal log excerpt proving it,
               and the concrete next step.")
```

Sort the verdict with the taxonomy above: a determined fix goes through the
gates; anything else joins the next bring-back.

### Review or comment burst → cc-context:pr-review-triage

```
Agent(subagent_type: "cc-context:pr-review-triage",
      prompt: "PR #<pr> on <owner/repo>, review <review-id>. Per comment:
               a verdict (mechanical fix, needs decision, or reply only),
               the concrete change, a draft reply, and the gh api recipe
               to post it.")
```

One spawn per burst — a review with nine comments is one delegation.
Mechanical verdicts (suggestion blocks, named replacements) go through the
gates, and their drafted replies post after the ship; decision verdicts
carry their drafts into the bring-back for the human to approve before
anything posts.
