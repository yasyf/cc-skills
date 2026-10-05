#!/usr/bin/env python3
"""Tests for the records scaffold, the prevention check and the board.

  python3 scripts/test_retro_new.py
"""
import argparse, contextlib, datetime, io, json, sys, tempfile, unittest, zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retro
import retro_live
import retro_new

PT = zoneinfo.ZoneInfo("America/Los_Angeles")


def option(oid, label, **extra):
    return {"id": oid, "t": label, "hint": "one line", "buys": "speed", "pros": [{"text": "fast"}], **extra}


class FactsBullets(unittest.TestCase):
    def rows(self, text):
        path = Path(tempfile.mkdtemp()) / "facts.md"
        path.write_text(text)
        return retro_new.facts_rows(path, datetime.date(2026, 10, 4), PT, retro, retro_live)

    def test_clock_bullets_roll_past_midnight(self):
        rows = self.rows("- 9:18 PM: build froze 28 databases\n- 12:24:31 AM first failure\n- not a time\n")
        self.assertEqual([r["ts"] for r in rows], ["2026-10-04T21:18:00-07:00", "2026-10-05T00:24:31-07:00"])

    def test_a_range_takes_its_start_and_links_become_refs(self):
        rows = self.rows("- 12:46-12:47 AM router reverted (https://buildkite.com/forge/release/builds/1224)\n")
        self.assertEqual(rows[0]["ts"], "2026-10-04T00:46:00-07:00")
        self.assertEqual(rows[0]["refs"], ["https://buildkite.com/forge/release/builds/1224"])
        self.assertNotIn("()", rows[0]["text"])
        self.assertEqual(rows[0]["kind"], "mitigation")

    def test_lowercase_lane_names_do_not_read_as_markers(self):
        self.assertEqual(retro_new.kind_of("launch silk-routing-revert lane"), "report")
        self.assertEqual(retro_new.kind_of("FIX-LIVE routing verified"), "mitigation")


class Prevention(unittest.TestCase):
    def check(self, prevention):
        root = Path(tempfile.mkdtemp())
        R = json.loads((retro.TEMPLATES / "starter" / "retro.json").read_text())
        R["prevention"] = prevention
        rep = retro.Report(True)
        ids = retro.check_prevention(rep, R)
        return ids, rep

    def test_a_well_formed_question_passes(self):
        ids, rep = self.check([{"id": "P1", "t": "Who approves a switch?", "h": "switch approval",
                                "options": [option("P1a", "Gate it", recommended=True), option("P1b", "Rules only")]}])
        self.assertEqual(ids, {"P1"})
        self.assertEqual(rep.errors, [])

    def test_one_option_and_two_recommendations_fail(self):
        _, rep = self.check([{"id": "P1", "t": "Q", "options": [option("P1a", "A", recommended=True)]},
                             {"id": "P2", "t": "Q", "options": [option("P2a", "A", recommended=True),
                                                                 option("P2b", "B", recommended=True)]}])
        joined = "\n".join(rep.errors)
        self.assertIn("P1 offers 1 option", joined)
        self.assertIn("P2 recommends more than one", joined)

    def test_option_ids_belong_to_their_question(self):
        _, rep = self.check([{"id": "P1", "t": "Q", "options": [option("P2a", "A"), option("P1b", "B")]}])
        self.assertIn("option id 'P2a'", "\n".join(rep.errors))


class Board(unittest.TestCase):
    def test_board_carries_facts_detail_and_the_recommendation(self):
        root = Path(tempfile.mkdtemp())
        R = {"meta": {"title": "T", "subtitle": "S"},
             "prevention": [{"id": "P1", "t": "Who approves?", "text": "The owner never approved. More.",
                             "options": [option("P1a", "Gate it", recommended=True, cons=[{"text": "slow"}]),
                                         option("P1b", "Rules only", text="Add a rule.")]}]}
        (root / "retro.json").write_text(json.dumps(R))
        out = root / "board.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(retro_new.board(argparse.Namespace(dir=str(root), out=str(out), retro=retro)), 0)
        doc = json.loads(out.read_text())
        self.assertEqual(doc["version"], 1)
        card = doc["blocks"][0]
        self.assertEqual(card["summary"], "The owner never approved.")
        choice = card["children"][1]
        self.assertEqual(choice["options"][0]["facts"], [{"label": "buys", "value": "speed"}])
        self.assertEqual(choice["options"][0]["detail"], {"pros": ["fast"], "cons": ["slow"]})
        self.assertTrue(choice["options"][0]["recommended"])
        self.assertNotIn("recommended", choice["options"][1])
        self.assertEqual(choice["options"][1]["detail"]["md"], "Add a rule.")


if __name__ == "__main__":
    unittest.main()
