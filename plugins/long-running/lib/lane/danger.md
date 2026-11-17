Decide whether this pull request plainly needs a staging pass before it merges.

This is the default bar for a repository whose long-running settings name no danger page of their own. Only a change that plainly changes how the repository's own database stores, replicates, journals, compacts or reads back data, the consistency it gives a reader, its migrations or the shape of what it stores, needs the pass first.

Everything else merges on his Approve and is tested on staging after the merge, in one batch. That includes features, UI, API handlers, infrastructure, CI and tooling, refactors, tests, and changes that only call a database from outside it.

Answer yes only when the title or the changed files show the change plainly changes that storage. When it is unclear, answer no: it is tested after the merge.
