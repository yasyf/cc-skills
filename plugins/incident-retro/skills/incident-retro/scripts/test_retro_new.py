#!/usr/bin/env python3
"""Tests for the records scaffold, the prevention check and the board.

  python3 scripts/test_retro_new.py
"""
import argparse, contextlib, datetime, io, json, sys, tempfile, unittest, unittest.mock, zoneinfo
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


class Remediation(unittest.TestCase):
    def report(self, **extra):
        R = json.loads((retro.TEMPLATES / "starter" / "retro.json").read_text())
        R["meta"]["date"] = "2026-10-05"
        R.update(extra)
        rep = retro.Report(True)
        retro.check_remediation(rep, R, "draft")
        return rep

    def test_a_retro_without_remediation_fails(self):
        joined = "\n".join(self.report().errors)
        for part in ("remediation.done is empty", "remediation.lanes is empty", "prevention is empty"):
            self.assertIn(part, joined)

    def test_picks_need_an_owner_and_every_question_a_pick(self):
        rep = self.report(remediation={"done": [{"text": "Reverted routing."}], "lanes": [{"name": "fix", "text": "x"}]},
                          prevention=[{"id": "P1", "t": "Q", "options": [option("P1a", "A"), option("P1b", "B")]}])
        self.assertIn("P1 has no picked option", "\n".join(rep.errors))

    def test_a_complete_remediation_passes(self):
        rep = self.report(remediation={"done": [{"text": "Reverted routing."}], "lanes": [{"name": "fix", "text": "x"}]},
                          prevention=[{"id": "P1", "t": "Q", "options": [
                              option("P1a", "A", picked=True, owner="root",
                                     links=["https://github.com/Forge-AI/monorepo/pull/1"]), option("P1b", "B")]}])
        self.assertEqual(rep.errors, [])

    def test_retros_before_the_cutoff_are_not_held_to_it(self):
        R = json.loads((retro.TEMPLATES / "starter" / "retro.json").read_text())
        R["meta"]["date"] = "2026-09-30"
        rep = retro.Report(True)
        retro.check_remediation(rep, R, "draft")
        self.assertEqual(rep.errors, [])


    def test_a_pick_without_a_pr_needs_a_named_lane(self):
        base = {"remediation": {"done": [{"text": "Reverted."}], "lanes": [{"name": "rules-lane", "text": "x"}]}}
        orphan = self.report(**base, prevention=[{"id": "P1", "t": "Q", "options": [
            option("P1a", "A", picked=True, owner="root"), option("P1b", "B")]}])
        self.assertIn("names no lane", "\n".join(orphan.errors))
        carried = self.report(**base, prevention=[{"id": "P1", "t": "Q", "options": [
            option("P1a", "A", picked=True, owner="root", lane="rules-lane"), option("P1b", "B")]}])
        self.assertEqual(carried.errors, [])


class LockedWriter(unittest.TestCase):
    def lock_report(self, entry, fallback=None):
        root = Path(tempfile.mkdtemp())
        R = json.loads((retro.TEMPLATES / "starter" / "retro.json").read_text())
        R["summary"]["text"] = "Opus wrote this."
        if fallback:
            R["meta"]["proseFallback"] = fallback
        (root / "prose.lock.json").write_text(json.dumps({"model": entry.get("model", ""), "fields": {"summary.text": entry}}))
        rep = retro.Report(True)
        retro.sibling_module("retro_prose").check_lock(retro, rep, R, root)
        return rep.errors

    def test_every_recorded_writer_model_passes(self):
        prose = retro.sibling_module("retro_prose")
        digest = prose.digest("Opus wrote this.")
        for entry in ({"sha256": digest, "model": prose.PROSE_MODEL}, {"sha256": digest, "run": "/runs/codex-ask.1"},
                      {"sha256": digest, "model": "claude-opus-5-5", "reason": "codex down"}):
            with self.subTest(entry=entry):
                self.assertFalse(any("summary.text" in e for e in self.lock_report(entry)))

    def test_a_historical_fallback_record_still_passes(self):
        prose = retro.sibling_module("retro_prose")
        entry = {"sha256": prose.digest("Opus wrote this."), "model": "claude-opus-5-5", "reason": "codex down"}
        fallback = {"model": "claude-opus-5-5", "reason": "codex down", "fields": ["summary.text"]}
        self.assertFalse(any("summary.text" in e for e in self.lock_report(entry, fallback)))


class CommsCheck(unittest.TestCase):
    URL = "https://docs.poetic.design/incident-retros/2026-10-04-x/"

    def check(self, text):
        path = Path(tempfile.mkdtemp()) / "draft.md"
        path.write_text(text)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = retro_new.comms_check(argparse.Namespace(draft=str(path), url=self.URL))
        return code, err.getvalue()

    def test_a_pull_request_link_is_refused(self):
        code, err = self.check(f"Retro: https://github.com/Forge-AI/design-docs/pull/65 and {self.URL}")
        self.assertEqual(code, 1)
        self.assertIn("pull/65", err)

    def test_the_rendered_page_is_required(self):
        self.assertEqual(self.check("Retro is up.")[0], 1)
        self.assertEqual(self.check(f"Retro: {self.URL}")[0], 0)


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


class AwaitMerges(unittest.TestCase):
    URL = "https://github.com/Forge-AI/design-docs/pull/73"

    def pr(self, state, merge_state, *verdicts):
        return json.dumps({"state": state, "mergeStateStatus": merge_state, "mergeCommit": {"oid": "abc"},
                           "statusCheckRollup": [{"name": f"check-{i}", "conclusion": v} for i, v in enumerate(verdicts)]})

    def test_a_clean_green_pr_is_merged_with_the_allowed_method(self):
        views = iter([self.pr("OPEN", "CLEAN", "SUCCESS", ""), self.pr("OPEN", "CLEAN", "SUCCESS", "SUCCESS"),
                      self.pr("MERGED", "UNKNOWN", "SUCCESS", "SUCCESS")])
        calls = []

        def gh(argv, cwd=None, check=True):
            calls.append((argv, cwd))
            if argv[:3] == ["gh", "pr", "view"]:
                return next(views)
            if argv[:2] == ["gh", "api"]:
                return json.dumps({"allow_squash_merge": False, "allow_rebase_merge": True, "allow_merge_commit": False})
            return ""

        root = Path(tempfile.mkdtemp()) / "incident-retros" / "slug"
        with unittest.mock.patch.object(retro_new, "run", gh), \
                unittest.mock.patch.object(retro_new, "pages_live", return_value=True), \
                unittest.mock.patch.object(retro_new, "rendered_url", return_value="https://docs.example/slug/"), \
                unittest.mock.patch.object(retro_new.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(retro_new.await_rendered(root, self.URL, 60), 0)
        merges = [(argv, cwd) for argv, cwd in calls if argv[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(merges, [(["gh", "pr", "merge", self.URL, "--rebase"], root.parents[1])])


if __name__ == "__main__":
    unittest.main()
