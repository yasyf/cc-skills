import CcPresentKit
import CcPresentUI
import IOSKit
import IOSKitUI
import SwiftUI

enum DriveTab: Hashable {
    case screen(Screen)
    case talk
}

struct DriveView: View {
    let drive: Drive

    @State private var model: DriveModel?
    @State private var tab: DriveTab = .screen(.now)
    @State private var focusedRow: [Screen: String] = [:]

    init(drive: Drive) {
        self.drive = drive
        _model = State(initialValue: drive.dashboard.map { DriveModel(dashboard: $0) })
    }

    var body: some View {
        TabView(selection: $tab) {
            ForEach([Screen.now, .status, .asks], id: \.self) { screen in
                screenTab(screen)
            }
            Tab("Talk", systemImage: "bubble.left.and.bubble.right", value: DriveTab.talk) {
                TalkView(drive: drive, open: open)
            }
            ForEach([Screen.plan, .work, .review, .history], id: \.self) { screen in
                screenTab(screen)
            }
        }
        .task {
            guard let model else { return }
            await model.load(Screen.allCases)
            await model.follow()
        }
    }

    private func screenTab(_ screen: Screen) -> some TabContent<DriveTab> {
        Tab(screen.title, systemImage: screen.systemImage, value: DriveTab.screen(screen)) {
            if let model {
                ScreenPage(
                    screen: screen,
                    model: model,
                    focusedRow: focusedRow[screen],
                    open: open
                )
            } else {
                NavigationStack {
                    ContentUnavailableView(
                        "No Dashboard",
                        systemImage: "rectangle.slash",
                        description: Text("This drive has no dashboard running, so only Talk is live.")
                    )
                    .navigationTitle(screen.title)
                    .toolbar { DrivesButton() }
                }
            }
        }
    }

    private func open(_ link: String) {
        guard let target = ScreenLink(link) else { return }
        focusedRow[target.screen] = target.row
        tab = .screen(target.screen)
    }
}

struct TalkView: View {
    let drive: Drive
    let open: (String) -> Void

    @State private var store: BoardStore
    @State private var failure: String?
    private let client: HTTPClient

    init(drive: Drive, open: @escaping (String) -> Void) {
        self.drive = drive
        self.open = open
        let client = HTTPClient(baseURL: drive.machine.baseURL, bearerToken: drive.token)
        self.client = client
        _store = State(initialValue: BoardStore(subject: drive.subject, transport: client))
    }

    var body: some View {
        NavigationStack {
            Group {
                if let failure {
                    ContentUnavailableView("Talk Unavailable", systemImage: "wifi.exclamationmark", description: Text(failure))
                } else {
                    TalkThreadView(
                        store: store,
                        talkId: Drive.talkBlock,
                        client: client,
                        packContext: PackContext(baseURL: drive.machine.baseURL, token: drive.token, subject: drive.subject)
                    )
                }
            }
            .navigationTitle("Talk · \(drive.name)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { DrivesButton() }
        }
        .environment(\.openURL, OpenURLAction { url in
            guard let route = drive.route(url) else { return .systemAction }
            open(route)
            return .handled
        })
        .task {
            do {
                let snapshot = try await client.snapshot(subject: drive.subject)
                store.load(snapshot)
                let connection = await SSEClient(
                    baseURL: drive.machine.baseURL,
                    session: drive.subject,
                    lastEventID: snapshot.seq,
                    bearerToken: drive.token
                ).connect()
                store.connect(connection)
            } catch {
                failure = error.localizedDescription
            }
        }
    }
}
