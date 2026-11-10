# orca

Launch, supervise, and reclaim Orca workers.

- `orca-worker-launch.sh` prepares a Sprite with cc-remote and attaches a native Orca Task to it, or, with `WORKER_LOCAL=1`, starts Claude in a local Orca worktree.
- `orca-reclaim.py` ends a done lane and reclaims the terminals, worktrees, and Sprites settled Dispatches left behind.
  `orca-reclaim.py --new --shells` is its recurring pass. Each pass handles a bounded batch, caps its closes and removals, and backs off while Orca is slow.
- The `orca-doctor` agent diagnoses one stuck lane without changing anything.
- The `orca` skill is the contract for all three.

A repository checks in `.orca.json` at its root to name its cc-remote CLI and config, its Orca repository id, its base branch, and its sparse cone; the skill lists the keys. Environment variables override each key.

Tests run with `python3 -m unittest discover -s plugins/orca/tests`.
