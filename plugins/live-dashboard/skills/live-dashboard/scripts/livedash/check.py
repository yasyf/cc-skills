from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
from pathlib import Path

from livedash import layout, payloads, registry, secrets
from livedash.context import Context, failure_text, now
from livedash.server import read_facts


SCAFFOLD_QUESTION = "TODO"


def selected(cards: list[layout.Card], only: str | None) -> list[layout.Card]:
    return [card for card in cards if only in (None, card.id, card.use)]


def run_card(card: layout.Card, directory: Path, facts: dict, env: dict) -> list[str]:
    spec = card.spec
    printed = io.StringIO()
    outcome: dict = {}

    def call() -> None:
        try:
            with contextlib.redirect_stdout(printed):
                outcome["result"] = spec.fn(Context(directory, facts, now(), spec.timeout), **card.bound)
        except Exception as failure:
            outcome["error"] = failure

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(spec.timeout)
    where = f"{card.id} ({card.use}, layout.yaml:{card.line})"
    if worker.is_alive():
        sys.stdout = sys.__stdout__
        return [f"{where}: did not finish within its {spec.timeout:g}s timeout"]
    found = [f"{where}: printed {len(printed.getvalue())} characters to stdout; a component prints nothing"] if printed.getvalue() else []
    if "error" in outcome:
        failure = outcome["error"]
        return [*found, f"{where}: {secrets.redacted(failure_text(failure) if isinstance(failure, OSError) else f'{type(failure).__name__}: {failure}', env)}"]
    result = outcome["result"]
    if not isinstance(result, spec.payload):
        return [*found, f"{where}: returned {type(result).__name__}, not the {spec.payload.__name__} its signature names"]
    found += [f"{where}: {problem}" for problem in payloads.problems(result)]
    if names := secrets.hits(json.dumps(result.json(), default=str), env):
        found.append(f"{where}: secret-shaped value ({', '.join(names)})")
    return found


def defects(directory: Path, only: str | None, run: bool, env: dict) -> list[str]:
    facts = read_facts(directory)
    registry.builtins()
    registry.load_packs(facts["packs"])
    registry.load_local(directory)
    found = [f"components/{name.rsplit('.', 1)[-1]}.py: {error}" for name, error in sorted(registry.LOAD_ERRORS.items())]
    try:
        built = layout.load(directory, facts)
    except layout.LayoutError as failure:
        return [*found, failure.where(directory / layout.LAYOUT_FILE)]
    cards = selected(built.cards, only)
    if only and not cards:
        return [*found, f"no card or component {only} in {directory / layout.LAYOUT_FILE}"]
    for card in cards:
        if card.spec is None:
            found.append(f"{card.id} (layout.yaml:{card.line}): {card.error}")
            continue
        if card.question.startswith(SCAFFOLD_QUESTION):
            found.append(f"{card.id} (layout.yaml:{card.line}): question is still the scaffold's; name the one question this card answers")
        if run:
            found += run_card(card, directory, facts, env)
    return found
