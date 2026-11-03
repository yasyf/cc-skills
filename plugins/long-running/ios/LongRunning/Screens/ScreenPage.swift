import IOSKit
import IOSKitUI
import SwiftUI

struct ScreenPage: View {
    let screen: Screen
    let model: DriveModel
    let focusedRow: String?
    let open: (String) -> Void

    @State private var selection: [String: String] = [:]
    @State private var expanded: Set<String> = []

    var body: some View {
        NavigationStack {
            Group {
                if let view = model.views[screen] {
                    page(view)
                } else if let failure = model.failures[screen] {
                    ContentUnavailableView {
                        Label("Can't Load \(screen.title)", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(failure)
                    } actions: {
                        Button("Try Again") { Task { await model.load([screen]) } }
                    }
                } else {
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .navigationTitle(screen.title)
            .toolbar { DrivesButton() }
            .refreshable { await model.load([screen]) }
        }
    }

    private func page(_ view: ScreenView) -> some View {
        let rows = view.rows.matching(selection)
        return ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: Metrics.space3) {
                    HeadView(head: view.head)
                    ForEach(view.filters.filter { $0.values.count > 1 }, id: \.facet) { filter in
                        FilterBar(filter: filter, selection: $selection)
                    }
                    if rows.isEmpty {
                        Text(view.rows.isEmpty ? "Nothing here right now." : "No rows match these filters.")
                            .voice(.prose, size: 14)
                            .foregroundStyle(Palette.muted)
                            .padding(.vertical, Metrics.space4)
                    }
                    ForEach(rows) { row in
                        RowView(
                            screen: screen,
                            row: row,
                            expanded: expanded.contains(row.id),
                            focused: row.id == focusedRow,
                            toggle: { toggle(row.id) },
                            open: open
                        )
                        .id(row.id)
                    }
                }
                .padding(Metrics.space4)
            }
            .onAppear { reveal(focusedRow, proxy) }
            .onChange(of: focusedRow) { _, row in reveal(row, proxy) }
        }
    }

    private func toggle(_ id: String) {
        if expanded.contains(id) {
            expanded.remove(id)
        } else {
            expanded.insert(id)
        }
    }

    private func reveal(_ row: String?, _ proxy: ScrollViewProxy) {
        guard let row else { return }
        selection = [:]
        expanded.insert(row)
        withAnimation { proxy.scrollTo(row, anchor: .top) }
    }
}

private struct FilterBar: View {
    let filter: ScreenFilter
    @Binding var selection: [String: String]

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: Metrics.space1) {
                chip(nil)
                ForEach(filter.values.filter { !$0.isEmpty }, id: \.self) { value in
                    chip(value)
                }
            }
        }
        .scrollIndicators(.hidden)
    }

    private func chip(_ value: String?) -> some View {
        let active = selection[filter.facet] == value
        return Button {
            selection[filter.facet] = value
        } label: {
            Text(value ?? "All \(filter.facet)s")
                .voice(.prose, size: 13, weight: active ? .semibold : .regular)
                .foregroundStyle(active ? Palette.accentFg : Palette.ink)
                .padding(.horizontal, Metrics.space3)
                .padding(.vertical, 6)
                .background(active ? Palette.accentInk : Palette.chipBg, in: Capsule())
        }
        .buttonStyle(.plain)
    }
}

struct RowView: View {
    let screen: Screen
    let row: ScreenRow
    let expanded: Bool
    let focused: Bool
    let toggle: () -> Void
    let open: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Metrics.space2) {
            Button(action: toggle) {
                HStack(alignment: .firstTextBaseline, spacing: Metrics.space2) {
                    Circle()
                        .fill(Self.tone(row.status))
                        .frame(width: 8, height: 8)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(row.title)
                            .voice(.prose, size: 15, weight: .semibold)
                            .foregroundStyle(Palette.ink)
                            .multilineTextAlignment(.leading)
                        if !row.subtitle.isEmpty {
                            Text(row.subtitle)
                                .voice(.prose, size: 13)
                                .foregroundStyle(Palette.muted)
                                .multilineTextAlignment(.leading)
                        }
                        if !row.facets.isEmpty {
                            Text(row.facets.sorted { $0.key < $1.key }.map(\.value).filter { !$0.isEmpty }.joined(separator: " · "))
                                .voice(.stamp, size: 10)
                                .foregroundStyle(Palette.muted)
                        }
                    }
                    Spacer(minLength: 0)
                    if !row.records.isEmpty {
                        Image(systemName: expanded ? "chevron.up" : "chevron.down")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Palette.muted)
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if expanded {
                ForEach(row.records) { record in
                    RecordRow(record: record)
                }
                if let target = ScreenLink(row.link), target.screen != screen {
                    Button("Open in \(target.screen.title)") { open(row.link) }
                        .buttonStyle(GhostButtonStyle())
                }
            }
        }
        .padding(Metrics.space3)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.cardLift, in: RoundedRectangle(cornerRadius: Metrics.radiusLg))
        .overlay(
            RoundedRectangle(cornerRadius: Metrics.radiusLg)
                .stroke(focused ? Palette.accentInk : Palette.line, lineWidth: focused ? 2 : 1)
        )
    }

    static func tone(_ status: String) -> Color {
        switch status {
        case "needs_you": Palette.warn
        case "doing": Palette.accentInk
        case "stuck": Palette.reject
        case "done": Palette.approve
        default: Palette.muted
        }
    }
}

private struct RecordRow: View {
    let record: ScreenRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(["#\(record.seq)", record.kind, record.lane, record.at.map { relativeTimestamp($0) }].compactMap { $0 }.joined(separator: " · "))
                .voice(.mono, size: 11)
                .foregroundStyle(Palette.muted)
            Text(record.text)
                .voice(.prose, size: 13)
                .foregroundStyle(Palette.ink)
                .textSelection(.enabled)
        }
        .padding(Metrics.space2)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.monoBg, in: RoundedRectangle(cornerRadius: Metrics.radiusMd))
    }
}

struct DrivesButton: ToolbarContent {
    @Environment(\.dismiss) private var dismiss

    var body: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                dismiss()
            } label: {
                Label("Drives", systemImage: "chevron.backward")
            }
        }
    }
}
