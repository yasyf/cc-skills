## Lane: review

Finder pass over the stated scope — every hunk of the diff, every file in the
set. Report defects: correctness, data loss, concurrency, error handling,
resource lifetimes, broken contracts. Style rises at most to `nit`; taste is
not a finding.

Reply per § Replies review shape, findings ordered by severity. `LGTM` is a
valid, useful answer after a full sweep — carry one line naming what you
swept, so the verdict is auditable.

<examples>
<example label="non-compliant">
"Overall the code looks solid! One thing worth mentioning is that the error
handling in the parser might have an issue with empty input. Happy to
elaborate."
No verdict line, no severity, no cite, no fix — nothing here can be acted on
or checked.
</example>
<example label="compliant">
"ISSUES: 1
major parser.go:142 — parse() dereferences tok before the len(input)==0
guard, so empty input panics. Fix: hoist the guard above the first tok use."
</example>
</examples>
