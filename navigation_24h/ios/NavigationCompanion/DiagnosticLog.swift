import Foundation

struct PortableStage: Encodable {
    let stage: String
    let attempt_id: String
    var candidate_ordinal: Int? = nil
    var candidate_count: Int? = nil
    var route_version: UInt64? = nil
    var event_id: UInt64? = nil
}

struct DiagnosticEntry: Encodable {
    let wall_timestamp: String
    let monotonic_ms: UInt64
    let source = "ios"
    let event: String
    let payload: String
}

@MainActor
final class DiagnosticLog {
    let url: URL
    private let recentURL: URL
    private(set) var errorCode: String?
    private let maximumBytes = 8 * 1024 * 1024
    private let recentMaximumBytes = 32 * 1024
    private var recentEntries = Data()

    init() {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        url = documents.appendingPathComponent("navigation-session.jsonl")
        recentURL = documents.appendingPathComponent("navigation-recent.jsonl")
    }

    func write<T: Encodable>(_ event: String, _ payload: T) {
        do {
            let payloadData = try JSONEncoder().encode(payload)
            let entry = DiagnosticEntry(wall_timestamp: ISO8601DateFormatter().string(from: Date()),
                                        monotonic_ms: NavigationClock.milliseconds, event: event,
                                        payload: String(decoding: payloadData, as: UTF8.self))
            var line = try JSONEncoder().encode(entry)
            line.append(0x0a)
            if !FileManager.default.fileExists(atPath: url.path) {
                try Data().write(to: url, options: .atomic)
            }
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            if let size = attributes[.size] as? NSNumber, size.intValue >= maximumBytes {
                let previous = url.deletingPathExtension().appendingPathExtension("previous.jsonl")
                if FileManager.default.fileExists(atPath: previous.path) {
                    try FileManager.default.removeItem(at: previous)
                }
                try FileManager.default.moveItem(at: url, to: previous)
                try Data().write(to: url, options: .atomic)
            }
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: line)
            if event == "ble_state" || event == "app_lifecycle" || event == "portable_stage" ||
                (event == "ble_rx" && ["status", "physical_button", "route_ready", "fault"]
                    .contains((payload as? DeviceMessage)?.type ?? "")) {
                try writeRecent(line)
            }
        } catch {
            errorCode = "LOG_IO_\((error as NSError).code)"
        }
    }

    private func writeRecent(_ line: Data) throws {
        guard line.count <= recentMaximumBytes else { return }
        recentEntries.append(line)
        if recentEntries.count > recentMaximumBytes {
            let overflow = recentEntries.count - recentMaximumBytes
            if let boundary = recentEntries.dropFirst(overflow - 1).firstIndex(of: 0x0a) {
                recentEntries = Data(recentEntries.suffix(from: recentEntries.index(after: boundary)))
            }
        }
        try recentEntries.write(to: recentURL, options: .atomic)
    }
}
