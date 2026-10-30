#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The merge queue's CLI over a drive's landing desk: read why a PR waits, and steer what the queue holds.

    mq.py --config PATH status
    mq.py --config PATH gos
    mq.py --config PATH why PR
    mq.py --config PATH gate PR... [--partial]
    mq.py --config PATH enqueue PR... [--partial]
    mq.py --config PATH checks
    mq.py --config PATH hold PR --reason R
    mq.py --config PATH lift PR
    mq.py --config PATH priority PR N
    mq.py --config PATH requeue PR
    mq.py --config PATH dequeue PR [--reason R]
    mq.py --config PATH recover PR...

STDLIB ONLY. `--config` is the drive's desk-runner `runner.json`; any other file, such as a `runner.<name>.json` state file
beside it, exits 3 naming the `drive`, `orca` or `landing` block it lacks; every verb reads the same ledger,
cci hold records, action store, and repository adapter the landing desk runs on. `status` prints each enqueue
this drive's store believes queued and each open tree in the order the desk gates it. `gos` prints each
open PR a standing root go names that the queue does not hold, with the head the go names, the queue state
and verdict from one batched status read, and any hold, never a GitHub read per PR. `why` prints the
adapter's verdict on the PR's current head, then one `<check> pass|pending|fail <reason>` line per
configured check, posting nothing. `gate` prints each hold or enqueue check that does not pass a head a hand
`stack-enqueue` of the PRs puts in the queue, and exits 1 when it prints any. `enqueue` runs that gate and
enqueues through the desk's adapter only when it prints nothing, then names the head the queue took
for each PR, and posts a note for one that moved while the call ran; it is the hand enqueue a desk runs. `checks` prints the registry in order with the stage each runs at.
`hold` posts a root cci `hold` record leading `hold #<n> <reason>`, and `lift` posts one `lift` record
resolving every open hold record that holds the PR, naming any other PR such a record held, which lifts
with it. `priority` sets the
ledger `priority` the desk orders trees and enqueue waves by, lower first. `dequeue` refuses a PR this
drive's queue does not answer for, naming the drives whose records name it; otherwise it takes the PR
out through the adapter and posts a cci `stopped` record to every drive that names it, and the desk
leaves that head out until `requeue`. `requeue` makes a pulled, ejected, or go-awaiting head
retryable. `recover` runs the adapter's recover over the PRs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from long_running import actions
import cci

SCRIPTS = Path(__file__).resolve().parent
CONFIG_KEYS = ("drive", "orca", "landing")


def desk_runner():
    spec = importlib.util.spec_from_file_location("desk_runner", SCRIPTS / "desk-runner.py")
    module = sys.modules.get("desk_runner") or importlib.util.module_from_spec(spec)
    if "desk_runner" not in sys.modules:
        sys.modules["desk_runner"] = module
        spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mq.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--config", type=Path, required=True)
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("status")
    verbs.add_parser("gos")
    verbs.add_parser("checks")
    for verb in ("why", "lift", "requeue"):
        verbs.add_parser(verb).add_argument("pr")
    hold = verbs.add_parser("hold")
    hold.add_argument("pr")
    hold.add_argument("--reason", required=True)
    priority = verbs.add_parser("priority")
    priority.add_argument("pr")
    priority.add_argument("value", type=int)
    dequeue = verbs.add_parser("dequeue")
    dequeue.add_argument("pr")
    dequeue.add_argument("--reason", default="dequeued by hand with mq dequeue")
    recover = verbs.add_parser("recover")
    recover.add_argument("prs", nargs="+")
    for verb in ("gate", "enqueue"):
        gated = verbs.add_parser(verb)
        gated.add_argument("prs", nargs="+")
        gated.add_argument("--partial", action="store_true")
    return parser


def main(argv: list[str] | None = None, shell=None) -> int:
    args = build_parser().parse_args(argv)
    if missing := [key for key in CONFIG_KEYS if key not in json.loads(args.config.read_text())]:
        print(f"mq.py: {args.config} is no desk-runner config, since it has no {' or '.join(missing)} block; pass the drive's runner.json", file=sys.stderr)
        return 3
    runner_module = desk_runner()
    config = runner_module.Config.load(args.config)
    if fault := runner_module.mq_config_fault(config.landing):
        print(f"mq.py: {args.config} {fault}", file=sys.stderr)
        return 3
    runner = runner_module.Runner(shell or runner_module.Shell(), config, actions.Store(config.store))
    landing = runner_module.Landing(runner, config.landing)
    if args.verb in ("hold", "lift"):
        return hold_or_lift(runner_module, runner, landing, args)
    if args.verb == "checks":
        print("\n".join(f"{name} {runner_module.CHECK_STAGES[name]}" for name in landing.checks))
        return 0
    if args.verb == "status":
        print("\n".join(landing.status_lines()))
        return 0
    if args.verb == "gos":
        print("\n".join(landing.standing_go_lines()))
        return 0
    if args.verb == "why":
        print("\n".join(landing.why(args.pr)))
        return 0
    if args.verb == "gate":
        if lines := landing.hand_enqueue_gate(args.prs, args.partial)[1]:
            print("\n".join(lines))
        return 1 if lines else 0
    if args.verb == "enqueue":
        queued, lines = landing.hand_enqueue(args.prs, args.partial)
        runner.flush()
        print("\n".join(lines))
        return 0 if queued else 1
    if args.verb == "priority":
        done = landing.ledger_py("priority", "--ledger", landing.ledger, "--repo", landing.repo, "--pr", args.pr, "--priority", str(args.value))
        print((done.out or done.err).strip())
        return done.code
    if args.verb == "requeue":
        requeued, text = landing.requeue_pr(args.pr)
        runner.flush()
        print(text, file=sys.stdout if requeued else sys.stderr)
        return 0 if requeued else 1
    if args.verb == "dequeue":
        released, text = landing.dequeue_pr(args.pr, args.reason)
        print(text, file=sys.stdout if released else sys.stderr)
        return 0 if released else 1
    code, text = landing.adapter.recover(args.prs)
    print(text)
    return code


def hold_or_lift(runner_module, runner, landing, args: argparse.Namespace) -> int:
    pr = args.pr.lstrip("#")
    post = ["cci", "post", "--drive", runner.config.drive, "--lane", runner_module.ROOT, "--to", "all", "--pr", f"{landing.repo}#{pr}"]
    if args.verb == "hold":
        done = runner.shell.run([*post, "--kind", "hold", "--text", f"hold #{pr} {args.reason}"])
        print((done.out or done.err).strip(), file=sys.stderr if done.code else sys.stdout)
        return done.code
    holding = [record for record in cci.open_holds(runner.cci_tail, runner.config.drive) if pr in cci.held_by(record)]
    if not holding:
        print(f"no open cci hold record holds #{pr}")
        return 0
    others = sorted({other for record in holding for other in cci.held_by(record)} - {pr}, key=int)
    done = runner.shell.run([*post, "--kind", "lift", "--resolves", ",".join(str(record["seq"]) for record in holding), "--text", f"lift #{pr}"])
    print((done.out or done.err).strip() + (f"; the same records held {', '.join(f'#{other}' for other in others)}, which lift too" if others and not done.code else ""), file=sys.stderr if done.code else sys.stdout)
    return done.code


if __name__ == "__main__":
    import drive

    drive.run_installed(__file__)
    sys.exit(main())
