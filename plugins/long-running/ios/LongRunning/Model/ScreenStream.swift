import Foundation
import IOSKit

enum ScreenStream {
    static func changed(by event: SSEEvent) -> Set<Screen>? {
        guard event.event == "screens",
              let frame = try? JSONDecoder().decode(Frame.self, from: Data(event.data.utf8))
        else { return nil }
        return Set(frame.screens.compactMap(Screen.init(rawValue:)))
    }

    private struct Frame: Decodable {
        let screens: [String]
    }
}
