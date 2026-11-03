import Foundation
import IOSKit
import Observation

extension AppIdentity {
    static let longRunning = AppIdentity(
        name: "Long Running",
        storageNamespace: "com.yasyf.long-running",
        bonjourType: "_cc-present._tcp",
        pairScheme: "longrunning"
    )
}

struct TalkSession: Decodable, Equatable, Sendable {
    let sessionId: String
    let slug: String
    let title: String
    let status: String
    let updatedAt: String
    let href: String?

    var dashboard: URL? {
        href.flatMap { $0.isEmpty ? nil : URL(string: $0) }
    }
}

struct Drive: Identifiable, Hashable, Sendable {
    let name: String
    let machine: Machine
    let token: String?
    let slug: String
    let title: String
    let updatedAt: String
    let dashboard: URL?

    var id: String {
        "\(machine.id)/\(name)"
    }

    var talkURL: URL {
        var items = [URLQueryItem(name: "thread", value: "talk")]
        if let token {
            items.append(URLQueryItem(name: "token", value: token))
        }
        return machine.baseURL.appending(path: "p").appending(component: slug).appending(queryItems: items)
    }

    static let talkPrefix = "talk-"

    static func drives(on machine: Machine, token: String?, sessions: [TalkSession]) -> [Drive] {
        sessions
            .filter { $0.sessionId.hasPrefix(talkPrefix) && $0.status == "open" }
            .map {
                Drive(
                    name: String($0.sessionId.dropFirst(talkPrefix.count)),
                    machine: machine,
                    token: token,
                    slug: $0.slug,
                    title: $0.title,
                    updatedAt: $0.updatedAt,
                    dashboard: $0.dashboard
                )
            }
    }
}

protocol TalkSessionsProviding: Sendable {
    func talkSessions() async throws -> [TalkSession]
}

extension HTTPClient: TalkSessionsProviding {
    func talkSessions() async throws -> [TalkSession] {
        try await get("api/sessions")
    }
}

@MainActor
@Observable
final class DrivesModel {
    private(set) var drives: [Drive] = []
    private(set) var unreachable: [Machine] = []
    private(set) var loaded = false

    private let registry: any MachineRegistry
    private let clientFactory: @Sendable (Machine, String?) -> any TalkSessionsProviding

    init(
        registry: any MachineRegistry = KeychainMachineRegistry(identity: .longRunning),
        clientFactory: @escaping @Sendable (Machine, String?) -> any TalkSessionsProviding = { HTTPClient(baseURL: $0.baseURL, bearerToken: $1) }
    ) {
        self.registry = registry
        self.clientFactory = clientFactory
    }

    func refresh() async {
        let machines = (try? registry.load()) ?? []
        let registry = registry
        let clientFactory = clientFactory
        let results = await withTaskGroup(of: (Machine, [Drive]?).self) { group in
            for machine in machines {
                group.addTask {
                    let token = try? registry.token(for: machine.id)
                    guard let sessions = try? await clientFactory(machine, token).talkSessions() else { return (machine, nil) }
                    return (machine, Drive.drives(on: machine, token: token, sessions: sessions))
                }
            }
            return await group.reduce(into: [(Machine, [Drive]?)]()) { $0.append($1) }
        }
        drives = results.compactMap { $0.1 }.flatMap { $0 }.sorted { (parseTimestamp($0.updatedAt) ?? .distantPast) > (parseTimestamp($1.updatedAt) ?? .distantPast) }
        unreachable = results.filter { $0.1 == nil }.map { $0.0 }.sorted { $0.name < $1.name }
        loaded = true
    }
}
