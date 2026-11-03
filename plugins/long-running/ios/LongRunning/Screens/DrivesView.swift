import IOSKit
import IOSKitUI
import SwiftUI

struct DrivesView: View {
    @State private var model = DrivesModel()
    @State private var showingMachines = false
    @State private var selected: Drive?

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Drives")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) {
                        Button {
                            showingMachines = true
                        } label: {
                            Label("Macs", systemImage: "desktopcomputer")
                        }
                    }
                }
                .task { await model.refresh() }
                .refreshable { await model.refresh() }
        }
        .fullScreenCover(item: $selected) { drive in
            DriveView(drive: drive)
        }
        .sheet(isPresented: $showingMachines, onDismiss: { Task { await model.refresh() } }) {
            MachinesView(identity: .longRunning) { machine in
                MachineDrivesView(machine: machine)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if !model.loaded {
            ProgressView("Finding drives…")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if model.drives.isEmpty {
            ContentUnavailableView {
                Label("No Active Drives", systemImage: "point.3.connected.trianglepath.dotted")
            } description: {
                Text(model.unreachable.isEmpty
                    ? "Pair a Mac that runs a drive, or open its tailnet pair link."
                    : "None of your Macs answered. Check that Tailscale is on.")
            } actions: {
                Button("Pair a Mac") { showingMachines = true }
            }
        } else {
            List {
                Section {
                    ForEach(model.drives) { drive in
                        Button {
                            selected = drive
                        } label: {
                            DriveRow(drive: drive)
                        }
                    }
                }
                if !model.unreachable.isEmpty {
                    Section("Not answering") {
                        ForEach(model.unreachable) { machine in
                            Label(machine.name, systemImage: "wifi.exclamationmark")
                                .foregroundStyle(Palette.muted)
                        }
                    }
                }
            }
        }
    }
}

private struct DriveRow: View {
    let drive: Drive

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(drive.name)
                .voice(.prose, size: 17, weight: .semibold)
                .foregroundStyle(Palette.ink)
            HStack {
                Text(drive.machine.name)
                Spacer()
                Text(relativeTimestamp(drive.updatedAt))
            }
            .voice(.prose, size: 13)
            .foregroundStyle(Palette.muted)
            if drive.dashboard == nil {
                Text("Talk only: this drive has no dashboard running.")
                    .voice(.prose, size: 12)
                    .foregroundStyle(Palette.warn)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct MachineDrivesView: View {
    let machine: Machine

    var body: some View {
        ContentUnavailableView(
            machine.name,
            systemImage: "desktopcomputer",
            description: Text("Its drives appear in the Drives list when you close this sheet.")
        )
    }
}
