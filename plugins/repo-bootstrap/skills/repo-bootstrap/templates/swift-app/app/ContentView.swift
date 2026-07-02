import SwiftUI

struct ContentView: View {
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: "sparkles")
                .imageScale(.large)
            Text("{{PROJECT_NAME}}")
                .font(.headline)
        }
        .padding()
    }
}

#Preview {
    ContentView()
}
