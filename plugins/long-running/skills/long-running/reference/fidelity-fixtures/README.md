# Fidelity fixtures

Eight heads the sweep desk grades once after its launch, each with the grade it must
reach and the register row that grade reads against. Six are real heads of the
drive's private repository. This public repository holds only a pointer to each:
the desk fetches the diff at grading time with the command in its row. Two are
synthetic, hand-written diffs with the shape of a change that never shipped.

Grade each fixture in head mode as [fidelity-desk-brief.md](../fidelity-desk-brief.md)
says: the fetched or committed diff is the head and the PR body is empty. A fixture
has no PR body to check, so its grade reads the diff alone. A `missing` counts only
when it names the fixture's row and the code the row bans, never the absent body
sections. Write the fixture's name where a grade line names `<head7>`, and record
each grade beside its expected one with `ccn note add`. What each grade must find is in the drive's
own cc-notes, note `b65fcb5`, beside the owner's words of each row. A shape-only
pass is the failure every fixture catches: each diff reads as harmless until the
desk reads what the code does.

| Fixture | Source | Expected grade | Row it reads against |
|---|---|---|---|
| `3j-read-only-deletion` | One-clause deletion at `go/sand/cmd/iris_local_migrator.go:649` on `b3eb96249fc`; the exact edit is in note `b65fcb5`: `git -C <checkout> show b3eb96249fc:go/sand/cmd/iris_local_migrator.go` | `missing` | `rule/784f1ab`; plan 1.7 |
| `41210-open-elsewhere-suppressed` | PR 41210 of Forge-AI/monorepo at its head: `git -C <checkout> fetch origin 17f1c046db576d9a29bb792572ccd0c96b9ca871 && git -C <checkout> diff origin/dev...17f1c046db576d9a29bb792572ccd0c96b9ca871` | `missing` | `prompt/1020-133122` |
| `40265-hooks-tests-in-monorepo-ci` | PR 40265 of Forge-AI/monorepo at its head: `git -C <checkout> fetch origin 73e661d002b2e8ffbe63461e99a76017bdf3e82c && git -C <checkout> diff origin/dev...73e661d002b2e8ffbe63461e99a76017bdf3e82c` (C230) | `missing` | `rule/d968e1d`; standing rule 12 |
| `landing-gates-before-41522` | `git -C <checkout> diff 4b825dc642cb6eb9a060e54bf8d69288fbee4904 fdcaf449440^ -- go/ci/internal/landinggates go/ci/landinggates.go go/ci/landinggates_test.go` (C230) | `missing` | `rule/d968e1d` |
| `46612-new-input-to-old-api` | PR 46612 of Forge-AI/monorepo as merged: `git -C <checkout> fetch origin a88b7a6a1f3caf1023e70dcc91ae5534517f4242 && git -C <checkout> diff a88b7a6a1f3caf1023e70dcc91ae5534517f4242^ a88b7a6a1f3caf1023e70dcc91ae5534517f4242`; the Mac app sends `first` and `appChatsOnly` at `go/sand/internal/platformapi/client_iris_sessions.go:71` to every api, released or not (retro C2) | `missing` | `rule/7ab71e2` |
| `46615-api-refuses-list-all` | PR 46615 of Forge-AI/monorepo as merged: `git -C <checkout> fetch origin 5b2f97e067b20721112e0630f080aa8c15ed3df6 && git -C <checkout> diff 5b2f97e067b20721112e0630f080aa8c15ed3df6^ 5b2f97e067b20721112e0630f080aa8c15ed3df6`; the api refuses at `api/src/entities/IrisSession.ts:429` the list-all call older Mac builds make (retro C2) | `missing` | `rule/7ab71e2` |
| [`c00-weakened-hypothetical.diff`](c00-weakened-hypothetical.diff) | Synthetic: drops the assertion `plan/B-tests.md` row C00 puts in `TestEveryCorpusShapeOpensAndANewChatStartsBesideIt` (C231) | `missing` | `corpus/b-tests`; `rule/35e3757` |
| [`syncing-label-hypothetical.diff`](syncing-label-hypothetical.diff) | Synthetic: a `syncing` chat state with a "Syncing…" label (C232) | `missing` | `rule/f958522` |

C230's other half, a release-tooling head on the release owner's word that grades
ok, has no fixture: f872565 named no release owner, so no head carries that word yet.
