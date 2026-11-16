Decide whether this pull request plainly needs a staging pass before it merges.

His words at 14:55 on 2026-11-16: "almost all non-SandSQL PRs can be tested on staging after merging, as a big batch". Only a change that plainly does one of these needs the pass first:

1. A migration of stored data: a migration file or step that changes a database schema or data already stored.
2. A backfill: a job that rewrites or fills in rows that already exist.
3. A delete of user data: code that removes a customer's or user's stored data, or widens what an existing delete removes.
4. SandSQL storage, consistency or handoff: how SandSQL stores, replicates, journals, compacts or reads back data, the consistency it gives a reader, or the handoff of a database between writers or nodes.
5. The run journal schema: the shape of the run journal's entries or tables.
6. A wire or storage format other processes or older versions read: a protobuf or RPC message, a blob or file layout, or a serialized record.

Everything else merges on his Approve and is tested on staging after the merge, in one batch. That includes features, UI, API handlers, infrastructure, CI and tooling, refactors, tests, and changes inside SandSQL that touch none of item 4.

Answer yes only when the title or the changed files show the change plainly does one of the six. When it is unclear, answer no: it is tested after the merge.
