import Foundation

struct ScreenLink: Equatable, Sendable {
    let screen: Screen
    let section: String?
    let row: String?
}

extension ScreenLink {
    init?(_ link: String) {
        guard link.hasPrefix("#/"),
              let components = URLComponents(string: "x:" + link.dropFirst(2))
        else { return nil }
        let parts = components.path.split(separator: "/").map(String.init)
        guard let first = parts.first, let screen = Screen(rawValue: first) else { return nil }
        self.screen = screen
        section = parts.dropFirst().first
        row = components.queryItems?.first { $0.name == "row" }?.value ?? (screen == .review ? section : nil)
    }
}
