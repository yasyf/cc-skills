import Foundation
import IOSKit
import Observation

protocol ScreenViewProviding: Sendable {
    func view(_ screen: Screen) async throws -> ScreenView
}

extension HTTPClient: ScreenViewProviding {
    func view(_ screen: Screen) async throws -> ScreenView {
        try await get("api/view/\(screen.rawValue)")
    }
}

@MainActor
@Observable
final class DriveModel {
    private(set) var views: [Screen: ScreenView] = [:]
    private(set) var failures: [Screen: String] = [:]

    private let client: any ScreenViewProviding
    private let streamURL: URL?

    init(client: any ScreenViewProviding, streamURL: URL?) {
        self.client = client
        self.streamURL = streamURL
    }

    convenience init(dashboard: URL) {
        self.init(client: HTTPClient(baseURL: dashboard), streamURL: dashboard.appending(path: "api/stream"))
    }

    func load(_ screens: some Sequence<Screen>) async {
        await withTaskGroup(of: (Screen, Result<ScreenView, Error>).self) { group in
            for screen in screens {
                let client = client
                group.addTask {
                    do {
                        return try await (screen, .success(client.view(screen)))
                    } catch {
                        return (screen, .failure(error))
                    }
                }
            }
            for await (screen, result) in group {
                switch result {
                case let .success(view):
                    views[screen] = view
                    failures[screen] = nil
                case let .failure(error):
                    failures[screen] = Self.message(for: error)
                }
            }
        }
    }

    func follow() async {
        guard let streamURL else { return }
        let connection = await EventSource(url: streamURL).connect()
        for await event in connection.events {
            if let changed = ScreenStream.changed(by: event) {
                await load(changed)
            }
        }
    }

    private static func message(for error: Error) -> String {
        if case let APIError.status(code, _) = error {
            return "The dashboard returned an error (\(code))."
        }
        if error is DecodingError {
            return "The dashboard sent a screen this app can't read yet."
        }
        return "Couldn't reach this drive's dashboard."
    }
}
