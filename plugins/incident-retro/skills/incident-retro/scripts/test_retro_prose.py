#!/usr/bin/env python3
"""Tests for the parts of retro_prose that two concurrent runs would corrupt.

  python3 scripts/test_retro_prose.py
"""
import contextlib, io, json, os, signal, subprocess, sys, tempfile, unittest
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
        lock = self.root / retro_prose.WRITE_LOCK
        self.assertTrue(lock.exists(), "the lock file is the claim's identity and outlives the run")
        self.assertEqual(lock.read_text().strip(), "", "a released claim names no holder")

    def test_the_lock_file_is_never_unlinked(self):
        """Unlinking it lets the next run lock a fresh inode while this one still holds the old."""
        lock = self.root / retro_prose.WRITE_LOCK
        with retro_prose.Owner(self.root):
            first = lock.stat().st_ino
        with retro_prose.Owner(self.root):
            self.assertEqual(lock.stat().st_ino, first, "the claim moved to a new inode")

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
                                     "stale": False, "quick": False, "dry_run": False, "batch": 4,
                                     "detach": False, "await_run": False})()
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
    def test_an_empty_headline_counts_as_stale(self):
        """--quick intersects with the stale set, so a headline missing from it is never written."""
        store = {"meta.title": {"kind": retro_prose.HEADLINE, "text": ""},
                 "meta.subtitle": {"kind": retro_prose.SUBTITLE, "text": ""},
                 "C1.code.caption": {"kind": "prose", "text": ""}}
        stale = [a for a, s in store.items()
                 if not s["text"].strip() and s["kind"] in retro_prose.REQUIRED_KINDS]
        self.assertEqual(sorted(stale), ["meta.subtitle", "meta.title"])

    def test_a_required_short_name_that_is_missing_counts_as_stale(self):
        store = {"T1.h": {"kind": "short name", "text": ""},
                 "C1.code.caption": {"kind": "prose", "text": ""},
                 "C1.text": {"kind": "prose", "text": "written"}}
        lock = {"C1.text": {"sha256": retro_prose.digest("written")}}
        stale = [a for a, s in store.items()
                 if (not s["text"].strip() and s["kind"] == "short name")
                 or (s["text"].strip() and (lock.get(a) or {}).get("sha256") != retro_prose.digest(s["text"]))]
        self.assertEqual(stale, ["T1.h"])


class Grandfathering(unittest.TestCase):
    """The migration pins untouched fields once; it is not a way to launder a later hand edit."""

    def setUp(self):
        self.retro = type("Retro", (), {"plugin_version": staticmethod(lambda: "0.3.0")})
        self.root = Path(tempfile.mkdtemp())

    def test_an_untouched_field_is_pinned_as_legacy(self):
        store = {"C1.text": {"kind": "prose", "text": "original wording", "holder": {}, "key": "text"}}
        lock = {"fields": {}}
        self.assertEqual(retro_prose.grandfather(self.retro, {}, self.root, store, set(), lock), 1)
        self.assertEqual(lock["fields"]["C1.text"]["kind"], retro_prose.LEGACY)
        self.assertEqual(lock["fields"]["C1.text"]["grandfathered"], "0.3.0")

    def test_a_hand_edit_cannot_relaunder_itself_by_rerunning_the_migration(self):
        store = {"C1.text": {"kind": "prose", "text": "edited by hand", "holder": {}, "key": "text"}}
        lock = {"fields": {"C1.text": {"sha256": retro_prose.digest("original wording"),
                                       "kind": retro_prose.LEGACY, "grandfathered": "0.3.0"}}}
        self.assertEqual(retro_prose.grandfather(self.retro, {}, self.root, store, set(), lock), 0)
        self.assertNotEqual(lock["fields"]["C1.text"]["sha256"], retro_prose.digest("edited by hand"))

    def test_an_astra_written_field_keeps_its_astra_provenance(self):
        store = {"C1.text": {"kind": "prose", "text": "astra wording", "holder": {}, "key": "text"}}
        lock = {"fields": {"C1.text": {"sha256": retro_prose.digest("astra wording"), "run": "/runs/x"}}}
        retro_prose.grandfather(self.retro, {}, self.root, store, set(), lock)
        self.assertNotIn("kind", lock["fields"]["C1.text"])


class HeadlineAndSubtitle(unittest.TestCase):
    """0.3.0 required a compact title but gave no way to write one. Both are prose fields now."""

    def setUp(self):
        self.retro = type("Retro", (), {"DOC_TITLE_CHARS": 60, "DOC_TITLE_WORDS": 8, "SUBTITLE_CHARS": 120,
                                        "SUBTITLE_WORDS": 20, "words": staticmethod(lambda s: len(s.split()))})

    def spec(self, kind, text):
        return {"kind": kind, "text": text, "holder": {}, "key": "title", "grounded": True}

    def test_an_overlong_headline_is_over_budget(self):
        long = "A migration ran before all services using the schema were deployed, so run creation stopped"
        self.assertTrue(retro_prose.over_budget(self.retro, self.spec(retro_prose.HEADLINE, long)))

    def test_a_compact_headline_is_within_budget(self):
        self.assertFalse(retro_prose.over_budget(self.retro, self.spec(retro_prose.HEADLINE,
                                                                      "Run creation stopped for 71 minutes")))

    def test_a_headline_may_not_invent_a_fact_the_subtitle_lacks(self):
        source = json.dumps({"subtitle": "A config set burst limits to zero, stopping checkouts."})
        self.assertTrue(retro_prose.fact_drift("", "Checkouts blocked for 94 minutes", source))

    def test_a_headline_drawn_from_the_subtitle_is_accepted(self):
        source = json.dumps({"subtitle": "A config set burst limits to zero, stopping checkouts for 94 minutes."})
        self.assertEqual(retro_prose.fact_drift("", "Checkouts blocked for 94 minutes", source), [])

    def test_shortening_an_overlong_headline_may_drop_words(self):
        source = json.dumps({"subtitle": "A migration ran before the workers deployed, so run creation stopped."})
        self.assertEqual(retro_prose.fact_drift("", "Run creation stopped", source), [])


class OperatorNotes(unittest.TestCase):
    """An operator steers the writing without writing it."""

    def store(self):
        return {"meta.title": {"kind": retro_prose.HEADLINE, "text": ""},
                "C1.text": {"kind": "prose", "text": "x"}}

    def test_an_addressed_note_reaches_only_that_field(self):
        store = self.store()
        self.assertEqual(retro_prose.attach_notes(store, ["meta.title=name the cause"]), "")
        self.assertEqual(store["meta.title"]["note"], "name the cause")
        self.assertNotIn("note", store["C1.text"])

    def test_a_bare_note_reaches_every_field(self):
        store = self.store()
        retro_prose.attach_notes(store, ["prefer the operator's words"])
        self.assertEqual({s["note"] for s in store.values()}, {"prefer the operator's words"})

    def test_a_note_for_an_unknown_address_is_reported(self):
        self.assertEqual(retro_prose.attach_notes(self.store(), ["meta.nope=hi"]), "meta.nope")

    def test_notes_survive_the_reread_under_the_claim(self):
        """The record is re-read inside the lock, which once discarded the notes attached before it."""
        root = Path(tempfile.mkdtemp())
        (root / "retro.json").write_text(json.dumps({"meta": {"slug": "s"}}))
        seen, original_read, original_write = [], retro_prose.read_record, retro_prose.write_prose
        retro_prose.read_record = lambda retro, where: ({"meta": {}}, self.store())
        retro_prose.write_prose = lambda retro, R, root, args, store, *a, **k: seen.append(
            store["meta.title"].get("note")) or 0
        try:
            args = type("Args", (), {"retro": None, "dir": str(root), "list": False,
                                     "field": ["meta.title"], "stale": False, "quick": False,
                                     "dry_run": False, "batch": 4, "note": ["meta.title=name the cause"],
                                     "detach": False, "await_run": False})()
            retro_prose.prose(args)
        finally:
            retro_prose.read_record, retro_prose.write_prose = original_read, original_write
        self.assertEqual(seen, ["name the cause"], "the note did not survive the re-read under the claim")


class BatchedLint(unittest.TestCase):
    """The model pass runs once per batch, and each finding still names the field it came from."""

    def test_findings_are_attributed_to_the_field_they_fall_in(self):
        landed = {"C1.text": "We leverage a robust solution.", "C2.text": "The column rename broke inserts.",
                  "C3.text": "It is not just fast, but also powerful."}
        deep = retro_prose.lint(landed, True)
        self.assertIn("C1.text", deep)
        self.assertNotIn("C2.text", deep, "a clean field must not inherit a neighbor's finding")
        for addr, violations in deep.items():
            for v in violations:
                self.assertLessEqual(v["startIndex"], len(landed[addr]), f"{addr} offset escaped its field")


class FactFreeze(unittest.TestCase):
    def test_a_moved_number_is_refused(self):
        self.assertTrue(retro_prose.fact_drift("ran for 71 minutes", "ran for 17 minutes", ""))

    def test_a_reworded_sentence_keeping_every_fact_is_accepted(self):
        self.assertEqual(retro_prose.fact_drift("The release renamed the column at 19:32Z.",
                                                "At 19:32Z the release renamed the column.", ""), [])

    def test_a_new_short_name_may_not_invent_a_number(self):
        self.assertTrue(retro_prose.fact_drift("", "Outage of 40 minutes", json.dumps({"t": "Column rename"})))

    def test_wrapping_a_number_in_backticks_is_markup_not_a_fact_change(self):
        self.assertEqual(retro_prose.fact_drift("workers exceeded their 8 GiB limit",
                                                "workers exceeded their `8 GiB` limit", ""), [])

    def test_an_identifier_survives_losing_its_backticks(self):
        self.assertEqual(retro_prose.fact_drift("column `manifest_section` was renamed",
                                                "column manifest_section was renamed", ""), [])

    def test_dropping_an_identifier_is_refused(self):
        self.assertTrue(retro_prose.fact_drift("column manifest_section was renamed",
                                               "the column was renamed", ""))


class DetachedRun(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.lane = self.root / "lane"
        self.poll, retro_prose.AWAIT_POLL = retro_prose.AWAIT_POLL, 0.05
        self.children = []

    def tearDown(self):
        retro_prose.AWAIT_POLL = self.poll
        for pid in self.children:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def detach(self, script: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            code = retro_prose.detach(self.root, self.lane, ["-c", script])
        self.children.append(int((self.lane / retro_prose.RUN_PID).read_text()))
        return code

    def await_detached(self, seconds: float):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = retro_prose.await_detached(self.root, self.lane, seconds)
        return code, out.getvalue()

    def test_await_returns_the_runs_exit_status_and_its_log(self):
        self.assertEqual(self.detach("print('prose: wrote 3 field(s)'); raise SystemExit(3)"), 0)
        code, out = self.await_detached(30)
        self.assertEqual(code, 3)
        self.assertIn("prose: wrote 3 field(s)", out)
        self.assertIn("exited 3", out)

    def test_await_hands_back_a_fresh_await_line_while_the_run_is_still_going(self):
        self.detach("import time; time.sleep(30)")
        code, out = self.await_detached(0.2)
        self.assertEqual(code, retro_prose.STILL_RUNNING)
        self.assertIn("AWAIT: ", out)
        self.assertTrue(out.rstrip().endswith("--await"))

    def test_a_second_detach_is_refused_while_the_first_runs(self):
        self.detach("import time; time.sleep(30)")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(retro_prose.detach(self.root, self.lane, ["-c", "pass"]), 1)
        self.assertIn("already writing", err.getvalue())

    def test_await_without_a_detached_run_says_how_to_start_one(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(retro_prose.await_detached(self.root, self.lane, 0), 1)
        self.assertIn("--detach", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
