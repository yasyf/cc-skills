import Foundation
import IOSKit
@testable import LongRunningApp
import Testing

@Suite("Screen stream")
struct ScreenStreamTests {
    @Test("a screens frame names the screens to fetch again")
    func screensFrame() {
        let event = SSEEvent(event: "screens", data: #"{"version":9,"screens":["status","asks","talk"]}"#, lastEventID: nil)
        #expect(ScreenStream.changed(by: event) == [.status, .asks])
    }

    @Test("version, build, and malformed frames name nothing")
    func otherFrames() {
        #expect(ScreenStream.changed(by: SSEEvent(event: "version", data: "9", lastEventID: nil)) == nil)
        #expect(ScreenStream.changed(by: SSEEvent(event: "build", data: "abc", lastEventID: nil)) == nil)
        #expect(ScreenStream.changed(by: SSEEvent(event: "screens", data: "not json", lastEventID: nil)) == nil)
    }
}

private func session(_ id: String, status: String = "open", href: String? = "https://studio.ts.net:61118") -> TalkSession {
    TalkSession(sessionId: id, slug: "\(id)-slug", title: id, status: status, updatedAt: "2026-11-03T10:00:00Z", href: href)
}

private func studio() throws -> Machine {
    try Machine(id: "m1", name: "Studio", baseURL: #require(URL(string: "https://studio.ts.net:8765")))
}

@Suite("Drives")
struct DriveListTests {
    @Test("only open Talk boards are drives, named after their session id")
    func talkBoardsAreDrives() throws {
        let drives = try Drive.drives(on: studio(), token: nil, sessions: [
            session("talk-durable-agents"),
            session("board-1"),
            session("talk-old", status: "closed"),
        ])
        #expect(drives.map(\.name) == ["durable-agents"])
        #expect(drives[0].dashboard == URL(string: "https://studio.ts.net:61118"))
    }

    @Test("an empty or missing href leaves the drive with Talk only")
    func emptyHrefHasNoDashboard() throws {
        let drives = try Drive.drives(on: studio(), token: nil, sessions: [session("talk-a", href: ""), session("talk-b", href: nil)])
        #expect(drives.map(\.dashboard) == [nil, nil])
    }

    @Test("the Talk thread opens on the paired Mac, carrying its token when it has one")
    func talkURL() throws {
        let tokenless = try #require(Drive.drives(on: studio(), token: nil, sessions: [session("talk-a")]).first)
        #expect(tokenless.talkURL.absoluteString == "https://studio.ts.net:8765/p/talk-a-slug?thread=talk")
        let tokened = try #require(Drive.drives(on: studio(), token: "t1", sessions: [session("talk-a")]).first)
        #expect(tokened.talkURL.absoluteString == "https://studio.ts.net:8765/p/talk-a-slug?thread=talk&token=t1")
    }
}

private final class MemoryRegistry: MachineRegistry, @unchecked Sendable {
    var machines: [Machine]

    init(_ machines: [Machine]) {
        self.machines = machines
    }

    func load() throws -> [Machine] {
        machines
    }

    func add(_ machine: Machine, token _: String?) throws {
        machines.append(machine)
    }

    func remove(_ machine: Machine) throws {
        machines.removeAll { $0.id == machine.id }
    }

    func token(for _: String) throws -> String? {
        nil
    }
}

private struct ScriptedSessions: TalkSessionsProviding {
    let sessions: [TalkSession]?

    func talkSessions() async throws -> [TalkSession] {
        guard let sessions else { throw URLError(.cannotConnectToHost) }
        return sessions
    }
}

@MainActor
@Test func drivesGatherAcrossMacsNewestFirstAndNameTheSilentOnes() async throws {
    let studio = try Machine(id: "m1", name: "Studio", baseURL: #require(URL(string: "https://studio.ts.net:8765")))
    let laptop = try Machine(id: "m2", name: "Laptop", baseURL: #require(URL(string: "https://laptop.ts.net:8765")))
    let away = try Machine(id: "m3", name: "Away", baseURL: #require(URL(string: "https://away.ts.net:8765")))
    let older = TalkSession(sessionId: "talk-a", slug: "a", title: "a", status: "open", updatedAt: "2026-11-03T09:00:00Z", href: "")
    let newer = TalkSession(sessionId: "talk-b", slug: "b", title: "b", status: "open", updatedAt: "2026-11-03T12:00:00Z", href: "")
    let model = DrivesModel(registry: MemoryRegistry([studio, laptop, away])) { machine, _ in
        switch machine.id {
        case "m1": ScriptedSessions(sessions: [older])
        case "m2": ScriptedSessions(sessions: [newer])
        default: ScriptedSessions(sessions: nil)
        }
    }

    await model.refresh()

    #expect(model.drives.map(\.name) == ["b", "a"])
    #expect(model.unreachable.map(\.name) == ["Away"])
    #expect(model.loaded)
}

private struct ScriptedViews: ScreenViewProviding {
    func view(_ screen: Screen) async throws -> ScreenView {
        if screen == .work {
            throw APIError.status(code: 502, body: "")
        }
        return try Fixtures.view(screen)
    }
}

@MainActor
@Test func driveLoadsEachScreenAndKeepsFailuresPerScreen() async {
    let model = DriveModel(client: ScriptedViews(), streamURL: nil)

    await model.load(Screen.allCases)

    #expect(Set(model.views.keys) == Set(Screen.allCases).subtracting([.work]))
    #expect(model.failures[.work]?.contains("502") == true)
}
