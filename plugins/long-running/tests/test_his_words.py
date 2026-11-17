import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "lib/lane" / f"{name}.md" for name in ("owner", "work", "audit", "rank", "danger")] + [ROOT / "skills/long-running/reference/voice.md"]
QUOTED = re.compile(r'(?:[Hh]is words[^"\n]{0,80}?: |[Hh]e (?:asked|said|wrote)[^"\n]{0,80}?: |\()"([^"\n]{12,})"')
SOURCED = re.compile(r"slack\.com/archives/|\bsession [0-9a-f]{8}\b")
WINDOW = 160


def test_every_quote_a_page_gives_as_his_names_where_he_said_it() -> None:
    known = json.loads((ROOT / "his-words.json").read_text())
    listed = {row["passage"] for row in known["his"]} | set(known["ours"])
    unsourced = []
    for page in PAGES:
        text = page.read_text()
        for found in QUOTED.finditer(text):
            beside = text[max(0, found.start() - WINDOW) : found.end() + WINDOW]
            if found[1] not in listed and not SOURCED.search(beside):
                unsourced.append(f"{page.relative_to(ROOT)}: {found[1]!r}")
    assert not unsourced, "quotes given as his with no source: add each to his-words.json with where he said it, write its permalink or session beside it, or list it under ours if it is our own example:\n" + "\n".join(unsourced)
