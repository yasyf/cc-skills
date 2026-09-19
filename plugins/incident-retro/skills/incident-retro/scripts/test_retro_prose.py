#!/usr/bin/env python3
"""Tests for the parts of retro_prose that two concurrent runs would corrupt.

  python3 scripts/test_retro_prose.py
"""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retro_prose


class ExclusiveClaim(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_second_claim_is_refused_while_the_first_is_held(self):
        with retro_prose.Owner(self.root):
            with self.assertRaises(retro_prose.Busy) as refused:
                with retro_prose.Owner(self.root):
                    pass
        self.assertIn("pid", str(refused.exception))

    def test_the_claim_is_released_when_the_run_ends(self):
        with retro_prose.Owner(self.root):
            pass
        with retro_prose.Owner(self.root):
            pass
        self.assertFalse((self.root / retro_prose.WRITE_LOCK).exists())

    def test_a_crashed_run_does_not_wedge_the_retro(self):
        script = (f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parent)!r});"
                  f"from pathlib import Path; import retro_prose;"
                  f"claim = retro_prose.Owner(Path({str(self.root)!r})); claim.__enter__();"
                  f"print('held', flush=True); raise SystemExit(1)")
        crashed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertIn("held", crashed.stdout, f"the child never took the lock: {crashed.stderr}")
        self.assertTrue((self.root / retro_prose.WRITE_LOCK).exists())
        with retro_prose.Owner(self.root):
            pass


class RecordIsReadUnderTheClaim(unittest.TestCase):
    """A run that waited for the claim must not act on the copy it read before waiting."""

    def test_the_record_is_read_while_the_claim_is_held(self):
        root = Path(tempfile.mkdtemp())
        (root / "retro.json").write_text(json.dumps({"meta": {"slug": "s"}}))
        held = []
        original_read, original_write = retro_prose.read_record, retro_prose.write_prose
        record = {"meta": {"slug": "s"}}
        store = {"C1.text": {"kind": "prose", "text": "written", "holder": {}, "key": "text"}}

        def watching(retro, where):
            held.append((where / retro_prose.WRITE_LOCK).exists())
            return record, store

        retro_prose.read_record = watching
        retro_prose.write_prose = lambda *a, **k: 0
        try:
            args = type("Args", (), {"retro": None, "dir": str(root), "list": False, "field": None,
                                     "stale": False, "dry_run": False, "batch": 4})()
            retro_prose.prose(args)
        finally:
            retro_prose.read_record, retro_prose.write_prose = original_read, original_write
        self.assertEqual(held[0], False, "the first read happens before the claim, which is fine")
        self.assertTrue(len(held) > 1 and held[1], "the record was never re-read under the claim")


class AtomicWrite(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_the_reader_never_sees_a_half_written_file(self):
        path = self.root / "prose.lock.json"
        retro_prose.write_atomic(path, json.dumps({"fields": {"C1.text": {"sha256": "a"}}}))
        retro_prose.write_atomic(path, json.dumps({"fields": {"C1.text": {"sha256": "b"}}}))
        self.assertEqual(json.loads(path.read_text())["fields"]["C1.text"]["sha256"], "b")

    def test_it_leaves_no_scratch_behind(self):
        retro_prose.write_atomic(self.root / "prose.lock.json", "{}")
        self.assertEqual([p.name for p in self.root.iterdir()], ["prose.lock.json"])


class StaleFields(unittest.TestCase):
    def test_a_required_short_name_that_is_missing_counts_as_stale(self):
        store = {"T1.h": {"kind": "short name", "text": ""},
                 "C1.code.caption": {"kind": "prose", "text": ""},
                 "C1.text": {"kind": "prose", "text": "written"}}
        lock = {"C1.text": {"sha256": retro_prose.digest("written")}}
        stale = [a for a, s in store.items()
                 if (not s["text"].strip() and s["kind"] == "short name")
                 or (s["text"].strip() and (lock.get(a) or {}).get("sha256") != retro_prose.digest(s["text"]))]
        self.assertEqual(stale, ["T1.h"])


class FactFreeze(unittest.TestCase):
    def test_a_moved_number_is_refused(self):
        self.assertTrue(retro_prose.fact_drift("ran for 71 minutes", "ran for 17 minutes", ""))

    def test_a_reworded_sentence_keeping_every_fact_is_accepted(self):
        self.assertEqual(retro_prose.fact_drift("The release renamed the column at 19:32Z.",
                                                "At 19:32Z the release renamed the column.", ""), [])

    def test_a_new_short_name_may_not_invent_a_number(self):
        self.assertTrue(retro_prose.fact_drift("", "Outage of 40 minutes", json.dumps({"t": "Column rename"})))


if __name__ == "__main__":
    unittest.main(verbosity=2)
