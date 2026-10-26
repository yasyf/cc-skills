#!/usr/bin/env python3
"""The merge queue's CLI over a drive's landing desk: read why a PR waits, and steer what the queue holds.

    mq.py --config PATH status
    mq.py --config PATH why PR
    mq.py --config PATH checks
    mq.py --config PATH hold PR --reason R
    mq.py --config PATH lift PR
    mq.py --config PATH priority PR N
    mq.py --config PATH requeue PR
    mq.py --config PATH dequeue PR [--reason R]
    mq.py --config PATH recover PR...

STDLIB ONLY. `--config` is the drive's desk-runner `runner.json`; every verb reads the same ledger,
holds file, action store, and repository adapter the landing desk runs on. `status` prints each enqueue
this drive's store believes queued and each open tree in the order the desk gates it. `why` prints the
adapter's verdict on the PR's current head, then one `<check> pass|pending|fail <reason>` line per
configured check, posting nothing. `checks` prints the registry in order with the stage each runs at.
`hold` and `lift` write and remove the PR's holds-file line through holds.py. `priority` sets the
ledger `priority` the desk orders trees and enqueue waves by, lower first. `dequeue` refuses a PR this
drive's queue does not answer for, naming the drives whose records name it; otherwise it takes the PR
out through the adapter and posts a cci `stopped` record to every drive that names it, and the desk
leaves that head out until `requeue`. `requeue` puts a flaky head back in at once and makes a pulled,
ejected, or go-awaiting head retryable. `recover` runs the adapter's recover over the PRs.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import actions
import holds

SCRIPTS = Path(__file__).resolve().parent


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
    return parser


def main(argv: list[str] | None = None, shell=None) -> int:
    args = build_parser().parse_args(argv)
    if args.verb in ("hold", "lift"):
        edit = ["add", "--pr", args.pr, "--reason", args.reason] if args.verb == "hold" else ["rm", "--pr", args.pr]
        return holds.main(["--config", str(args.config), *edit])
    runner_module = desk_runner()
    config = runner_module.Config.load(args.config)
    if fault := runner_module.mq_config_fault(config.landing):
        print(f"mq.py: {args.config} {fault}", file=sys.stderr)
        return 3
    runner = runner_module.Runner(shell or runner_module.Shell(), config, actions.Store(config.store))
    landing = runner_module.Landing(runner, config.landing)
    if args.verb == "checks":
        print("\n".join(f"{name} {runner_module.CHECK_STAGES[name]}" for name in landing.checks))
        return 0
    if args.verb == "status":
        print("\n".join(landing.status_lines()))
        return 0
    if args.verb == "why":
        print("\n".join(landing.why(args.pr)))
        return 0
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


if __name__ == "__main__":
    import drive

    drive.refuse_stale(__file__)
    sys.exit(main())
