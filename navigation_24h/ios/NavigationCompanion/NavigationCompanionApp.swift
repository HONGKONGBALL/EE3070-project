import SwiftUI

@main
struct NavigationCompanionApp: App {
    @StateObject private var session = SessionStore()
    @StateObject private var macLocation = MacLocationSharing()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            SessionView(session: session, macLocation: macLocation)
                .onOpenURL(perform: macLocation.stage)
                .onChange(of: phase) { _, value in
                    session.sceneChanged(String(describing: value))
                    macLocation.setForeground(value == .active)
                }
        }
    }
}
