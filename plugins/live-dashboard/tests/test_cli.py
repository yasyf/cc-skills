from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from livedash import layout, registry

SKILL = Path(__file__).resolve().parents[1] / "skills" / "live-dashboard"
SCRIPT = SKILL / "scripts" / "live-dashboard.py"
LR_PACK = Path(__file__).resolve().parents[2] / "long-running" / "dashboard"
DRIVE_FACTS = {
    "id": "900424b6",
    "title": "release-v3",
    "program": "release-v3",
    "repo": "o/r",
    "checkout": "/checkout",
    "ledger": "L1",
    "sessions": ["s1"],
    "cci_drive": "release-v3",
    "orca_run": None,
    "state_dir": "/state/release-v3",
    "started_at": "2026-10-06T00:00:00Z",
    "packs": {"lr": str(LR_PACK)},
}


def cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("preset", sorted(path.stem for path in (SKILL / "presets").glob("*.yaml")))
def test_every_preset_binds_against_a_drives_facts(preset, tmp_path):
    registry.load_packs(DRIVE_FACTS["packs"])
    built = layout.build((SKILL / "presets" / f"{preset}.yaml").read_text(), DRIVE_FACTS, tmp_path)
    assert built.cards and all(card.spec is not None for card in built.cards)


def test_the_catalog_matches_the_code():
    assert cli("catalog", "--check").returncode == 0


def test_init_seeds_a_layout_and_new_adds_a_local_card_that_checks_clean(tmp_path):
    board = tmp_path / "board"
    assert cli("init", "--dir", str(board), "--preset", "live-test", "--title", "Bot latency").returncode == 0
    assert json.loads((board / "context.json").read_text()) == {"id": "board", "title": "Bot latency"}
    assert (board / "layout.yaml").read_text().startswith('title: "Bot latency"\n')
    assert "exists" in cli("init", "--dir", str(board), "--preset", "drive").stderr
    made = cli("new", "rulings", "--dir", str(board), "--payload", "Matrix")
    assert made.returncode == 0, made.stderr
    assert (board / "components" / "rulings.py").exists()
    assert (board / "layout.yaml").read_text().endswith("  - title: Rulings\n    components:\n      - {use: local.rulings}\n")
    scaffolded = cli("check", "--dir", str(board), "--only", "local.rulings")
    assert scaffolded.returncode == 1 and "question is still the scaffold's" in scaffolded.stdout
    module = board / "components" / "rulings.py"
    module.write_text(module.read_text().replace('question="TODO: which question does this card answer for the owner?"', 'question="Which PRs meet each ruling?"'))
    checked = cli("check", "--dir", str(board), "--only", "local.rulings")
    assert (checked.returncode, checked.stdout.strip()) == (0, f"{board / 'layout.yaml'}: every card checks clean")
    assert cli("check", "--dir", str(board), "--static").returncode == 0


def test_check_prints_each_defect_and_exits_one(tmp_path):
    board = tmp_path / "board"
    cli("init", "--dir", str(board), "--preset", "live-test")
    (board / "layout.yaml").write_text((board / "layout.yaml").read_text().replace("use: slack-feed", "use: slack-fed"))
    checked = cli("check", "--dir", str(board), "--static")
    assert checked.returncode == 1
    assert checked.stdout.startswith("layout.yaml:5: unknown component 'slack-fed'")


def test_check_reports_a_card_that_outlives_its_timeout(tmp_path):
    board = tmp_path / "board"
    cli("init", "--dir", str(board), "--preset", "live-test")
    (board / "components").mkdir(exist_ok=True)
    (board / "components" / "slow.py").write_text(
        "import time\nfrom livedash import Context, Markdown, component\n\n\n"
        '@component("slow", "Slow", question="Does a slow card time out?", reads=["nothing"], timeout="1s")\n'
        "def slow(ctx: Context) -> Markdown:\n    print('working')\n    time.sleep(30)\n    return Markdown('done')\n"
    )
    (board / "layout.yaml").write_text((board / "layout.yaml").read_text() + "  - title: Slow\n    components:\n      - {use: local.slow}\n")
    checked = cli("check", "--dir", str(board), "--only", "local.slow")
    assert checked.returncode == 1, checked.stderr
    assert checked.stdout.strip().endswith("did not finish within its 1s timeout")


def test_snapshot_renders_cards_as_markdown(tmp_path):
    board = tmp_path / "board"
    cli("init", "--dir", str(board), "--preset", "live-test")
    shot = cli("snapshot", "--dir", str(board), "--card", "test-channels", "--md")
    assert shot.stdout == "## Slack test threads\n\n_Not run: no test thread is named yet; set `threads` to Slack permalinks._\n"
