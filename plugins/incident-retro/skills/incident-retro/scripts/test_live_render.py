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
  liveCard: !document.querySelector("#liveCard").hidden,
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


def record(**over):
    base = {
        "meta": {"title": "A live incident", "subtitle": "A fixture for the live poll.", "slug": SLUG,
                 "status": "ongoing", "date": "2026-09-18", "timezone": "UTC", "teams": ["Polar"],
                 "authors": ["Ada"], "tags": ["live", "render"]},
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
