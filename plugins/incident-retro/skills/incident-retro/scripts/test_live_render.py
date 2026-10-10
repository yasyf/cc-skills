#!/usr/bin/env python3
"""Tests that a live poll rebuilds the page instead of layering onto the last render.

  python3 scripts/test_live_render.py

The page is driven through four states of an ongoing incident with the GitHub
contents API stubbed to files the test serves, so the poll runs over real HTTP.
"""
import json, shutil, sys, tempfile, time, unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SHARED = SKILL.parents[2] / "_shared"
sys.path.insert(0, str(SHARED / "py"))
sys.path.insert(0, str(SHARED))
import build
import build_pdf as B

SLUG = "2026-09-18-live-render"
SLACK_URL = "https://example.slack.com/archives/C0TEST0001/p1789762983924459"
POLL_STUB = """
(() => {
  const real = window.fetch;
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    const m = /api\\.github\\.com\\/repos\\/[^/]+\\/[^/]+\\/contents\\/incident-retros\\/[^/]+\\/(.+?)\\?ref=/.exec(url || "");
    if (!m) return real(input, init);
    window.__stubHits = (window.__stubHits || 0) + 1;
    return real("/live/" + decodeURIComponent(m[1]).split("/").map(encodeURIComponent).join("/") + "?t=" + Date.now());
  };
})();
"""
PAGE_STATE = """JSON.stringify({
  sections: Object.fromEntries(["timeline","causes","actions","evidence"]
    .map(id => [id, !document.getElementById(id).hidden])),
  acts: [...document.querySelectorAll(".ir-act")].map(n => n.id).sort(),
  causes: [...document.querySelectorAll("#causeList .ir-cause")].map(n => n.id).sort(),
  decisions: [...document.querySelectorAll("#decisionsHost .ir-decision")].map(n => n.id),
  actionsMounted: !!document.querySelector("#actionsHost .ir-acts"),
  slackMessages: document.querySelectorAll("#evidenceHost .ir-msg").length,
  quoteButtons: document.querySelectorAll("[data-quote]").length,
  scrubMax: Number(document.querySelector("#sbTrack").getAttribute("aria-valuemax")),
  statusPill: (document.querySelector("#docMeta .pill") || {}).textContent,
  channelChip: [...document.querySelectorAll("#docMeta a")].filter(a => a.title === "Incident channel")
    .map(a => [a.textContent, a.getAttribute("href")]),
  liveCard: !document.querySelector("#liveCard").hidden,
  liveBanner: (document.querySelector("#liveCard .lc-banner") || {}).textContent || "",
  polling: window.irLivePolling(),
  stubHits: window.__stubHits || 0,
  duplicateIds: (() => {
    const seen = new Set(), dup = new Set();
    [...document.querySelectorAll("#main [id]")].forEach(n => seen.has(n.id) ? dup.add(n.id) : seen.add(n.id));
    return [...dup];
  })(),
})"""

TIMELINE = [
    {"id": "T1", "ts": "2026-09-18T19:32:00Z", "kind": "deploy", "key": True, "h": "Migration ran", "text": "The migration ran."},
    {"id": "T2", "ts": "2026-09-18T19:36:00Z", "kind": "alert", "key": True, "h": "Alert fired", "text": "The alert fired."},
    {"id": "T3", "ts": "2026-09-18T19:50:00Z", "kind": "report", "key": True, "h": "Report came in", "text": "A report came in."},
]
ACTIONS = [
    {"id": "AI1", "t": "Roll the workers forward", "h": "Worker roll", "owner": "Ada", "state": "in-progress"},
    {"id": "AI2", "t": "Page on database errors", "h": "Database paging", "owner": "Grace", "state": "todo"},
]
CAUSES = [
    {"id": "C1", "kind": "root", "t": "A column rename broke old workers", "h": "Breaking rename",
     "text": "The rename landed first.", "identifiedAt": "2026-09-18T19:58:00Z"},
    {"id": "C2", "kind": "trigger", "t": "A partial release", "h": "Partial release",
     "text": "One target rolled.", "identifiedAt": "2026-09-18T20:02:00Z"},
]
DECISIONS = [
    {"id": "D1", "t": "Roll forward rather than revert", "h": "Roll forward", "who": "Ada",
     "when": "2026-09-18T20:05:00Z", "why": "Reverting risked data loss."},
]
SLACK_SNAPSHOT = {
    "channel_id": "C0TEST0001", "channel_name": "incident", "permalink": SLACK_URL,
    "fetchedAt": "2026-09-18T20:10:00Z",
    "messages": [{"ts": "1789762983.924459", "datetime": "2026-09-18T19:50:00Z",
                  "user_id": "U1", "user_name": "Ada", "text": "Run creation is failing."}],
}

LIVE_VIEW = """JSON.stringify({
  view: document.documentElement.dataset.view || "retro",
  switchShown: !document.getElementById("viewSwitch").hidden,
  overviewShown: getComputedStyle(document.getElementById("overview")).display !== "none",
  chapters: [...document.querySelectorAll("#lvChs .lv-chap")].map(n => [n.querySelector(".lv-chh b").textContent,
    n.classList.contains("open"), [...n.querySelectorAll(".lv-row")].map(r => r.dataset.kind)]),
  feed: [...document.querySelectorAll("#lvChs .lv-row")].map(n => n.dataset.kind),
  slackCards: [...document.querySelectorAll("#lvChs .lv-slack .msg")].map(n => n.textContent),
  monitor: [...document.querySelectorAll("#lvChs .lv-mon .pill")].map(n => n.textContent),
  charts: document.querySelectorAll("#lvChs .lv-chart").length,
  prCards: [...document.querySelectorAll("#lvChs .lv-pr")].map(a => a.dataset.lvpr),
  cards: [...document.querySelectorAll("#lvNow .lv-card2")].map(n => [n.dataset.role,
    (n.querySelector(".lv-pulse, .pill") || {}).textContent, n.querySelector(".lv-step").textContent]),
  bands: [...document.querySelectorAll("#lvStrip .lv-band")].map(n => n.textContent),
  toasts: [...document.querySelectorAll("#lvToasts .lv-toast")].map(t => t.textContent),
  flashed: [...document.querySelectorAll("#lvChs .lv-row.lv-new")].map(n => n.dataset.kind),
})"""
OPEN_ROWS = "(document.querySelectorAll('#lvChs .lv-row:not(.open)').forEach(r => r.click()), 1)"
MONITOR_URL = "https://app.datadoghq.com/monitors/4242"
PR_URL = "https://github.com/Forge-AI/monorepo/pull/34474"
MONITOR_SNAPSHOT = {
    "schema": "ir.monitor/1", "id": 4242, "url": MONITOR_URL, "site": "datadoghq.com",
    "fetchedAt": "2026-09-18T19:52:00Z", "name": "Run creation errors", "type": "query alert",
    "overallState": "Alert", "thresholds": {"critical": 5, "warning": None},
    "series": {"t": [1789760000, 1789760600, 1789761200], "series": [{"label": "team:polar", "v": [0, 3, 9]}]},
}


def record(**over):
    base = {
        "meta": {"title": "A live incident", "subtitle": "A fixture for the live poll.", "slug": SLUG,
                 "status": "ongoing", "date": "2026-09-18", "timezone": "UTC", "teams": ["Polar"],
                 "authors": ["Ada"], "tags": ["live", "render"],
                 "incident": {"channel": {"name": "inc-live-render", "id": "C0TEST0001",
                                          "permalink": "https://example.slack.com/archives/C0TEST0001"}}},
        "live": {"updatedAt": "2026-09-18T19:40:00Z", "phase": "detected",
                 "headline": "Run creation is failing", "currentState": "Rolling the workers.",
                 "next": "Confirm recovery.",
                 "source": {"repo": "Forge-AI/design-docs", "branch": "live/" + SLUG}},
        "summary": {}, "timestamps": {"onset": "2026-09-18T19:32:00Z"}, "windows": [], "timeline": [],
        "impact": {}, "causes": [], "resolution": {}, "detection": {}, "decisions": [], "hypotheses": [],
        "actions": [], "lessons": {}, "evidence": {}, "notes": [], "footnotes": [],
    }
    base.update(over)
    return base


class LivePollRebuildsThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.chrome_path = B.require_chrome()
        except Exception as e:
            raise unittest.SkipTest(f"headless Chrome is not available: {e}")
        cls.root = Path(tempfile.mkdtemp())
        (cls.root / "evidence").mkdir()
        (cls.root / "index.html").write_text(build.render_host(SKILL / "templates" / "src" / "incident-retro.html"))
        cls.publish(record())
        (cls.root / "retro.json").write_text(json.dumps(record(), indent=2))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def publish(cls, rec, with_slack=False):
        live = cls.root / "live"
        live.mkdir(exist_ok=True)
        (live / "retro.json").write_text(json.dumps(rec, indent=2))
        if with_slack:
            slack = live / "evidence" / "slack"
            slack.mkdir(parents=True, exist_ok=True)
            (slack / "thread.json").write_text(json.dumps(SLACK_SNAPSHOT))
            datadog = live / "evidence" / "datadog"
            datadog.mkdir(parents=True, exist_ok=True)
            (datadog / "monitor-4242.json").write_text(json.dumps(MONITOR_SNAPSHOT))

    def open_page(self, chrome, base):
        target = chrome.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = chrome.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        for domain in ("Page", "Runtime", "Log"):
            chrome.call(domain + ".enable", session=session)
        chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": POLL_STUB}, session=session)
        chrome.call("Emulation.setDeviceMetricsOverride",
                    {"width": 1440, "height": 980, "deviceScaleFactor": 1, "mobile": False}, session=session)
        chrome.call("Page.navigate", {"url": base + "/index.html"}, session=session)
        self.assertEqual(B.settle(chrome, session, 40).get("ready"), "1", "the empty shell never became ready")
        return session

    def live_view(self, chrome, session, poll=False):
        if poll:
            B.evaluate(chrome, session, "(window.irLiveTick(),1)")
            time.sleep(1.5)
        return json.loads(B.evaluate(chrome, session, LIVE_VIEW))

    def test_the_live_view_leads_while_the_incident_runs_and_toasts_each_new_entry(self):
        chrome = B.Chrome(self.chrome_path)
        server, base = B.serve(self.root)
        try:
            self.publish(record(timeline=TIMELINE[:2]))
            session = self.open_page(chrome, base)
            time.sleep(1.5)
            first = self.live_view(chrome, session)
            self.assertEqual(first["view"], "live", "an ongoing incident did not open on the live view")
            self.assertTrue(first["switchShown"])
            self.assertFalse(first["overviewShown"], "the live view left the retro sections showing")
            self.assertEqual(first["feed"], ["alert", "deploy"], "the feed is not newest first")
            self.assertEqual(first["chapters"], [["Before detection", True, ["alert", "deploy"]]],
                             "without chapters from the keeper the page did not group the timeline itself")
            self.assertEqual(first["toasts"], [], "the entries already there on open were toasted")

            cited = [dict(e, refs=[PR_URL]) if e["id"] == "T1" else
                     dict(e, refs=[MONITOR_URL]) if e["id"] == "T2" else dict(e, refs=[SLACK_URL]) for e in TIMELINE]
            self.publish(record(timeline=cited, evidence={
                "slack": [{"url": SLACK_URL, "file": "evidence/slack/thread.json"}],
                "monitors": [{"id": 4242, "url": MONITOR_URL, "file": "evidence/datadog/monitor-4242.json"}]}),
                with_slack=True)
            grown = self.live_view(chrome, session, poll=True)
            self.assertEqual(grown["feed"], ["report", "alert", "deploy"])
            self.assertEqual(grown["slackCards"], [], "an update showed its previews before it was opened")
            B.evaluate(chrome, session, OPEN_ROWS)
            grown = {**self.live_view(chrome, session), "toasts": grown["toasts"], "flashed": grown["flashed"]}
            self.assertEqual(grown["slackCards"], ["Run creation is failing."], "the report did not show its message")
            self.assertEqual(grown["monitor"], ["Alert"], "the monitor preview lost its state")
            self.assertEqual(grown["charts"], 1, "the monitor preview drew no chart slot")
            self.assertEqual(grown["prCards"], ["Forge-AI/monorepo#34474"])
            self.assertEqual(len(grown["toasts"]), 1, "the new entry was not toasted exactly once")
            self.assertIn("Report came in", grown["toasts"][0])
            self.assertEqual(grown["flashed"], ["report"], "the new entry was not highlighted in the feed")

            B.evaluate(chrome, session, "(document.querySelector('#viewSwitch [data-view=retro]').click(),1)")
            retro_view = self.live_view(chrome, session)
            self.assertEqual(retro_view["view"], "retro")
            self.assertTrue(retro_view["overviewShown"], "the full retro stayed hidden after the switch")
            B.evaluate(chrome, session, "(document.querySelector('#viewSwitch [data-view=live]').click(),1)")
            self.assertEqual(self.live_view(chrome, session)["view"], "live")

            final = record(timeline=cited)
            final["meta"]["status"] = "draft"
            final.pop("live")
            self.publish(final)
            closed = self.live_view(chrome, session, poll=True)
            self.assertEqual(closed["view"], "retro", "the live view outlived the incident")
            self.assertFalse(closed["switchShown"])
            self.assertEqual(B.page_errors(chrome), [], "the page logged an error in the live view")
        finally:
            server.shutdown()
            server.server_close()
            chrome.close()

    def test_the_keepers_chapters_sessions_and_monitor_lead_the_live_view(self):
        chrome = B.Chrome(self.chrome_path)
        server, base = B.serve(self.root)
        try:
            now = time.time()
            stamp = lambda ago: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ago))
            timeline = [dict(e, ts=stamp(ago)) for e, ago in zip(TIMELINE, (1800, 1500, 300))]
            timeline[1]["refs"] = [MONITOR_URL]
            rec = record(timeline=timeline, timestamps={"onset": stamp(1800)}, evidence={
                "monitors": [{"id": 4242, "url": MONITOR_URL, "file": "evidence/datadog/monitor-4242.json"}]})
            rec["live"].update(updatedAt=stamp(10), chapters=[
                {"start": timeline[0]["ts"], "title": "Migration breaks runs", "summary": "The migration ran and the alert fired."},
                {"start": timeline[2]["ts"], "title": "Reports come in", "summary": "A report came in."}],
                sessions=[{"role": "fixer", "state": "running", "phase": "fixing", "step": "Rolling the workers",
                           "beatAt": stamp(20), "attempt": 1},
                          {"role": "ic", "state": "running", "step": "Updating #outage", "beatAt": stamp(600)}])
            self.publish(rec, with_slack=True)
            session = self.open_page(chrome, base)
            time.sleep(1.5)
            view = self.live_view(chrome, session)
            self.assertEqual(view["chapters"], [["Reports come in", True, ["report"]],
                                                ["Migration breaks runs", False, []]],
                             "the keeper's chapters did not group the rows, newest first with only the current one open")
            self.assertEqual(view["bands"], ["Migration breaks runs", "Reports come in"])
            self.assertEqual(view["cards"], [["fixer", "Working", "Rolling the workers"],
                                             ["ic", "Not responding", "Updating #outage"],
                                             ["monitor", "Alert", "Run creation errors"]])
            B.evaluate(chrome, session, "(document.querySelector('#lvChs .lv-chap:last-child .lv-chh').click(), 1)")
            B.evaluate(chrome, session, OPEN_ROWS)
            opened = self.live_view(chrome, session)
            self.assertEqual(opened["chapters"][1], ["Migration breaks runs", True, ["alert", "deploy"]])
            self.assertEqual(opened["monitor"], ["Alert"])
            self.assertEqual(B.page_errors(chrome), [], "the page logged an error in the live view")
        finally:
            server.shutdown()
            server.server_close()
            chrome.close()

    def page_state(self, chrome, session):
        return json.loads(B.evaluate(chrome, session, PAGE_STATE))

    def poll(self, chrome, session):
        B.evaluate(chrome, session, "(window.irLiveTick(),1)")
        time.sleep(1.5)
        return self.page_state(chrome, session)

    def test_four_states(self):
        chrome = B.Chrome(self.chrome_path)
        server, base = B.serve(self.root)
        try:
            target = chrome.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            session = chrome.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            for domain in ("Page", "Runtime", "Log"):
                chrome.call(domain + ".enable", session=session)
            chrome.call("Page.addScriptToEvaluateOnNewDocument", {"source": POLL_STUB}, session=session)
            chrome.call("Emulation.setDeviceMetricsOverride",
                        {"width": 1440, "height": 980, "deviceScaleFactor": 1, "mobile": False}, session=session)
            chrome.call("Page.navigate", {"url": base + "/index.html"}, session=session)
            self.assertEqual(B.settle(chrome, session, 40).get("ready"), "1", "the empty shell never became ready")

            shell = self.page_state(chrome, session)
            self.assertFalse(any(shell["sections"].values()), "empty registers left their sections visible")
            self.assertTrue(shell["liveCard"], "an ongoing retro showed no live card")
            self.assertEqual(shell["channelChip"], [["#inc-live-render", "https://example.slack.com/archives/C0TEST0001"]])
            self.assertRegex(shell["liveBanner"], r"^Live incident in progressDetectedupdated ",
                             "the live banner lost its phase or its age")

            self.publish(record(timeline=TIMELINE, actions=ACTIONS))
            filled = self.poll(chrome, session)
            self.assertTrue(filled["sections"]["timeline"], "the timeline section stayed hidden after data arrived")
            self.assertTrue(filled["sections"]["actions"], "the actions section stayed hidden after data arrived")
            self.assertTrue(filled["actionsMounted"], "the action list never mounted on a later poll")
            self.assertEqual(filled["acts"], ["act-AI1", "act-AI2"])
            self.assertGreater(filled["scrubMax"], shell["scrubMax"], "the scrubber kept its startup bounds")

            cited = [dict(e, refs=[SLACK_URL]) if e["id"] == "T3" else e for e in TIMELINE]
            whole = record(timeline=cited, actions=ACTIONS, causes=CAUSES, decisions=DECISIONS,
                           evidence={"slack": [{"url": SLACK_URL, "file": "evidence/slack/thread.json"}]})
            self.publish(whole, with_slack=True)
            grown = self.poll(chrome, session)
            self.assertEqual(grown["causes"], ["C1", "C2"])
            self.assertEqual(grown["decisions"], ["D1"])
            self.assertTrue(grown["sections"]["evidence"], "polled evidence never un-hid its section")
            self.assertTrue(grown["slackMessages"], "the snapshot was never fetched from the live ref")
            self.assertTrue(grown["quoteButtons"], "a polled Slack citation produced no quote button")
            self.assertGreater(grown["stubHits"], filled["stubHits"], "evidence bypassed the live contents API")

            repolled = self.poll(chrome, session)
            self.assertEqual(repolled["decisions"], ["D1"], "re-polling the same record duplicated the decisions")
            self.assertEqual(repolled["duplicateIds"], [], "re-polling duplicated element ids")

            final = record(timeline=cited, actions=ACTIONS, causes=CAUSES, decisions=DECISIONS)
            final["meta"]["status"] = "draft"
            final.pop("live")
            self.publish(final)
            stopped = self.poll(chrome, session)
            self.assertFalse(stopped["polling"], "the poll timers outlived the ongoing status")
            self.assertEqual(stopped["statusPill"], "Draft retro", "the last record never reached the page")
            self.assertFalse(stopped["liveCard"], "the live card survived the end of the incident")

            quiet = B.evaluate(chrome, session, "window.__stubHits")
            time.sleep(2.0)
            self.assertEqual(B.evaluate(chrome, session, "window.__stubHits"), quiet,
                             "a request went out after the poll should have stopped")
            self.assertEqual(B.page_errors(chrome), [], "the page logged an error while polling")
        finally:
            server.shutdown()
            server.server_close()
            chrome.close()


if __name__ == "__main__":
    unittest.main()
