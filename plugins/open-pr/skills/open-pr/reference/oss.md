# Someone else's repo

## The ownership test

The repo is the user's when any row holds:

| Signal | Test |
|---|---|
| Private | `isPrivate: true` |
| Elevated permission | `viewerPermission` is `ADMIN` or `MAINTAIN` |
| Own namespace | owner login is the viewer's login or one of their orgs (`gh api user/orgs --jq '.[].login'`) |

`ccx vcs lane --json` already computes this as `github.mine`; recompute only when lane output is unavailable. Anything else is someone else's repo: the passes below apply, and ship will have silently left the gt lane — stacked-PR mechanics don't reach a repo you can't push branches to.

## The politeness register

Direct about the change, deferential about the decision. The diff's facts are yours to state; the repo's direction is the maintainer's to call.

- State what changed and its measured effect; numbers carry the claims.
- Offer outs on approach: "happy to split this in two, or drop the rename if the old name is load-bearing."
- Ask about what only maintainers know — backport policy, whether a changelog entry is wanted — instead of guessing and asserting.
- Answer review with the fix, and thank for the specific catch rather than effusively for the attention.
- Timing is theirs: no nudges, no "hoping for a quick review."

<examples>
<example label="entitled">
"This fixes an obvious bug — please merge soon, we need it for our release."
Claims the maintainer's judgment and their calendar in one sentence.
</example>
<example label="guest">
"This fixes the off-by-one in `parse_range` (repro in #812). Happy to add a regression test to `test_parse.py` if that's where you'd want it."
The bug is stated as fact; the repo decisions stay with the maintainer.
</example>
</examples>

## CLA and DCO

- **DCO** — a repo with a DCO check wants `Signed-off-by:` on every commit. Add it when required: it's the one trailer the match-the-history rule doesn't govern, because a requirement beats a convention. It certifies the user's right to submit the change, which shipping the change they asked for already implies.
- **CLA** — signing happens out-of-band: a bot comments on the first PR with a signing link. The agreement is a legal document under the user's identity, so surface the link and what it covers through `AskUserQuestion` and let the user sign — the pr-watcher treats a CLA bot comment as a judgment call and routes it back for exactly this reason.

## The conformance pass

Before writing the body, walk `ccx vcs diff` against this list. Each miss is a fix before shipping, or a sentence in the body owning the deviation — silence is the one wrong disposition.

- **Scope** — one concern; no drive-by refactors, reformatting, or import shuffles beyond the lines the change needs.
- **Style** — naming, error handling, and test idioms match the files the diff touches; the style card's conventions hold in the new code too.
- **Tests** — the change carries the coverage its sibling features have; find the pattern by locating the tests for the nearest peer.
- **Docs and changelog** — update the artifacts the repo's own merged PRs update: a changelog entry, a docs page, a regenerated reference.
- **Generated files and lockfiles** — churn only when the change requires it, and say so when it does.
- **Commit hygiene** — subjects per the style card, no fixup or wip commits, trailers per the repo's history plus DCO where required.
- **Size** — inside the repo's merged-PR norm from the style card; an oversized diff gets split, or at minimum a split offer in the body.

## Issue-first

When the guidelines card marks an issue as a hard requirement, search before opening:

```bash
gh issue list --repo <owner/name> --search "<keywords>" --state all
```

A match — a closed one included — is linkable; link it in the repo's own grammar (`Fixes #N`, `Closes #N`, or the template's field). No match: draft the issue title and body, then put the draft in front of the user with `AskUserQuestion` — open as drafted, edit it first, link a different existing issue, or skip the requirement deliberately — and act on their pick. Opening an issue publishes under the user's name on someone else's project; drafting doesn't.
