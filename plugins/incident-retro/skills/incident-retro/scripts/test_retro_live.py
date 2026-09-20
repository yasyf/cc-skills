#!/usr/bin/env python3
"""Tests for the live-incident commands and the schema fields the scrubber reads.

  python3 scripts/test_retro_live.py
"""
import argparse, io, json, os, shutil, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retro, retro_live, retro_prose

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "live"
INDEX_PAGE = """<!doctype html>
<html><body><main>
  <h2><a href="incident-retros/">Incident retrospectives</a></h2>
  <ul>
    <li><a class="card" href="incident-retros/2026-06-29-older-incident/" data-retro="2026-06-29-older-incident">
      <div class="t">An older incident</div>
      <div class="d">It happened first.</div>
      <div class="m">2026-06-29 · draft</div>
    </a></li>
  </ul>
</main></body></html>
"""
RETRO_INDEX_PAGE = """<!doctype html>
<html><body><main>
  <ol><li>Prepare the retrospective.</li></ol>
  <ul>
    <li><a class="card" href="2026-06-29-older-incident/" data-retro="2026-06-29-older-incident">
      <div class="t">An older incident</div>
      <div class="d">It happened first.</div>
      <div class="m">2026-06-29 · draft</div>
    </a></li>
  </ul>
</main></body></html>
"""
SLUG = "2026-09-02-executors-could-reach-browsers"


def docs_checkout() -> Path:
    docs = Path(tempfile.mkdtemp())
    (docs / retro_live.RETRO_DIR).mkdir()
    (docs / "index.html").write_text(INDEX_PAGE)
    (docs / retro_live.RETRO_DIR / "index.html").write_text(RETRO_INDEX_PAGE)
    return docs


def incident_dir() -> Path:
    incident = Path(tempfile.mkdtemp()) / "incident"
    shutil.copytree(FIXTURE, incident)
    return incident


def args(incident: Path, docs: Path, **extra):
    base = {"incident_dir": str(incident), "docs": str(docs), "slug": None,
            "repo": "Forge-AI/design-docs", "timezone": "America/Los_Angeles",
            "forbidden_terms": None, "retro": retro, "no_push": True,
            "tags": "browser-pool,executors,deploy"}
    base.update(extra)
    return argparse.Namespace(**base)


def run(fn, *call, **kwargs) -> int:
    with redirect_stdout(io.StringIO()):
        return fn(*call, **kwargs)


class Init(unittest.TestCase):
    def setUp(self):
        self.incident, self.docs = incident_dir(), docs_checkout()
        self.code = run(retro_live.init, args(self.incident, self.docs))
        self.root = self.docs / retro_live.RETRO_DIR / SLUG

    def test_the_scaffolded_retro_passes_check(self):
        self.assertEqual(self.code, 0)

    def test_it_opens_as_ongoing_with_the_branch_the_page_polls(self):
        R = json.loads((self.root / "retro.json").read_text())
        self.assertEqual(R["meta"]["status"], "ongoing")
        self.assertEqual(R["live"]["source"], {"repo": "Forge-AI/design-docs", "branch": f"live/{SLUG}"})
        self.assertEqual(R["live"]["phase"], "mitigated")

    def test_the_slug_is_written_back_to_the_incident_state(self):
        self.assertEqual(json.loads((self.incident / "state.json").read_text())["retro_slug"], SLUG)

    def test_both_index_pages_lead_with_the_ongoing_card(self):
        for page, href in ((self.docs / "index.html", f"{retro_live.RETRO_DIR}/{SLUG}/"),
                           (self.docs / retro_live.RETRO_DIR / "index.html", f"{SLUG}/")):
            text = page.read_text()
            self.assertIn(f'data-retro="{SLUG}"', text)
            self.assertIn(f'href="{href}"', text)
            self.assertLess(text.index(SLUG), text.index("2026-06-29-older-incident"))

    def test_a_second_init_refuses_rather_than_overwriting(self):
        self.assertEqual(run(retro_live.init, args(self.incident, self.docs)), 1)

    def test_an_existing_empty_directory_is_written_into(self):
        """A checkout may create the path ahead of init; only files in it are a refusal."""
        incident, docs = incident_dir(), docs_checkout()
        (docs / retro_live.RETRO_DIR / SLUG).mkdir(parents=True)
        self.assertEqual(run(retro_live.init, args(incident, docs)), 0)
        self.assertTrue((docs / retro_live.RETRO_DIR / SLUG / "retro.json").exists())


class Sync(unittest.TestCase):
    def setUp(self):
        self.incident, self.docs = incident_dir(), docs_checkout()
        run(retro_live.init, args(self.incident, self.docs))
        self.root = self.docs / retro_live.RETRO_DIR / SLUG
        self.code = run(retro_live.sync, args(self.incident, self.docs))

    def record(self) -> dict:
        return json.loads((self.root / "retro.json").read_text())

    def test_the_synced_retro_passes_check(self):
        self.assertEqual(self.code, 0)

    def test_the_timeline_carries_slack_deploys_monitors_and_pull_requests(self):
        rows = self.record()["timeline"]
        self.assertEqual([t["kind"] for t in rows],
                         ["deploy", "report", "alert", "report", "action", "mitigation"])
        stamps = [retro.parse_ts(t["ts"]) for t in rows]
        self.assertEqual(stamps, sorted(stamps))

    def test_a_thread_reply_is_not_its_own_timeline_entry(self):
        self.assertNotIn("1788388140", json.dumps(self.record()["timeline"]))

    def test_an_entry_before_onset_carries_a_phase(self):
        deploy = self.record()["timeline"][0]
        self.assertEqual((deploy["kind"], deploy["phase"]), ("deploy", "before"))

    def test_actions_come_from_the_inventory_with_the_disposition_as_state(self):
        actions = {a["id"]: a for a in self.record()["actions"]}
        self.assertEqual(actions["AI1"]["state"], "todo")
        self.assertEqual(actions["AI2"]["state"], "in-progress")
        self.assertEqual(actions["AI1"]["links"][0]["url"], "https://github.com/Forge-AI/monorepo/pull/22601")

    def test_a_diagnosis_becomes_a_cause_the_action_answers(self):
        record = self.record()
        self.assertEqual([c["id"] for c in record["causes"]], ["C1"])
        self.assertEqual({a["id"]: a["source"] for a in record["actions"]}, {"AI1": "C1", "AI2": "review"})

    def test_a_raw_customer_name_is_replaced_by_its_codename(self):
        blob = (self.root / "retro.json").read_text() + "".join(
            p.read_text() for p in (self.root / "evidence" / "slack").glob("*.json"))
        for raw in ("Northwind Foods", "Northwind", "Contoso EU"):
            self.assertNotIn(raw, blob, f"{raw} survived the scrub")
        self.assertIn("Polar", blob)
        self.assertIn("Mamba", blob)

    def test_the_slack_snapshots_hold_the_thread_and_are_registered(self):
        record = self.record()
        files = sorted(p.name for p in (self.root / "evidence" / "slack").glob("*.json"))
        self.assertEqual(files, ["outage-1788387720.000100.json", "outage-1788389280.000300.json"])
        self.assertEqual([e["file"] for e in record["evidence"]["slack"]],
                         [f"evidence/slack/{name}" for name in files])
        thread = json.loads((self.root / "evidence" / "slack" / files[0]).read_text())
        self.assertEqual(thread["schema"], "ir.slack/1")
        self.assertEqual(len(thread["messages"]), 2)

    def test_a_forbidden_term_refuses_the_push(self):
        code = run(retro_live.sync, args(self.incident, self.docs, no_push=False, forbidden_terms="Polar"))
        self.assertEqual(code, 1)


class HistoryAcrossSyncs(unittest.TestCase):
    """The scrubber renders an entry as it stood at T, so a sync appends only on a change."""

    def setUp(self):
        self.incident, self.docs = incident_dir(), docs_checkout()
        run(retro_live.init, args(self.incident, self.docs))
        self.root = self.docs / retro_live.RETRO_DIR / SLUG
        run(retro_live.sync, args(self.incident, self.docs))

    def actions(self) -> dict:
        return {a["id"]: a for a in json.loads((self.root / "retro.json").read_text())["actions"]}

    def move(self, issue: str, disposition: str):
        state = json.loads((self.incident / "state.json").read_text())
        for entry in state["inventory"]:
            if entry["id"] == issue:
                entry["disposition"] = disposition
        (self.incident / "state.json").write_text(json.dumps(state))

    def test_an_unchanged_disposition_appends_nothing(self):
        before = self.actions()["AI1"]["history"]
        run(retro_live.sync, args(self.incident, self.docs))
        self.assertEqual(self.actions()["AI1"]["history"], before)

    def test_a_changed_disposition_appends_the_new_state(self):
        before = self.actions()["AI1"]["history"]
        self.move("i1", "fixed")
        run(retro_live.sync, args(self.incident, self.docs))
        after = self.actions()["AI1"]["history"]
        self.assertEqual(after[:-1], before)
        self.assertEqual(after[-1]["state"], "done")
        self.assertEqual(self.actions()["AI1"]["state"], "done")

    def test_a_cause_keeps_the_sync_that_first_saw_it(self):
        record = json.loads((self.root / "retro.json").read_text())
        record["causes"][0]["identifiedAt"] = "2026-09-02T15:48:00-07:00"
        (self.root / "retro.json").write_text(json.dumps(record))
        run(retro_live.sync, args(self.incident, self.docs))
        after = json.loads((self.root / "retro.json").read_text())
        self.assertEqual(after["causes"][0]["identifiedAt"], "2026-09-02T15:48:00-07:00")


class Finalize(unittest.TestCase):
    def setUp(self):
        self.incident, self.docs = incident_dir(), docs_checkout()
        run(retro_live.init, args(self.incident, self.docs))
        self.root = self.docs / retro_live.RETRO_DIR / SLUG
        run(retro_live.sync, args(self.incident, self.docs))
        state = json.loads((self.incident / "state.json").read_text())
        state["all_clear_at"] = "2026-09-02T17:30:00-07:00"
        for entry in state["inventory"]:
            entry["disposition"] = "fixed"
        (self.incident / "state.json").write_text(json.dumps(state))
        run(retro_live.sync, args(self.incident, self.docs))
        self.code = run(retro_live.finalize, args(self.incident, self.docs))

    def test_the_finalized_retro_is_a_valid_draft(self):
        self.assertEqual(self.code, 0)
        R = json.loads((self.root / "retro.json").read_text())
        self.assertEqual(R["meta"]["status"], "draft")
        self.assertNotIn("source", R["live"])
        self.assertEqual(R["timestamps"]["resolved"], "2026-09-02T17:30:00-07:00")

    def test_the_all_clear_closes_the_window_and_the_phase(self):
        R = json.loads((self.root / "retro.json").read_text())
        self.assertEqual(R["live"]["phase"], "resolved")
        self.assertEqual(R["windows"][0]["end"], "2026-09-02T17:30:00-07:00")


class Scrubbing(unittest.TestCase):
    def scrub(self):
        return retro_live.scrubber([{"codename": "Polar", "aliases": ["Northwind", "Northwind Foods"]}])

    def test_the_longest_alias_wins(self):
        self.assertEqual(self.scrub()("Northwind Foods called"), "Polar called")

    def test_the_match_ignores_case(self):
        self.assertEqual(self.scrub()("northwind called"), "Polar called")

    def test_a_team_with_no_aliases_leaves_the_text_alone(self):
        blank = retro_live.scrubber([{"codename": "Polar", "aliases": []}])
        self.assertEqual(blank("Northwind called"), "Northwind called")

    def test_a_short_alias_does_not_match_inside_a_longer_word(self):
        """Finding 7: alias Box turned 'Sandbox workers' into 'SandPolar workers'."""
        scrub = retro_live.scrubber([{"codename": "Polar", "aliases": ["Box"]}])
        self.assertEqual(scrub("Sandbox workers failed"), "Sandbox workers failed")
        self.assertEqual(scrub("Box workers failed"), "Polar workers failed")

    def test_an_alias_still_matches_against_punctuation(self):
        scrub = retro_live.scrubber([{"codename": "Polar", "aliases": ["Northwind"]}])
        self.assertEqual(scrub("#northwind-outage"), "#Polar-outage")
        self.assertEqual(scrub("(Northwind)"), "(Polar)")

    def test_an_identifier_carrying_the_alias_is_left_alone(self):
        scrub = retro_live.scrubber([{"codename": "Polar", "aliases": ["Box"]}])
        self.assertEqual(scrub("sandbox_pool and BoxCutter"), "sandbox_pool and BoxCutter")


def git_docs() -> Path:
    """A docs checkout with a bare origin, so a push is a real push."""
    home = Path(tempfile.mkdtemp())
    origin, docs = home / "origin", home / "docs"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    docs.mkdir()
    for argv in (["init", "-q"], ["config", "user.email", "live@test"], ["config", "user.name", "live"],
                 ["remote", "add", "origin", str(origin)]):
        subprocess.run(["git", "-C", str(docs), *argv], check=True)
    (docs / retro_live.RETRO_DIR).mkdir()
    (docs / "index.html").write_text(INDEX_PAGE)
    (docs / retro_live.RETRO_DIR / "index.html").write_text(RETRO_INDEX_PAGE)
    subprocess.run(["git", "-C", str(docs), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(docs), "commit", "-qm", "init"], check=True)
    return docs


def pushed_ref(docs: Path, branch: str) -> str:
    origin = subprocess.run(["git", "-C", str(docs), "ls-remote", "origin", f"refs/heads/{branch}"],
                            capture_output=True, text=True, check=True)
    return origin.stdout.strip()


class PushGate(unittest.TestCase):
    """The gate reads the artifact being pushed, not a scrubbed copy of part of it."""

    def setUp(self):
        self.incident, self.docs = incident_dir(), git_docs()
        self.branch = f"live/{SLUG}"
        run(retro_live.init, args(self.incident, self.docs, forbidden_terms="Northwind|Contoso EU"))
        self.root = self.docs / retro_live.RETRO_DIR / SLUG

    def sync(self, **extra):
        settings = {"no_push": False, "forbidden_terms": "Northwind|Contoso EU"}
        settings.update(extra)
        return run(retro_live.sync, args(self.incident, self.docs, **settings))

    def test_a_clean_sync_reaches_the_branch(self):
        self.assertEqual(self.sync(), 0)
        self.assertTrue(pushed_ref(self.docs, self.branch))

    def test_a_raw_alias_in_the_branch_name_refuses_the_push(self):
        record = json.loads((self.root / "retro.json").read_text())
        record["live"]["source"]["branch"] = "live/NORTHWIND-browser-outage"
        (self.root / "retro.json").write_text(json.dumps(record))
        self.assertEqual(self.sync(), 1)
        self.assertEqual(pushed_ref(self.docs, "live/NORTHWIND-browser-outage"), "")

    def test_a_raw_alias_in_a_staged_file_name_refuses_the_push(self):
        (self.root / retro_live.SLACK_DIR).mkdir(parents=True, exist_ok=True)
        (self.root / retro_live.SLACK_DIR / "NORTHWIND-notes.txt").write_text("nothing to see")
        self.assertEqual(self.sync(), 1)
        self.assertEqual(pushed_ref(self.docs, self.branch), "")

    def test_a_raw_alias_inside_an_html_blob_refuses_the_push(self):
        """Finding 2: the old gate only read .json/.md/.txt/.csv, so html walked through it."""
        (self.root / retro_live.SLACK_DIR).mkdir(parents=True, exist_ok=True)
        (self.root / retro_live.SLACK_DIR / "archived.html").write_text("<p>Northwind reported it</p>")
        self.assertEqual(self.sync(), 1)
        self.assertEqual(pushed_ref(self.docs, self.branch), "")

    def test_no_term_source_at_all_refuses_the_push(self):
        """Finding 3: a missing source only warned, so an unscrubbable retro published."""
        state = json.loads((self.incident / "state.json").read_text())
        state["teams"] = []
        (self.incident / "state.json").write_text(json.dumps(state))
        environ = dict(os.environ)
        os.environ.pop("FORBIDDEN_TERMS", None)
        try:
            self.assertEqual(self.sync(forbidden_terms=None), 1)
        finally:
            os.environ.clear()
            os.environ.update(environ)
        self.assertEqual(pushed_ref(self.docs, self.branch), "")

    def test_the_aliases_alone_can_gate_the_push(self):
        environ = dict(os.environ)
        os.environ.pop("FORBIDDEN_TERMS", None)
        try:
            self.assertEqual(self.sync(forbidden_terms=None), 0)
        finally:
            os.environ.clear()
            os.environ.update(environ)
        self.assertTrue(pushed_ref(self.docs, self.branch))

    def test_the_push_leaves_the_checkout_head_and_index_alone(self):
        head = subprocess.run(["git", "-C", str(self.docs), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout
        self.assertEqual(self.sync(), 0)
        after = subprocess.run(["git", "-C", str(self.docs), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout
        self.assertEqual(head, after)
        staged = subprocess.run(["git", "-C", str(self.docs), "diff", "--cached", "--name-only"],
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(staged.strip(), "")


class SlugDerivation(unittest.TestCase):
    """Finding 1: a lowercase codename let the raw title reach the slug, the paths and the message."""

    def test_the_title_is_scrubbed_before_it_becomes_a_slug(self):
        incident, docs = incident_dir(), docs_checkout()
        state = json.loads((incident / "state.json").read_text())
        state["title"] = "Northwind Foods runs stalled"
        (incident / "state.json").write_text(json.dumps(state))
        self.assertEqual(run(retro_live.init, args(incident, docs)), 0)
        slug = json.loads((incident / "state.json").read_text())["retro_slug"]
        self.assertNotIn("northwind", slug)
        self.assertIn("polar", slug)
        self.assertTrue((docs / retro_live.RETRO_DIR / slug).is_dir())


class SnapshotRebuild(unittest.TestCase):
    """Finding 4: a snapshot captured before an alias was known stayed raw on disk."""

    def setUp(self):
        self.incident, self.docs = incident_dir(), docs_checkout()
        state = json.loads((self.incident / "state.json").read_text())
        state["teams"] = [{"id": "t1", "codename": "Polar", "aliases": []}]
        (self.incident / "state.json").write_text(json.dumps(state))
        run(retro_live.init, args(self.incident, self.docs))
        self.root = self.docs / retro_live.RETRO_DIR / SLUG
        run(retro_live.sync, args(self.incident, self.docs))

    def blob(self) -> str:
        return "".join(p.read_text() for p in (self.root / retro_live.SLACK_DIR).glob("*.json"))

    def test_the_first_sync_keeps_a_name_no_alias_covers(self):
        self.assertIn("Northwind Foods", self.blob())

    def test_a_snapshot_the_log_dropped_does_not_keep_a_name_a_later_alias_covers(self):
        """The finding's compound case: the thread leaves the log, the alias arrives after it, and
        the raw file left on disk blocks every later push."""
        log = (self.incident / "slack-log.jsonl").read_text().splitlines()
        kept = [line for line in log if "Northwind Foods" not in line]
        self.assertEqual(len(kept), len(log) - 1, "the fixture no longer carries the raw-name thread")
        (self.incident / "slack-log.jsonl").write_text("\n".join(kept) + "\n")
        state = json.loads((self.incident / "state.json").read_text())
        state["teams"] = [{"id": "t1", "codename": "Polar", "aliases": ["Northwind Foods", "Northwind"]}]
        (self.incident / "state.json").write_text(json.dumps(state))
        run(retro_live.sync, args(self.incident, self.docs))
        self.assertNotIn("Northwind", self.blob(), "a snapshot the log dropped kept its raw name")

    def test_a_snapshot_no_longer_in_the_log_is_dropped(self):
        orphan = self.root / retro_live.SLACK_DIR / "outage-1700000000.000001.json"
        orphan.write_text('{"schema": "ir.slack/1"}')
        run(retro_live.sync, args(self.incident, self.docs))
        self.assertFalse(orphan.exists(), "the sync rebuilds the snapshot set from the log")


class StateIsReadUnderTheClaim(unittest.TestCase):
    """Finding 5: a sync that waited for the claim published the state it read before waiting."""

    def test_state_is_reread_while_the_claim_is_held(self):
        incident, docs = incident_dir(), docs_checkout()
        run(retro_live.init, args(incident, docs))
        root = docs / retro_live.RETRO_DIR / SLUG
        held, original = [], retro_live.read_state

        def watching(where):
            held.append((root / retro_prose.WRITE_LOCK).exists()
                        and bool((root / retro_prose.WRITE_LOCK).read_text().strip()))
            return original(where)

        retro_live.read_state = watching
        try:
            run(retro_live.sync, args(incident, docs))
        finally:
            retro_live.read_state = original
        self.assertFalse(held[0], "the first read resolves the slug, before the claim")
        self.assertTrue(len(held) > 1 and held[-1], "state was never re-read under the claim")


class LiveBlock(unittest.TestCase):
    def report(self, R, status="ongoing"):
        rep = retro.Report(False)
        retro.check_live(rep, R, status)
        return rep

    def live(self, **extra):
        block = {"updatedAt": "2026-09-02T16:00:00Z", "phase": "investigating", "headline": "Executors cannot reach browsers",
                 "currentState": "One of two issues is open.", "next": "Waiting on the hotfix.",
                 "source": {"repo": "Forge-AI/design-docs", "branch": "live/x"}}
        block.update(extra)
        return {"live": block}

    def test_a_well_formed_block_passes(self):
        self.assertEqual(self.report(self.live()).errors, [])

    def test_an_ongoing_retro_with_no_live_block_is_an_error(self):
        self.assertTrue(self.report({}, "ongoing").errors)

    def test_a_published_retro_may_not_keep_polling_a_branch(self):
        self.assertTrue(self.report(self.live(), "draft").errors)

    def test_an_unknown_phase_is_an_error(self):
        self.assertTrue(self.report(self.live(phase="panicking")).errors)

    def test_a_naive_updated_at_is_an_error(self):
        self.assertTrue(self.report(self.live(updatedAt="2026-09-02T16:00:00")).errors)


class History(unittest.TestCase):
    def report(self, entry, current):
        rep = retro.Report(False)
        retro.check_history(rep, "AI1", entry, "state", retro.ACTION_STATES, current)
        return rep

    def rows(self, *pairs):
        return {"history": [{"ts": ts, "state": state} for ts, state in pairs]}

    def test_an_ordered_history_ending_on_the_current_state_passes(self):
        entry = self.rows(("2026-09-02T14:00:00Z", "todo"), ("2026-09-02T16:00:00Z", "done"))
        self.assertEqual(self.report(entry, "done").errors, [])

    def test_a_history_that_runs_backwards_is_an_error(self):
        entry = self.rows(("2026-09-02T16:00:00Z", "todo"), ("2026-09-02T14:00:00Z", "done"))
        self.assertTrue(self.report(entry, "done").errors)

    def test_a_history_disagreeing_with_the_current_state_is_an_error(self):
        entry = self.rows(("2026-09-02T14:00:00Z", "todo"))
        self.assertTrue(self.report(entry, "done").errors)

    def test_an_unknown_state_is_an_error(self):
        self.assertTrue(self.report(self.rows(("2026-09-02T14:00:00Z", "shipped")), "done").errors)

    def test_an_entry_with_no_history_is_accepted(self):
        self.assertEqual(self.report({}, "todo").errors, [])


class SummaryDeck(unittest.TestCase):
    PANEL = ('<section class="xs-panel" data-kind="impact">'
             '<h3 class="xs-head">Checkouts failed for 212 tenants</h3>'
             '<div class="xs-stats"><div class="xs-stat"><span class="xs-value">94m</span>'
             '<span class="xs-label">(W1)</span></div></div>'
             '<ul class="xs-points"><li>The retry queue replayed every failed checkout.</li>'
             '<li>Orders were delayed, none lost.</li></ul></section>')

    def test_the_parser_separates_the_headline_points_and_stats(self):
        panel = retro.summary_panels(self.PANEL)[0]
        self.assertEqual(panel["heading"], "Checkouts failed for 212 tenants")
        self.assertEqual(panel["headTag"], "h3")
        self.assertEqual(panel["points"], ["The retry queue replayed every failed checkout.",
                                           "Orders were delayed, none lost."])
        self.assertEqual(panel["stats"], ["94m (W1)"])
        self.assertEqual(panel["stray"], [])

    def test_prose_outside_the_points_is_stray(self):
        panel = retro.summary_panels(self.PANEL.replace("</ul>", "</ul><p>A trailing paragraph.</p>"))[0]
        self.assertEqual(panel["stray"], ["A trailing paragraph."])

    def test_markdown_renders_the_points_as_bullets(self):
        lines = retro.summary_markdown(self.PANEL)
        self.assertEqual(lines[0], "### Checkouts failed for 212 tenants")
        self.assertIn("- Orders were delayed, none lost.", lines)

    def check(self, fragment):
        root = Path(tempfile.mkdtemp())
        (root / "summary.html").write_text(fragment)
        rep = retro.Report(True)
        retro.check_summary(rep, root, set(), "draft")
        return rep

    def test_a_headline_over_budget_is_reported(self):
        long = " ".join(["word"] * (retro.XS_HEAD_WORDS + 1))
        rep = self.check(self.PANEL.replace("Checkouts failed for 212 tenants", long))
        self.assertTrue(any("words" in e for e in rep.errors))

    def test_a_panel_with_no_points_is_reported(self):
        rep = self.check(self.PANEL[:self.PANEL.index('<ul class="xs-points">')] + "</section>")
        self.assertTrue(any("xs-points" in e for e in rep.errors))

    def test_a_heading_that_is_not_an_xs_head_is_an_error(self):
        rep = self.check(self.PANEL.replace('<h3 class="xs-head">', "<h2>").replace("</h3>", "</h2>"))
        self.assertTrue(any("xs-head" in e for e in rep.errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
