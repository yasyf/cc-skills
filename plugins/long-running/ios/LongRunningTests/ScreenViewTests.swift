import Foundation
@testable import LongRunningApp
import Testing

@Suite("Screen views")
struct ScreenViewTests {
    @Test("every screen's served shape decodes", arguments: Screen.allCases)
    func everyScreenDecodes(_ screen: Screen) throws {
        let view = try Fixtures.view(screen)
        #expect(view.screen == screen)
        #expect(view.version == 7)
        #expect(!view.rows.isEmpty)
    }

    @Test("each screen's head decodes into its own shape")
    func headsDecode() throws {
        guard case let .now(now) = try Fixtures.view(.now).head else {
            Issue.record("expected a now head"); return
        }
        #expect(now.progress?.stops?.map(\.label) == ["Built", "Approved"])
        guard case let .plan(plan) = try Fixtures.view(.plan).head else {
            Issue.record("expected a plan head"); return
        }
        #expect(plan.milestones?.first?.since == "1:38 PM")
        guard case let .status(status) = try Fixtures.view(.status).head else {
            Issue.record("expected a status head"); return
        }
        #expect(status.said?.paragraphs.first?.label == "Main work")
        guard case let .review(review) = try Fixtures.view(.review).head else {
            Issue.record("expected a review head"); return
        }
        #expect(review.url == "http://studio:61118/p/merges-1")
        #expect(try Fixtures.view(.work).head == .empty)
    }

    @Test("a record may carry no kind, lane, or time")
    func sparseRecordDecodes() throws {
        let records = try Fixtures.view(.status).rows[0].records
        #expect(records.map(\.seq) == [101, 102])
        #expect(records[1].lane == nil)
        #expect(records[1].at == nil)
    }

    @Test("an unknown screen name fails at the boundary")
    func unknownScreenFails() {
        let json = #"{"screen":"drives","version":1,"head":{},"rows":[],"filters":[]}"#
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(ScreenView.self, from: Data(json.utf8))
        }
    }

    @Test("rows match every selected facet and nothing else")
    func rowsMatchSelection() throws {
        let rows = try Fixtures.view(.status).rows
        #expect(rows.matching([:]).map(\.id) == ["st-gateway-2", "st-dream-1"])
        #expect(rows.matching(["feature": "dreaming"]).map(\.id) == ["st-dream-1"])
        #expect(rows.matching(["feature": "tooling"]).isEmpty)
        #expect(rows.matching(["feature": "gateway", "state": "x"]).isEmpty)
    }
}

@Suite("Screen links")
struct ScreenLinkTests {
    @Test("a status section link names its screen, section, and row")
    func statusLink() {
        #expect(ScreenLink("#/status/gateway?row=st-gateway-2") == ScreenLink(screen: .status, section: "gateway", row: "st-gateway-2"))
    }

    @Test("a review link's path is its row")
    func reviewLink() {
        #expect(ScreenLink("#/review/47952") == ScreenLink(screen: .review, section: "47952", row: "47952"))
    }

    @Test("a bare screen link and a section without a row carry no row")
    func linksWithoutRow() {
        #expect(ScreenLink("#/plan") == ScreenLink(screen: .plan, section: nil, row: nil))
        #expect(ScreenLink("#/status/gateway") == ScreenLink(screen: .status, section: "gateway", row: nil))
        #expect(ScreenLink("#/asks?row=ask-7") == ScreenLink(screen: .asks, section: nil, row: "ask-7"))
    }

    @Test("anything but a dashboard route is no link")
    func notLinks() {
        #expect(ScreenLink("") == nil)
        #expect(ScreenLink("https://example.com") == nil)
        #expect(ScreenLink("#/drives") == nil)
        #expect(ScreenLink("/status") == nil)
    }
}
