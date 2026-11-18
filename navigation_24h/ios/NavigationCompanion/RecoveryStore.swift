import Foundation

struct RecoverySnapshot: Codable {
    var identity: SessionIdentity
    var active: Bool
    var routeVersion: UInt64
    var boundPeripheralID: UUID?
}

@MainActor
final class RecoveryStore {
    private let defaults = UserDefaults.standard

    func load() -> RecoverySnapshot {
        if let data = defaults.data(forKey: "navigationRecovery"),
           let snapshot = try? JSONDecoder().decode(RecoverySnapshot.self, from: data) {
            return snapshot
        }
        return RecoverySnapshot(identity: SessionIdentity(phoneSessionID: UUID().uuidString,
                                                          sessionID: UUID().uuidString, generation: 1),
                                active: false, routeVersion: 0, boundPeripheralID: nil)
    }

    func save(_ snapshot: RecoverySnapshot) throws {
        defaults.set(try JSONEncoder().encode(snapshot), forKey: "navigationRecovery")
    }
}
