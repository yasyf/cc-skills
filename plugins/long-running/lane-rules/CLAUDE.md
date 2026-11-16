# Lane rules

These are the rules from the owner's global instructions that a drive lane needs. A drive session below the top roots loads this file in place of `~/.claude/CLAUDE.md`. Your role's agent definition and brief come first; this file fills in what they leave out.

- **Judgment:** you own your deliverable. Decide the forks you meet, act, and name what you chose in your return; his later word steers it. Return to ask only for a limit below or a question that is truly his.
- **Tests:** never run tests locally. CI grades the work: push, read the CI log, fix and push again. You may run a build, a typecheck and a linter on what you touched.
- **Shipping:** ship through `ccx vcs ship --no-watch`, or the monorepo's `submit-pr`. The drive's watch follows each PR's CI from GitHub's webhooks, so a ship that waits on CI only holds the lane: a lane's environment sets `CCX_SHIP_NO_WATCH=1`, and the flag covers a ccx older than 0.96.25. A PR gets a written body and is never a draft. When ccx refuses, run the raw command marked `# ccx:raw` and report the refusal verbatim.
- **Paths:** new worktrees go under `~/repos/claude/worktrees/<repo>/<lane>`. Disposable scratch goes under `~/.vcache/scratch/<lane>`. Never use `/tmp`, `~/.cache`, or a new folder in `~`.
- **Python:** no background, bulk or dataset Python. Sample with zsh, and scan transcripts with `cc-transcript`.
- **Comments:** zero comments in code. The exceptions are public-API doc comments, TODOs, and workarounds the code can't express.
- **FDE (forward-deployed engineer) impact:** before changing anything FDEs' or customers' code can observe (a builtin, an API or SDK call, a consistency level, a default, a schema, a wire or storage format), run the monorepo's `fde-scan` skill, or `gh search code '<symbol>' --owner Forge-FDE --extension sand`. Put what it finds in the PR body's `## FDE impact` section.
- **Dangerous changes:** a dangerous change merges or releases only while the human is present, meaning his word within the last 15 minutes. Dangerous means each class the repository names: a migration, a backfill, a delete of user data, and the storage, runtime and API paths its `.claude/long-running.toml` rules list. Its root `AGENTS.md` keeps the full list.
- **Shell:** Bash runs zsh: write POSIX/zsh, never fish. No `sleep`, and no `until` or `while` loop polling a file, a process, `gh` or `orca orchestration check`, at any interval. Wait with a tool's own watch run in the background, such as `drive wait <pr or run>` for GitHub, and end your turn until it reports.
- **Transcripts:** read Claude session history only with `cc-transcript` (`list --project <substring>`, `grep`, `show`). Never use a recursive `grep`, `rg` or `find` over `~/.claude`.
- **Sessions:** never stop, signal or close another Claude or Codex session, Orca, or a terminal host.
- **Long calls:** messages reach you only between tool calls, so a foreground call past about 30 seconds leaves you deaf for its length. Run anything longer in the background and end your turn to wait.
- **Records:** post a record only when one other lane must act on it now. Progress and state are not posted; the dashboard shows them.
- **His decisions:** a decision only he can make is asked within a minute, as one card with 2 to 4 options and a recommendation. His ruling is applied as worded, never narrowed.
