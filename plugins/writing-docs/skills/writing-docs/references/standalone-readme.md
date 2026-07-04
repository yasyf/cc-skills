# Standalone READMEs: the inline tail when the README is the only doc

The skeleton in `readme.md` ends in two branches, and this page owns the second. When no docs site exists, the feature previews and link-out ending (skeleton sections 8-9) are replaced by inline how-to and reference content, and the rules for that tail live here. The front half — banner through use cases — is identical in both branches and `readme.md` governs it; nothing here overrides it. There is no hard length ceiling on the inline branch. `<details>` blocks and the rhythm rule from `readme.md` govern length, not a line count.

## The offload target is `--help`, not a docs site

A standalone README still offloads its long tail. The target is the tool itself. `--help`, `man`, a catalogue subcommand such as `rules` or `help <cmd>`, and the source alongside an `AGENTS.md` hold the exhaustive detail. "Never duplicate the docs site" becomes "never duplicate `--help`". Carry the high-value subset in-file and point the binary at itself for the rest. When the binary emits its own catalogue, point at that instead of transcribing it, since the prose copy drifts the moment the binary changes.

## When a standalone README is the right call

Go standalone when the surface is bounded and the binary documents itself:

- A single-binary CLI, a small library, or a plugin with a handful of commands a reader actually runs.
- One or two audiences reachable in one file, not separate learning tracks.
- An exhaustive reference that lives in the tool, so the README carries a curated table plus one example and defers the tail.
- No hosted docs site, with none planned. Standalone is the steady state, not a transitional gap.
- A whole that fits in one file without flag-dumping.

Reach for a front-door README plus a separate docs site when the surface or audience outgrows one file: a large or generated reference that would bloat or drift the README, multiple audiences each needing their own Diataxis track, reference generated from docstrings that must stay in sync mechanically, or long-form conceptual and migration material that swamps orientation.

Two valid standalone models exist; pick one and commit. A front door to `--help` orients, demos, installs, then defers the exhaustive reference to `--help` and man, the way ripgrep, fd, bat, zoxide, and hyperfine do. A whole manual puts everything in one file, fronted by a table of contents that *is* the published reference, the way just does. The anti-pattern is the in-between, a thin README that mostly redirects to a half-built docs site and leaves two sources of truth to drift apart.

## The inline tail

What replaces the feature previews and the link-out ending:

- Carry the reference tables you cannot relocate. A curated command table and a config or env-var table are legitimate here, since no reference page exists to hold them. Keep them at reference altitude, one row per signature plus a one-line description, no narrative. The bloat is a prose paragraph re-describing the same flags, not the table itself.
- Show one canonical invocation per surface and defer every flag to `--help`. Never enumerate every `--flag` in prose. The defining smell is a paragraph listing `--this`, `--that`, and `--budget N`, then deferring to `--help` in the same breath. The offload target is right there, so cut the enumeration and keep the deferral.
- State each command surface exactly once. A standalone README carries every Diataxis mode in one file, so "one mode per page" is unreachable. The substitute is a headed section per mode, each at its own altitude, naming each command once. The dominant bloat is triplication: the same commands in the use cases, again in the walkthrough, and a third time in the command table. Pick one home per fact. The table is the reference, get started shows one path, and a use case motivates without re-listing. This is ARID with no "link instead" escape hatch, so the discipline is harder, not softer.
- Keep any inline walkthrough one single-path lifecycle with real, trimmed output. The walkthrough drives the binary Get started installed — never a from-clone `./bin/` path — and acts on the repo's committed example document where one exists, fetched in one line rather than retyped inline. One branchless flow of at most ten steps, ending in a single shown outcome, not a feature tour of independent demos under sub-headings. Output stays real and current but trimmed. Collapse a minified JSON blob with a brittle float to its load-bearing fields, or bound it with the tool's own flag, instead of pasting a wall.
- Hold architecture to one paragraph, stated once. The absence of an architecture page does not license a long internals tour. Name the approach, attribute prior art, link the deep home — a contract or reference doc such as `docs/contract.md`, AGENTS.md for build mechanics — and stop. The paragraph never carries compile or embed directives, event-type inventories, notes on endpoints that don't exist, or agent-loop control flow. An internal-class walkthrough belongs in an explanation doc the standalone repo does not have, so leave it out.
- Cut verbosity, never facts. Verify completeness against the prior version so no genuine fact is silently dropped. A load-bearing fact or gotcha survives a hard trim, whether a dependency requirement, a billing or rate-limit consequence, or a safety cap such as `--apply` or a file-count limit. A verbose multi-clause restatement goes.

The absence of a docs site does not lower the bar. A standalone README still forbids exhaustive flag tables, internal-class architecture tours, CI and release mechanics, cross-project lineage such as "extracted from X, now powering Y and Z", inline lists of IDs the tool can emit itself, build mechanics and compile or embed directives, notes on endpoints that do not exist, agent-loop exit conditions, and slop prose. Describe the tool on its own terms for an outside reader. Rewrite "each consumer" to "your code", and drop sibling-project name-drops.

## Where the minimum-viable docs set lands

The minimum set is still README, LICENSE, CONTRIBUTING, and CHANGELOG. In a standalone repo, CONTRIBUTING usually folds into a short Development section covering build, test, lint, and a pointer to `AGENTS.md`. The CHANGELOG keeps its own file in Keep a Changelog format, since a README "Changes" section drifts and has nowhere to grow.

## Exemplars

- [ripgrep](https://github.com/BurntSushi/ripgrep) gives a one-sentence definition, real benchmark tables, and a paired "Why should I use ripgrep?" / "Why shouldn't I?", with the flag list deferred to `--help`, the man page, and an in-repo `GUIDE.md`.
- [fd](https://github.com/sharkdp/fd) is demo-first: a screencast, then bite-sized command-and-output subsections, redirecting to `fd --help` instead of duplicating every flag.
- [bat](https://github.com/sharkdp/bat) leads with screenshots of the rendered output so the value is visible in one screenful, then a platform-by-platform install matrix.
- [zoxide](https://github.com/ajeetdsouza/zoxide) nails the elevator pitch, "a smarter cd command, inspired by z and autojump", then gives first-class per-shell integration so install is self-contained.
- [just](https://github.com/casey/just) is the whole-manual model: the README *is* the complete manual, fronted by a table of contents and rendered verbatim as the published book, so no second source can drift.
- [hyperfine](https://github.com/sharkdp/hyperfine) opens with a five-word definition and a GIF, keeps a tight example-driven Usage section, and shows the actual exported artifacts, a results table, histogram, and whisker plot.

See `readme.md` for the front half and for the docs-site branch this page is the counterpart to.
