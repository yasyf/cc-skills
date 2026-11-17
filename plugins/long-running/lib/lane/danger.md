Decide whether this pull request plainly needs a staging pass before it merges.

His words at 17:00 on 2026-11-16: "Im talking about the fucking ssql work nothing other than the ssql work needs to run before merging it can test on staging after merging". Only a change that plainly changes SandSQL needs the pass first: how SandSQL stores, replicates, journals, compacts or reads back data, the consistency it gives a reader, its migrations or the shape of what it stores, or the handoff of a database between writers or nodes.

Everything else merges on his Approve and is tested on staging after the merge, in one batch. That includes Postgres migrations, backfills, deletes, the run journal, wire formats, features, UI, API handlers, infrastructure, CI and tooling, refactors, tests, and changes that only call SandSQL from outside it.

Answer yes only when the title or the changed files show the change plainly changes SandSQL. When it is unclear, answer no: it is tested after the merge.
