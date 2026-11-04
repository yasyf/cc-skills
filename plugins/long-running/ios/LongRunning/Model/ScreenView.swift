import Foundation

enum Screen: String, CaseIterable, Decodable, Identifiable, Sendable {
    case now
    case plan
    case status
    case work
    case review
    case history
    case asks

    var id: String {
        rawValue
    }

    var title: String {
        rawValue.capitalized
    }

    var systemImage: String {
        switch self {
        case .now: "dot.radiowaves.left.and.right"
        case .plan: "map"
        case .status: "list.bullet.rectangle"
        case .work: "hammer"
        case .review: "checkmark.seal"
        case .history: "clock.arrow.circlepath"
        case .asks: "questionmark.bubble"
        }
    }
}

struct ScreenView: Decodable, Equatable, Sendable {
    let screen: Screen
    let version: Int
    let head: ScreenHead
    let rows: [ScreenRow]
    let filters: [ScreenFilter]

    private enum CodingKeys: String, CodingKey {
        case screen, version, head, rows, filters
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        screen = try container.decode(Screen.self, forKey: .screen)
        version = try container.decode(Int.self, forKey: .version)
        rows = try container.decode([ScreenRow].self, forKey: .rows)
        filters = try container.decode([ScreenFilter].self, forKey: .filters)
        head = switch screen {
        case .now: try .now(container.decode(NowHead.self, forKey: .head))
        case .plan: try .plan(container.decode(PlanHead.self, forKey: .head))
        case .status: try .status(container.decode(StatusHead.self, forKey: .head))
        case .review: try .review(container.decode(ReviewHead.self, forKey: .head))
        case .history: try .history(container.decode(HistoryHead.self, forKey: .head))
        case .work, .asks: .empty
        }
    }
}

enum ScreenHead: Equatable, Sendable {
    case now(NowHead)
    case plan(PlanHead)
    case status(StatusHead)
    case review(ReviewHead)
    case history(HistoryHead)
    case empty
}

struct NowHead: Decodable, Equatable, Sendable {
    struct Progress: Decodable, Equatable, Sendable {
        struct Stop: Decodable, Equatable, Sendable {
            let state: String
            let label: String
            let why: String?
        }

        let sentence: String?
        let stops: [Stop]?
    }

    let headline: String?
    let because: [String]?
    let progress: Progress?
}

struct PlanHead: Decodable, Equatable, Sendable {
    struct Milestone: Decodable, Equatable, Sendable {
        let state: String
        let label: String
        let ships: String
        let waits: String?
        let since: String?
        let note: String?
        let unblock: String?
    }

    let summary: String?
    let milestones: [Milestone]?
}

struct StatusHead: Decodable, Equatable, Sendable {
    struct Said: Decodable, Equatable, Sendable {
        struct Paragraph: Decodable, Equatable, Sendable {
            let label: String
            let text: String
        }

        let at: String?
        let paragraphs: [Paragraph]
    }

    let said: Said?
}

struct ReviewHead: Decodable, Equatable, Sendable {
    let url: String?
}

struct HistoryHead: Decodable, Equatable, Sendable {
    let summary: [String]?
}

struct ScreenRow: Decodable, Equatable, Identifiable, Sendable {
    let id: String
    let title: String
    let status: String
    let subtitle: String
    let link: String
    let facets: [String: String]
    let records: [ScreenRecord]
}

struct ScreenRecord: Decodable, Equatable, Identifiable, Sendable {
    let seq: Int
    let kind: String?
    let lane: String?
    let at: String?
    let text: String

    var id: Int {
        seq
    }
}

struct ScreenFilter: Decodable, Equatable, Sendable {
    let facet: String
    let values: [String]
}

extension [ScreenRow] {
    func matching(_ selection: [String: String]) -> [ScreenRow] {
        filter { row in selection.allSatisfy { row.facets[$0.key] == $0.value } }
    }
}
