import Foundation
@testable import LongRunningApp

enum Fixtures {
    static let now = #"""
    {"screen":"now","version":7,
     "head":{"headline":"The nightly build is live; one merge waits on your tap.",
             "because":["The release went out at the usual time.","One pull request changed after you approved it."],
             "progress":{"sentence":"Two of three gates are clear.","stops":[{"state":"live","label":"Built","why":""},{"state":"waiting","label":"Approved","why":"The head moved."}]}},
     "rows":[{"id":"r1","title":"Readers keep their place","status":"needs_you","subtitle":"Waits on your tap for one pull request.","link":"#/review?row=r1","facets":{"kind":"merge"},"records":[]},
             {"id":"r2","title":"Which build should testers get?","status":"needs_you","subtitle":"Two options on the board.","link":"#/asks?row=ask-7","facets":{"kind":"question"},"records":[]}],
     "filters":[{"facet":"kind","values":["merge","question"]}]}
    """#

    static let plan = #"""
    {"screen":"plan","version":7,
     "head":{"summary":"Three of six milestones are live.",
             "milestones":[{"state":"live","label":"Live","ships":"Chats keep working during rollout.","waits":"","since":"1:38 PM","card":"","note":"","unblock":""}]},
     "rows":[{"id":"plan-0","title":"Code kept current","status":"waiting","subtitle":"Waits for a worker","link":"#/plan?row=plan-0","facets":{"state":"Waits for a worker"},"records":[]}],
     "filters":[{"facet":"state","values":["Waits for a worker","Done"]}]}
    """#

    static let status = #"""
    {"screen":"status","version":7,
     "head":{"said":{"at":"2026-11-03T09:36:30Z","paragraphs":[{"label":"Main work","text":"Two items are overdue."}]}},
     "rows":[{"id":"st-gateway-2","title":"Gateway retries","status":"doing","subtitle":"Fix in review.","link":"#/status/gateway?row=st-gateway-2","facets":{"feature":"gateway"},
              "records":[{"seq":101,"kind":"report","lane":"gateway-1","at":"2026-11-03T11:13:49.536Z","text":"Opened the retry fix."},{"seq":102,"kind":null,"lane":null,"at":null,"text":"A record with no lane."}]},
             {"id":"st-dream-1","title":"Dream nights","status":"done","subtitle":"Live.","link":"#/status/dreaming?row=st-dream-1","facets":{"feature":"dreaming"},"records":[]}],
     "filters":[{"facet":"feature","values":["gateway","dreaming"]}]}
    """#

    static let work = #"""
    {"screen":"work","version":7,"head":{},
     "rows":[{"id":"w1","title":"Nightly passes keep running","status":"doing","subtitle":"Retry fix lands next.","link":"#/work?row=w1","facets":{"state":"Landing"},"records":[]}],
     "filters":[{"facet":"state","values":["Landing"]}]}
    """#

    static let review = #"""
    {"screen":"review","version":7,"head":{"url":"http://studio:61118/p/merges-1"},
     "rows":[{"id":"47952","title":"Readers follow a chat by position","status":"needs_you","subtitle":"Your approval.","link":"#/review/47952","facets":{"group":"Readers"},"records":[]}],
     "filters":[{"facet":"group","values":["Readers"]}]}
    """#

    static let history = #"""
    {"screen":"history","version":7,"head":{"summary":["Releases apply in under five minutes."]},
     "rows":[{"id":"h-0","title":"Dream nights stop retrying","status":"info","subtitle":"A refusal now ends the night.","link":"#/history?row=h-0","facets":{"kind":"landed"},"records":[]}],
     "filters":[{"facet":"kind","values":["landed","incident"]}]}
    """#

    static let asks = #"""
    {"screen":"asks","version":7,"head":{},
     "rows":[{"id":"ask-7","title":"Can the phone pair from a link?","status":"doing","subtitle":"Answered","link":"#/asks?row=ask-7","facets":{"state":"answered","record":"375365"},"records":[]}],
     "filters":[{"facet":"state","values":["answered","applied"]}]}
    """#

    static let all: [Screen: String] = [
        .now: now, .plan: plan, .status: status, .work: work, .review: review, .history: history, .asks: asks,
    ]

    static func view(_ screen: Screen) throws -> ScreenView {
        try JSONDecoder().decode(ScreenView.self, from: Data(all[screen, default: ""].utf8))
    }
}
