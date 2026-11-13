# orca

Launch, supervise, and reclaim Orca workers.

- `orca-worker-launch.sh` prepares a Sprite with cc-remote and attaches a native Orca Task to it, or, with `WORKER_LOCAL=1`, starts Claude in a local Orca worktree.
- `orca-sprite-tools.sh` installs the latest released `ccx` and `cc-notes` on a prepared Sprite, and the Rust `wasm32-unknown-unknown` target and zig 0.14.0 that cc-context's go:embed WASM build needs; the launcher runs it before it attaches the worker.
- `orca-reclaim.py` ends a done lane and reclaims the terminals, worktrees, and Sprites settled Dispatches left behind; `--held` names what a lane's Sprite holds that no remote does, changing nothing, and `--save` commits and pushes it to `saved/<lane>/<branch>` on a token lent to that one push, and `--destroy` runs that save, then destroys a lane's Sprite and its Orca environment once it holds nothing `--held` would name.
  `orca-reclaim.py --new --shells` is its recurring pass. Each pass handles a bounded batch, caps its closes and removals, and backs off while Orca is slow.
- The `orca-doctor` agent diagnoses one stuck lane without changing anything.
- The `orca` skill is the contract for all of them.

A repository checks in `.orca.json` at its root to name its cc-remote CLI and config, its Orca repository id, its base branch, and its sparse cone; the skill lists the keys. Environment variables override each key.

Tests run with `python3 -m unittest discover -s plugins/orca/tests`.
