import IOSKit
import IOSKitUI
import IOSKitWeb
import SwiftUI

struct HeadView: View {
    let head: ScreenHead

    var body: some View {
        switch head {
        case let .now(now):
            NowHeadView(head: now)
        case let .plan(plan):
            PlanHeadView(head: plan)
        case let .status(status):
            if let said = status.said {
                StatusHeadView(said: said)
            }
        case let .review(review):
            if let url = review.url.flatMap { URL(string: $0) } {
                ReviewHeadView(board: url)
            }
        case let .history(history):
            if let summary = history.summary, !summary.isEmpty {
                Lines(lines: summary)
            }
        case .empty:
            EmptyView()
        }
    }
}

private struct NowHeadView: View {
    let head: NowHead

    var body: some View {
        VStack(alignment: .leading, spacing: Metrics.space3) {
            if let headline = head.headline {
                Text(headline)
                    .voice(.display, size: 22, weight: .semibold)
                    .foregroundStyle(Palette.ink)
            }
            if let because = head.because, !because.isEmpty {
                Lines(lines: because)
            }
            if let progress = head.progress {
                VStack(alignment: .leading, spacing: Metrics.space2) {
                    if let sentence = progress.sentence {
                        Text(sentence)
                            .voice(.prose, size: 14)
                            .foregroundStyle(Palette.ink)
                    }
                    ForEach(Array((progress.stops ?? []).enumerated()), id: \.offset) { _, stop in
                        HStack(alignment: .firstTextBaseline, spacing: Metrics.space2) {
                            Text(stop.label)
                                .voice(.stamp, size: 11, weight: .semibold)
                                .foregroundStyle(MilestoneState.tone(stop.state))
                            if let why = stop.why, !why.isEmpty {
                                Text(why)
                                    .voice(.prose, size: 13)
                                    .foregroundStyle(Palette.muted)
                            }
                        }
                    }
                }
                .padding(Metrics.space3)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Palette.chipBg, in: RoundedRectangle(cornerRadius: Metrics.radiusLg))
            }
        }
    }
}

private struct PlanHeadView: View {
    let head: PlanHead

    var body: some View {
        VStack(alignment: .leading, spacing: Metrics.space3) {
            if let summary = head.summary {
                Text(summary)
                    .voice(.display, size: 20, weight: .semibold)
                    .foregroundStyle(Palette.ink)
            }
            ForEach(Array((head.milestones ?? []).enumerated()), id: \.offset) { _, milestone in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: Metrics.space2) {
                        Text(milestone.label)
                            .voice(.stamp, size: 11, weight: .semibold)
                            .foregroundStyle(MilestoneState.tone(milestone.state))
                        if let since = milestone.since, !since.isEmpty {
                            Text("since \(since)")
                                .voice(.mono, size: 11)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                    Text(milestone.ships)
                        .voice(.prose, size: 14)
                        .foregroundStyle(Palette.ink)
                    ForEach([milestone.waits, milestone.unblock, milestone.note].compactMap { $0 }.filter { !$0.isEmpty }, id: \.self) { line in
                        Text(line)
                            .voice(.prose, size: 13)
                            .foregroundStyle(Palette.muted)
                    }
                }
                .padding(Metrics.space3)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Palette.chipBg, in: RoundedRectangle(cornerRadius: Metrics.radiusLg))
            }
        }
    }
}

private struct StatusHeadView: View {
    let said: StatusHead.Said

    var body: some View {
        VStack(alignment: .leading, spacing: Metrics.space2) {
            ForEach(Array(said.paragraphs.enumerated()), id: \.offset) { _, paragraph in
                VStack(alignment: .leading, spacing: 2) {
                    Text(paragraph.label)
                        .voice(.stamp, size: 11, weight: .semibold)
                        .foregroundStyle(Palette.muted)
                    Text(paragraph.text)
                        .voice(.prose, size: 14)
                        .foregroundStyle(Palette.ink)
                }
            }
            if let at = said.at {
                Text("Said \(relativeTimestamp(at))")
                    .voice(.mono, size: 11)
                    .foregroundStyle(Palette.muted)
            }
        }
    }
}

private struct ReviewHeadView: View {
    let board: URL

    @State private var showing = false

    var body: some View {
        Button("Open the merge board") { showing = true }
            .buttonStyle(PrimaryButtonStyle())
            .sheet(isPresented: $showing) {
                NavigationStack {
                    WebHost(url: board)
                        .navigationTitle("Merge board")
                        .navigationBarTitleDisplayMode(.inline)
                        .toolbar {
                            ToolbarItem(placement: .confirmationAction) {
                                Button("Done") { showing = false }
                            }
                        }
                }
            }
    }
}

private struct Lines: View {
    let lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: Metrics.space1) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                HStack(alignment: .firstTextBaseline, spacing: Metrics.space2) {
                    Text("·")
                    Text(line)
                }
                .voice(.prose, size: 14)
                .foregroundStyle(Palette.ink)
            }
        }
    }
}

enum MilestoneState {
    static func tone(_ state: String) -> Color {
        switch state {
        case "live", "done": Palette.approve
        case "blocked", "stuck": Palette.reject
        case "waiting", "you": Palette.warn
        default: Palette.accentInk
        }
    }
}
