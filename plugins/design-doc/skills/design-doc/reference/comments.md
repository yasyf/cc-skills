# Reader comments

Each comment a reader leaves on a published doc is one immutable JSON file at `<slug>/comments/<ulid>.json` in the Forge-AI/design-docs repository. Nothing is ever mutated or deleted.

## The record

```json
{
  "id": "01JBQ7X4M2K8V3",
  "rev": 4,
  "author": { "login": "yasyf" },
  "createdAt": "2026-09-05T18:22:07Z",
  "anchor": { "kind": "entry", "id": "DQ4" },
  "body": "The 50ms ceiling here assumes a warm cache.",
  "parent": null,
  "resolves": null,
  "supersedes": null
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | ULID, lexicographically sortable by time; also the filename stem |
| `rev` | yes | `meta.rev` this comment was written against |
| `author` | yes | `{login}`, the GitHub `login` that wrote it |
| `createdAt` | yes | when it was written |
| `anchor` | yes | where it attaches: `entry` or `quote`, below |
| `body` | yes | the comment text |
| `parent` | no | the comment this replies to; null for a top-level comment |
| `resolves` | no | the comment whose thread this closes; null otherwise |
| `supersedes` | no | the id this record replaces; null for a comment written fresh |

An edit is a later record carrying `supersedes` and a new `body`; a deletion is a later record carrying `supersedes` and an empty `body`. The page folds a chain to its newest body at read time and marks the result edited. A deleted comment with no replies disappears entirely; one with replies still attached to it renders as a tombstone, since dropping it orphans them.

## Anchors

| Kind | Shape | Meaning |
|---|---|---|
| `entry` | `{kind: "entry", id}` | a register entry: `A#`, `DQ#`, `Q#`, `V#`, `c-*`, `n-*`, `p-*` |
| `quote` | `{kind: "quote", scope, entry, prefix, exact, suffix}` | a text-quote selector; `scope` is `summary`, `entry`, or `notes`, naming the body of text the quote lives in (`summary.html`, a register entry's body, or `NOTES.md`); `entry` names which register entry when `scope` is `entry`; `exact` is the quoted text, and `prefix`/`suffix` its 32 characters of context on each side |

A text-quote selector is only meaningful within one body of text. `scope` bounds the search to it, so 32 characters of context pin the quote inside its own body instead of matching identical wording elsewhere on the page.

Entry anchors survive a revision because register ids are stable by contract ([schema.md](schema.md)). Quote anchors do not: rewording the anchored sentence orphans the selector, and an orphaned comment moves to a detached-comments tray rather than vanishing.

`check --strict` runs in CI, where an error blocks the merge that publishes the doc. A malformed comment file (bad JSON, a wrong type, a filename that isn't its own id) can only come from a buggy writer, so that stays an error. A dangling `anchor.id`, `anchor.entry`, `parent`, `resolves`, or `supersedes` is only a warning, and the comment renders in the detached-comments tray instead. A reader's comment must never block the author's publish, and treating a dangling reference to a since-deleted register entry as an error does exactly that.

## Sync

The browser queues a written comment in an `IndexedDB` outbox instead of sending it right away. The queue flushes as a pull request against Forge-AI/design-docs after 20 seconds idle, when the tab hides, and on load, draining anything an earlier session queued but never sent. The pull request auto-merges once its `check` context goes green.

Nothing is ever mutated, so two readers commenting at the same moment write two files with no line in common: no textual conflict, nothing for a human to resolve. That matters because `main` requires a branch to be up to date before it merges, so a real conflict strands a reader's comment behind a rebase nobody is watching for.

A comment that trips CI fails to merge and stays visible as pending instead of disappearing. The browser has no way to see the repo's `FORBIDDEN_TERMS` secret, so a comment using a forbidden term is caught only once its pull request runs, after submit, never before.

## The write token

Reading a comment needs nothing from the reader: the deployed `ai.json` already carries a read-scoped token ([schema.md](schema.md)), and every reader sees comments through it, including one who never connects a token of their own. Writing one is different: it goes out under a second, separate token, the reader's own, never the deployed one.

Once, a reader creates a fine-grained personal access token on Forge-AI/design-docs with Contents: Read and write, and Pull requests: Read and write. An organization may hold the token for admin approval before it starts working, the same wait the read-only token in [publish.md](publish.md) can hit.

The page stores it in `localStorage` under `design-doc-github-write`, sends it nowhere but `api.github.com`, and never writes it into a comment record or a commit message.
