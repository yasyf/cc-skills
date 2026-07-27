# The guidelines card

`ccx vcs guidelines` gathers the repo's stated contribution rules into one card, cached per repo, and prints the path of the card it wrote. The card is the source — read it, not the command's narration. Its sections mirror what the repo publishes:

- **CONTRIBUTING** — root, `.github/`, or `docs/`; the prose rules: how changes are proposed, test expectations, commit conventions, sign-off requirements.
- **PR templates** — `PULL_REQUEST_TEMPLATE.md` or a `PULL_REQUEST_TEMPLATE/` directory of variants; the body's skeleton. Multiple variants → pick by change type and say which one you filled.
- **Code of conduct** — rarely changes what you write, always bounds how you respond to review.
- **Issue config** — issue templates plus `config.yml`; `blank_issues_enabled: false` and routed discussion links signal how much process the repo wants before code arrives.

## Hard requirement or preference

The card reproduces prose; classifying it is your job. Three signals make a rule hard:

1. **Mechanical enforcement.** A bot or required check backs the rule — a DCO check, a CLA assistant, a template checkbox CI parses. An enforced rule fails the PR whether or not any human weighs in.
2. **A stated consequence.** "PRs without a linked issue will be closed" is hard; "please link an issue where relevant" is not.
3. **Modal force attached to a specific artifact.** "Must" or "required" pointing at a concrete thing — an issue, a signed commit, a changelog entry — not at a mood ("PRs must be high quality").

Everything else is a preference: follow it where the diff allows, and when the diff's reality wins, say so in the body — "kept this as one commit; the two halves don't build separately."

An issue-first rule classified as hard triggers the issue flow in SKILL.md. Classified as a preference, a clear motivation section in the body covers it.

## When the card is thin

Some repos publish nothing. Probe before concluding no rules exist:

```bash
gh api repos/<owner>/<name>/community/profile    # which health files exist, with URLs
gh repo view --json pullRequestTemplates         # template bodies without a checkout
gh api repos/<owner>/<name>/contents/.github/ISSUE_TEMPLATE
```

`community/profile` is the cheap first probe: one call reports which of CONTRIBUTING, code of conduct, and templates exist, with the path of each. Fetch what it names raw rather than guessing locations:

```bash
gh api "repos/<owner>/<name>/contents/<path>" -H "Accept: application/vnd.github.raw"
```

A repo with none of these still has conventions — the style card carries them, and the merged history is the one guideline every repo enforces on itself.
