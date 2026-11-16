Decide whether this pull request needs a staging pass before it merges.

He ruled at 14:33 on 2026-11-16 that only these need one:

1. A migration: a change to a database schema or to data already stored, run as a migration file or a migration step.
2. A backfill: a job or script that rewrites or fills in rows that already exist.
3. A delete of user data: code that removes a customer's or user's stored data, or widens what an existing delete removes.
4. SandSQL storage or consistency: a change to how SandSQL stores, replicates, journals, compacts or reads back data, or to the consistency level or guarantees it gives a reader.
5. A wire or storage format: a change to bytes another process or an older version reads, such as a protobuf or RPC message, a file or blob layout, a serialized record, or a journal entry.

Everything else merges on his Approve and gets its staging test after the merge, run on the merged head. That includes new features, UI, API handlers that keep their wire shape, infrastructure, CI and tooling, and refactors.

Judge by what the change does, read from its title and the files it changes. A file's directory alone decides nothing. A change under a SandSQL directory that only touches tests, logging or an HTTP handler is not item 4. When the files leave it open whether the change alters a stored or wire format, treat it as needing the pass.
