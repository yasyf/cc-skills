# The finder brief

Every drive head gets one finder pass: one `gpt-6.1-sol` reader at xhigh on the
standard tier, run through `Skill(codex)` with its finder recipe
(`codex-ask --schema findings`). The finder looks for defects in the diff, the way
any finder does, and it also reads the head against the same rows the
[fidelity desks](fidelity-desk-brief.md) read, returning one verdict per row. A
row it finds `missing` is a major. The lane that owns the head runs the pass,
posts its record, and fixes or rebuts each major in the PR; the landing desk reads
the record.

## What the finder receives

Codex reads neither the hooks nor the register, so the prompt carries everything by value:

- The head: repository, PR number, full head sha, base branch, and the command
  that prints its diff.
- Every row `rulings.py slice --pr <n> --head <sha> --base <base> --repo <owner/name> --required`
  prints (every global, pending, and matched row), each with its key, the owner's
  words verbatim, its implications with their `removes` or `permits` mark and
  status, and its names. Paste them as the register holds them, never summarized.
- The PR body, `## Rulings` and `## Regression tests` included, as claims to check.

## What it returns

The reply follows the `findings` schema, so the codex plugin needs nothing new:

- Each row with verdict `missing` is one finding of severity `major`, its `cite` the
  file and line, its `defect` starting `row <key> missing:` and then what the code
  keeps, offers, recommends, budgets, or tests that the words ban.
- `swept` names every other row: `rows met: <key>,<key>...; n/a: <key>,<key>...;
  pending: <key>,<key>...`, then the files it read. `met` means the head does what
  the words say; `n/a` means the head touches nothing the row covers; `pending` lists
  each `pending-owner` row, which gets no verdict of its own. A head that needs a
  pending row in force is read against the rows it would have excepted.
- Defect findings keep the schema's severities. A `blocker` counts as a major and a
  `nit` as a minor.

Every key the slice printed appears exactly once, as `met`, `n/a`, `pending`, or a
`missing` finding. A reply that drops a key is no verdict: run the pass again.

## The record

The lane posts the verdict as the pass returned it, exactly as cci #110240 item 3
fixed it:

```bash
cci post --drive <drive> --lane <lane> --kind review --pr <owner/repo>#<n> --text "finder: ok <head7> <one line>"
cci post --drive <drive> --lane <lane> --kind review --pr <owner/repo>#<n> --text "finder: major <head7> <count> open: <one line per major>"
```

`<count>` is the number of open majors, row `missing` findings included. The one
line per major names its cite and defect, and `; ` separates majors so the record
stays one line. Minors go in the PR body, never in the record.

The newest finder record that names the head decides. Two things answer a major:
a new head, which gets its own pass, or a later
`finder: ok <head7> answered: <how>` that a finder pass posts after it reads the
fix or the rebuttal in the PR. A lane never posts `answered` on its own word.

## What the landing desk does with it

The landing desk refuses a head with no finder record, or whose newest record is
`finder: major`, with `FINDER-PENDING`, and `rulings.py check-pr` reports the open
major as `FINDER-MAJOR <head7>`. An incident fix never waits for the finder: the
pass runs within 30 minutes after it merges (d968e1d, option (a)), and the runner escalates a merged head with no
finder record at 30 minutes as `UNGRADED-30`.

## Prompt

Fill the angle brackets, paste the slice rows whole, and pass the prompt to
`codex-ask --schema findings` through `Skill(codex)`.

```text
You are the finder for <owner/name>#<n> at head <full sha> on base <base>.
Read the head pinned by sha, never the PR's moving tip: `git fetch origin <full sha> <base>`,
then `git diff origin/<base>...<full sha>`. Read the code around every changed symbol at
that sha, its callers and callees included.

Do two things and report both in the findings schema.

1. Find defects in the diff: wrong results, broken callers, lost data, races,
   tests that assert less than they claim. Rate each blocker, major, minor, or nit.

2. Read the head against each owner row below. For each row, ask whether any line
   of the head keeps, offers, recommends, budgets, or tests what the words ban,
   under any of its names, and whether the code does what the words say. Read the
   words and implications, never a summary of them; the PR body's claims are claims
   to check. A deletion is judged on what it deletes: read every caller of the
   removed path. A new refusal kind is not a fix. An edited named test is judged
   against the assertion its row states. Drive-era code is what the drive-era code
   does today, never a requirement.
   For each row give exactly one verdict:
   - missing: one finding, severity major, cite file:line, defect starting
     "row <key> missing:".
   - met or n/a: list it in `swept` as "rows met: <keys>; n/a: <keys>; pending: <keys>".
   - pending: a row marked pending-owner gets no verdict; list it under pending, and
     read a head that needs it in force against the rows it would have excepted.
   Every key below appears exactly once.

PR body:
<the PR body, verbatim>

Rows (`rulings.py slice --required` for this head; slice-hash <sha12>):
<for each row: key, words verbatim, implications with removes/permits and status, names>
```
