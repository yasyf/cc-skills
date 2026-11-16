# Lane rules

These are the rules from the owner's global instructions that a drive lane needs. A drive session below the top roots loads this file in place of `~/.claude/CLAUDE.md`. Your role's agent definition and brief come first; this file fills in what they leave out.

- **Tests:** never run tests locally. CI grades the work: push, read the CI log, fix and push again. You may run a build, a typecheck and a linter on what you touched.
- **Shipping:** ship through `ccx vcs ship`, or the monorepo's `submit-pr`. A PR gets a written body and is never a draft. When ccx refuses, run the raw command marked `# ccx:raw` and report the refusal verbatim.
- **Paths:** new worktrees go under `~/repos/claude/worktrees/<repo>/<lane>`. Disposable scratch goes under `~/.vcache/scratch/<lane>`. Never use `/tmp`, `~/.cache`, or a new folder in `~`.
- **Python:** no background, bulk or dataset Python. Sample with zsh, and scan transcripts with `cc-transcript`.
- **Comments:** zero comments in code. The exceptions are public-API doc comments, TODOs, and workarounds the code can't express.
- **FDE (forward-deployed engineer) impact:** before changing anything FDEs' or customers' code can observe (a builtin, an API or SDK call, a consistency level, a default, a schema, a wire or storage format), run the monorepo's `fde-scan` skill, or `gh search code '<symbol>' --owner Forge-FDE --extension sand`. Put what it finds in the PR body's `## FDE impact` section.
- **Dangerous changes:** a dangerous change merges or releases only while the human is present, meaning his word within the last 15 minutes. Dangerous means: a migration, a backfill, a delete of user data, SandSQL consistency, storage or write paths, a builtin, the parser, checker or VM, `runtime-api` or `common/src/iris/appShell`. The monorepo's root `AGENTS.md` keeps the full list.
- **Shell:** Bash runs zsh: write POSIX/zsh, never fish. No `sleep` loops or polls in the shell; wait on a blocking command or a tool's own watch.
- **Transcripts:** read Claude session history only with `cc-transcript` (`list --project <substring>`, `grep`, `show`). Never use a recursive `grep`, `rg` or `find` over `~/.claude`.
- **Sessions:** never stop, signal or close another Claude or Codex session, Orca, or a terminal host.
