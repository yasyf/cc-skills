import Foundation
import IOSKitUI
@testable import LongRunningApp
import SwiftUI
import Testing
import UIKit

@Suite("Screen snapshots")
@MainActor
struct ScreenSnapshotTests {
    enum Appearance: String, CaseIterable, Sendable {
        case light
        case dark
    }

    @Test("every screen's head and rows render", arguments: Screen.allCases, Appearance.allCases)
    func screenRenders(_ screen: Screen, _ appearance: Appearance) throws {
        let view = try Fixtures.view(screen)
        let content = VStack(alignment: .leading, spacing: Metrics.space3) {
            HeadView(head: view.head)
            ForEach(Array(view.rows.enumerated()), id: \.offset) { index, row in
                RowView(screen: screen, row: row, expanded: index == 0, focused: false, toggle: {}, open: { _ in })
            }
        }
        .padding(Metrics.space4)
        .frame(width: 390)
        .background(Palette.cardLift)
        .environment(\.colorScheme, appearance == .dark ? .dark : .light)
        let renderer = ImageRenderer(content: content)
        renderer.scale = 3
        let image = try #require(renderer.uiImage)
        #expect(image.size.height > 0)
        if let directory = ProcessInfo.processInfo.environment["SNAPSHOT_DIR"], let png = image.pngData() {
            try png.write(to: URL(filePath: directory).appending(path: "\(screen.rawValue)-\(appearance.rawValue).png"))
        }
    }
}
