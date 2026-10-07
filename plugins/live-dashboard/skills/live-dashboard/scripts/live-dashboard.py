#!/usr/bin/env python3
"""A live dashboard assembled from typed components, served from one directory.

    live-dashboard start    --dir D [--host HOST]
    live-dashboard serve    --dir D [--host HOST] [--port N]
    live-dashboard stop     --dir D
    live-dashboard url      --dir D
    live-dashboard open     --dir D
    live-dashboard status   --dir D [--json | --changes]
    live-dashboard card     --dir D ID [--json]
    live-dashboard check    --dir D [--only ID] [--static]
    live-dashboard init     --dir D --preset NAME [--title TEXT]
    live-dashboard new      NAME --dir D --payload KIND
    live-dashboard snapshot --dir D [--card ID] [--md]
    live-dashboard catalog  [--check]

STDLIB ONLY. D holds context.json (the facts components bind from), layout.yaml (the cards),
components/ (this dashboard's own components), cache/ and server.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from livedash import check, layout, payloads, registry, server
from livedash.scheduler import InlineExecutor, Scheduler

SCRIPT = str(Path(__file__).resolve())
SKILL = Path(__file__).resolve().parents[1]
PRESETS = SKILL / "presets"
CATALOG = SKILL / "reference" / "catalog.md"
COMPONENT_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
CHANGES_SECONDS = 10.0
SCAFFOLD = '''from livedash import Context, {payload}, component


@component("{name}", "{title}", question="TODO: which question does this card answer for the owner?", reads=["TODO: each source it reads"], every="5m")
def {function}(ctx: Context) -> {payload}:
    return {payload}.example()
'''
APPENDED = """  - title: {title}
    components:
      - {{use: local.{name}}}
"""


def directory_of(args: argparse.Namespace) -> Path:
    return Path(args.dir).expanduser().resolve()


def cmd_serve(args: argparse.Namespace) -> int:
    return server.serve(directory_of(args), args.host, args.port, SCRIPT)


def cmd_start(args: argparse.Namespace) -> int:
    print(server.start(directory_of(args), args.host, SCRIPT))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    if not server.stop(directory_of(args)):
        print(f"no dashboard is serving {directory_of(args)}", file=sys.stderr)
        return 1
    return 0


def cmd_url(args: argparse.Namespace) -> int:
    if not (record := server.running(directory_of(args))):
        return 1
    print(server.dashboard_url(record))
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    if not (record := server.running(directory_of(args))):
        raise SystemExit(f"no dashboard is serving {directory_of(args)}; run `live-dashboard start --dir {directory_of(args)}`")
    webbrowser.open(server.dashboard_url(record))
    return 0


def fetch_state(record: dict) -> dict:
    with urllib.request.urlopen(f"{record['url']}state.json", timeout=server.HEALTH_TIMEOUT_SECONDS * 5) as response:
        return json.load(response)


def card_lines(state: dict) -> dict[str, str]:
    lines = {f"card:{card['id']}": f"{card['status']}{': ' + card['error'] if card['error'] else ''}" for card in state["cards"]}
    lines["layout"] = state["layout_error"] or "current"
    return lines


def cmd_status(args: argparse.Namespace) -> int:
    directory = directory_of(args)
    if not args.changes:
        if not (record := server.running(directory)):
            raise SystemExit(f"no dashboard is serving {directory}")
        state = fetch_state(record)
        if args.json:
            print(json.dumps({"layout_error": state["layout_error"], "counts": state["counts"], "cards": [{key: card[key] for key in ("id", "use", "status", "as_of", "error", "ms")} for card in state["cards"]]}, indent=2))
        else:
            print("\n".join(f"{name}\t{line}" for name, line in card_lines(state).items()))
        return 0
    known: dict[str, str] | None = None
    while True:
        record = server.running(directory)
        try:
            lines = card_lines(fetch_state(record)) if record else {"server": "down"}
        except (urllib.error.URLError, OSError, ValueError):
            lines = {"server": "unreachable"}
        for name, line in lines.items():
            if known is not None and known.get(name) != line:
                print(f"{name}\t{known.get(name, 'new')} -> {line}", flush=True)
        known = lines
        time.sleep(CHANGES_SECONDS)


def cmd_card(args: argparse.Namespace) -> int:
    directory = directory_of(args)
    if not (record := server.running(directory)):
        raise SystemExit(f"no dashboard is serving {directory}")
    try:
        with urllib.request.urlopen(f"{record['url']}cards/{args.id}.{'json' if args.json else 'md'}", timeout=server.HEALTH_TIMEOUT_SECONDS * 5) as response:
            print(response.read().decode(), end="")
    except urllib.error.HTTPError as failure:
        raise SystemExit(failure.read().decode().strip()) from failure
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    directory = directory_of(args)
    child = subprocess.run([sys.executable, SCRIPT, "check-child", "--dir", str(directory), *(["--only", args.only] if args.only else []), *(["--static"] if args.static else [])], capture_output=True, text=True)
    if child.returncode not in (0, 1):
        print(f"check crashed: {(child.stderr.strip().splitlines() or ['no output'])[-1]}")
        return 1
    found = json.loads(child.stdout.splitlines()[-1])
    print("\n".join(found) if found else f"{directory / layout.LAYOUT_FILE}: every card checks clean")
    return 1 if found else 0


def cmd_check_child(args: argparse.Namespace) -> int:
    found = check.defects(directory_of(args), args.only, not args.static, dict(os.environ))
    print(json.dumps(found), file=sys.__stdout__, flush=True)
    return 1 if found else 0


def cmd_init(args: argparse.Namespace) -> int:
    directory = directory_of(args)
    target = directory / layout.LAYOUT_FILE
    if target.exists():
        raise SystemExit(f"{target} exists; edit it, or remove it to start from a preset")
    preset = PRESETS / f"{args.preset}.yaml"
    if not preset.exists():
        raise SystemExit(f"no preset {args.preset}; presets: {', '.join(sorted(path.stem for path in PRESETS.glob('*.yaml')))}")
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / server.CONTEXT_FILE).exists():
        (directory / server.CONTEXT_FILE).write_text(json.dumps({"id": directory.name, "title": args.title or directory.name}, indent=2) + "\n")
    text = preset.read_text()
    target.write_text(re.sub(r"^title: .*$", f"title: {json.dumps(args.title)}", text, count=1, flags=re.M) if args.title else text)
    print(f"wrote {target} from preset {args.preset}; tailor it, then run `live-dashboard check --dir {directory}`")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    directory = directory_of(args)
    if not COMPONENT_NAME.match(args.name):
        raise SystemExit(f"{args.name!r} must be lowercase words joined by hyphens")
    if args.payload not in registry.PAYLOADS:
        raise SystemExit(f"--payload must be one of {', '.join(registry.PAYLOADS)}")
    module = directory / "components" / f"{args.name.replace('-', '_')}.py"
    if module.exists():
        raise SystemExit(f"{module} exists")
    layout_path = directory / layout.LAYOUT_FILE
    before = layout_path.read_text()
    title = args.name.replace("-", " ").capitalize()
    after = before.rstrip("\n") + "\n" + APPENDED.format(title=title, name=args.name)
    layout.parse(after)
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(SCAFFOLD.format(payload=args.payload, name=args.name, title=title, function=args.name.replace("-", "_")))
    layout_path.write_text(after)
    print(f"wrote {module} and a local.{args.name} card at the end of {layout_path}; edit both, then run `live-dashboard check --dir {directory} --only local.{args.name}`")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    directory = directory_of(args)
    facts = server.read_facts(directory)
    registry.builtins()
    registry.load_packs(facts["packs"])
    registry.load_local(directory)
    built = layout.load(directory, facts)
    cards = [card for card in built.cards if args.card in (None, card.id)]
    if not cards:
        raise SystemExit(f"no card {args.card} in {directory / layout.LAYOUT_FILE}")
    scheduler = Scheduler(directory, facts, executor=InlineExecutor())
    scheduler.apply(cards)
    for card in cards:
        if card.spec is not None:
            scheduler.run_once(card.id)
    envelopes = scheduler.envelopes(cards)
    if args.md:
        print("\n\n".join(f"## {card['title']}\n\n" + (payloads.markdown(card["kind"], card["payload"]) if card["payload"] else f"_{card['status']}: {card['error']}_") for card in envelopes))
    else:
        print(json.dumps(envelopes, indent=2, default=str))
    return 0


def catalog_text() -> str:
    registry.builtins()
    out = ["# Component catalog", "", "Generated by `live-dashboard catalog`; CI fails when it drifts from the code.", ""]
    out += ["| Component | Answers | Reads | Payload | Default cadence | Parameters |", "|---|---|---|---|---|---|"]
    specs = sorted(registry.REGISTRY.values(), key=lambda spec: (spec.id.startswith("release."), spec.id))
    for spec in specs:
        params = ", ".join(f"`{name}`" + ("" if param.default is registry.MISSING else f"={json.dumps(param.default, default=str)}") for name, param in spec.params.items()) or "none"
        out.append(f"| [`{spec.id}`](#{spec.id.replace('.', '')}) | {spec.question} | {', '.join(spec.reads)} | {spec.payload.__name__} | {spec.every} | {params} |")
    for spec in specs:
        out += ["", f"## {spec.id}", "", f"_{spec.question}_", "", f"**{spec.title}.** {spec.doc}".rstrip(), "", "Reads: " + ", ".join(f"`{source}`" for source in spec.reads) + "."]
        if spec.actions:
            out += ["", "Actions: " + ", ".join(f"`{name}`" for name in sorted(spec.actions)) + "."]
    return "\n".join(out) + "\n"


def cmd_catalog(args: argparse.Namespace) -> int:
    text = catalog_text()
    if args.check:
        if CATALOG.read_text() != text:
            print(f"{CATALOG} is stale; run `live-dashboard catalog`", file=sys.stderr)
            return 1
        return 0
    CATALOG.write_text(text)
    print(f"wrote {CATALOG}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="live-dashboard", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {
        "serve": cmd_serve,
        "start": cmd_start,
        "stop": cmd_stop,
        "url": cmd_url,
        "open": cmd_open,
        "status": cmd_status,
        "card": cmd_card,
        "check": cmd_check,
        "check-child": cmd_check_child,
        "init": cmd_init,
        "new": cmd_new,
        "snapshot": cmd_snapshot,
        "catalog": cmd_catalog,
    }
    for name, handler in commands.items():
        command = sub.add_parser(name)
        command.set_defaults(handler=handler)
        if name == "catalog":
            command.add_argument("--check", action="store_true", help="exit 1 when reference/catalog.md is stale")
            continue
        command.add_argument("--dir", required=True, help="the dashboard dir: context.json, layout.yaml, components/")
        if name in ("serve", "start"):
            command.add_argument("--host", default="127.0.0.1")
        if name == "serve":
            command.add_argument("--port", type=int)
        if name == "status":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--json", action="store_true")
            mode.add_argument("--changes", action="store_true", help="print only transitions, forever; a Monitor target")
        if name == "card":
            command.add_argument("id", help="a card id from layout.yaml")
            command.add_argument("--json", action="store_true", help="the card's envelope, payload included")
        if name in ("check", "check-child"):
            command.add_argument("--only", help="a card id or component id")
            command.add_argument("--static", action="store_true", help="parse and bind without running any component")
        if name == "init":
            command.add_argument("--preset", required=True)
            command.add_argument("--title")
        if name == "new":
            command.add_argument("name")
            command.add_argument("--payload", required=True)
        if name == "snapshot":
            command.add_argument("--card")
            command.add_argument("--md", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
