import Foundation

public struct Destination: Codable, Equatable {
    public let name: String
    public let lng: Double
    public let lat: Double
    public let coord_frame: String

    public init?(name: String, lng: Double, lat: Double) {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              name.utf8.count <= 96, lng.isFinite, lat.isFinite,
              (-180...180).contains(lng), (-90...90).contains(lat) else { return nil }
        self.name = name
        self.lng = lng
        self.lat = lat
        self.coord_frame = "WGS84"
    }
}

public struct LocationFrame: Codable, Equatable {
    public let type: String
    public let phone_session_id: String
    public let fix_seq: UInt64
    public let source_timestamp_ms: Int64
    public let measurement_age_ms: UInt64
    public let coord_frame: String
    public let lng: Double
    public let lat: Double
    public let horizontal_accuracy_m: Double
    public let speed_mps: Double?
    public let course_deg: Double?
    public let device_sent_lower_bound_ms: UInt64?
}

public struct FixSample {
    public let sequence: UInt64
    public let sourceTimestampMS: Int64
    public let ageAtReceiptMS: UInt64
    public let receivedAtMS: UInt64
    public let lng: Double
    public let lat: Double
    public let accuracyM: Double
    public let speedMPS: Double?
    public let courseDeg: Double?

    public func frame(phoneSessionID: String, at now: UInt64,
                      deviceSentLowerBoundMS: UInt64? = nil) -> LocationFrame {
        let elapsed = now >= receivedAtMS ? now - receivedAtMS : 0
        return LocationFrame(type: "location", phone_session_id: phoneSessionID,
                             fix_seq: sequence, source_timestamp_ms: sourceTimestampMS,
                             measurement_age_ms: ageAtReceiptMS + elapsed, coord_frame: "WGS84",
                             lng: lng, lat: lat, horizontal_accuracy_m: accuracyM,
                             speed_mps: speedMPS, course_deg: courseDeg,
                             device_sent_lower_bound_ms: deviceSentLowerBoundMS)
    }

    public func isFresh(at now: UInt64) -> Bool {
        accuracyM >= 0 && accuracyM <= 20 && frame(phoneSessionID: "", at: now).measurement_age_ms < 5000
    }

    public func transportFrame(phoneSessionID: String, at now: UInt64,
                               clock: DeviceClockEstimate?) -> LocationFrame? {
        guard isFresh(at: now), let clock,
              let bound = clock.deviceTimeLowerBound(atPhoneMS: now) else { return nil }
        return frame(phoneSessionID: phoneSessionID, at: now, deviceSentLowerBoundMS: bound)
    }
}

public enum NavigationCommand: String, Codable {
    case start, cancel, pause, resume, `repeat`, confirm_turn, confirm_crossing
    case confirm_arrival, get_status, permission, bind
}

public struct CommandFrame: Codable {
    public var type = "command"
    public let command: NavigationCommand
    public let command_id: String
    public let session_id: String
    public let generation: UInt64
    public var phone_session_id: String?
    public var destination: Destination?
    public var route_version: UInt64?
    public var event_id: UInt64?
    public var fix_seq: UInt64?
    public var confirmation_source: String?
    public var lease_ms: UInt64?
    public var phone_send_monotonic_ms: UInt64?
}

public struct SessionIdentity: Codable, Equatable {
    public let phoneSessionID: String
    public var sessionID: String
    public var generation: UInt64

    public mutating func cancelCommand() -> CommandFrame {
        generation += 1
        return command(.cancel)
    }

    public func command(_ operation: NavigationCommand) -> CommandFrame {
        CommandFrame(command: operation, command_id: UUID().uuidString,
                     session_id: sessionID, generation: generation, phone_session_id: phoneSessionID)
    }
}

public struct DeviceMessage: Codable {
    public let type: String
    public var session_id: String?
    public var generation: UInt64?
    public var route_version: UInt64?
    public var event_id: UInt64?
    public var ttl_ms: UInt64?
    public var issued_at_ms: UInt64?
    public var state: String?
    public var fix_seq: UInt64?
    public var fix_age_ms: UInt64?
    public var quality: String?
    public var ble: String?
    public var wifi: String?
    public var mcp: String?
    public var distance_m: Double?
    public var confirmation_source: String?
    public var command_id: String?
    public var accepted: Bool?
    public var reason: String?
    public var config_version: String?
    public var phone_send_monotonic_ms: UInt64?
    public var device_monotonic_ms: UInt64?
    public var button: String?
    public var button_seq: UInt64?

    public func physicalButton(after sequence: UInt64) -> (PortableButton, UInt64)? {
        guard type == "physical_button", let button, let key = PortableButton(rawValue: button),
              let button_seq, button_seq > sequence else { return nil }
        return (key, button_seq)
    }

    public func isCurrent(for identity: SessionIdentity, routeVersion: UInt64,
                          receivedAtMS: UInt64, nowMS: UInt64) -> Bool {
        session_id == identity.sessionID && generation == identity.generation &&
        route_version == routeVersion && nowMS >= receivedAtMS &&
        nowMS - receivedAtMS < min(ttl_ms ?? 0, 2000)
    }
}

public enum PortableButton: String {
    case confirm = "K1"
    case record = "K2"
    case next = "K3"
}

public struct DeviceClockEstimate {
    public let maximumOffsetMS: Double
    public let minimumOffsetMS: Double
    public let roundTripMS: UInt64
    public let synchronizedAtMS: UInt64

    public init?(sentAtMS: UInt64, deviceAtMS: UInt64, receivedAtMS: UInt64) {
        guard receivedAtMS >= sentAtMS, receivedAtMS - sentAtMS <= 1000,
              receivedAtMS <= 9_007_199_254_740_991,
              deviceAtMS <= 9_007_199_254_740_991 else { return nil }
        roundTripMS = receivedAtMS - sentAtMS
        maximumOffsetMS = Double(deviceAtMS) - Double(sentAtMS)
        minimumOffsetMS = Double(deviceAtMS) - Double(receivedAtMS)
        synchronizedAtMS = receivedAtMS
    }

    public func ageUpperBoundMS(issuedAtMS: UInt64, nowMS: UInt64) -> Double {
        max(0, Double(nowMS) - Double(issuedAtMS) + maximumOffsetMS)
    }

    public func isUsable(at now: UInt64) -> Bool {
        now >= synchronizedAtMS && now - synchronizedAtMS <= 30_000 && roundTripMS <= 1000
    }

    public func deviceTimeLowerBound(atPhoneMS now: UInt64) -> UInt64? {
        guard isUsable(at: now) else { return nil }
        let lowerBound = max(0, (Double(now) + minimumOffsetMS).rounded(.down))
        guard lowerBound <= 9_007_199_254_740_991 else { return nil }
        return UInt64(lowerBound)
    }
}

public enum NavigationClock {
    public static var milliseconds: UInt64 { DispatchTime.now().uptimeNanoseconds / 1_000_000 }
}

public struct SourceMeasurementTracker {
    private var wallOffsetMS: Double?
    private var lastSourceTimestampMS: Int64?
    public init() {}

    public mutating func accept(sourceTimestampMS: Int64, wallNowMS: Int64,
                                receivedAtMS: UInt64) -> Bool {
        let offset = Double(wallNowMS) - Double(receivedAtMS)
        if let previous = wallOffsetMS, abs(offset - previous) > 1000 {
            lastSourceTimestampMS = nil
        }
        wallOffsetMS = offset
        if let previous = lastSourceTimestampMS, sourceTimestampMS <= previous { return false }
        lastSourceTimestampMS = sourceTimestampMS
        return true
    }
}

public enum RouteFeedbackKind: String { case ready, failed }

public struct RouteFeedback {
    public let kind: RouteFeedbackKind
    public let routeVersion: UInt64
    public let eventID: UInt64
    public let reason: String?
}

public struct RouteFeedbackTracker {
    private var seen: [UInt64] = []
    public init() {}

    public mutating func consume(_ message: DeviceMessage, identity: SessionIdentity,
                                 routeVersion: UInt64, clock: DeviceClockEstimate?,
                                 at now: UInt64) -> RouteFeedback? {
        let kind: RouteFeedbackKind
        switch message.type {
        case "route_ready": kind = .ready
        case "fault": kind = .failed
        default: return nil
        }
        guard kind != .ready || routeVersion > 0,
              message.isCurrent(for: identity, routeVersion: routeVersion, receivedAtMS: now, nowMS: now),
              let id = message.event_id, id > 0, !seen.contains(id),
              let issued = message.issued_at_ms, let clock, clock.isUsable(at: now),
              Double(issued) <= Double(now) + clock.maximumOffsetMS,
              clock.ageUpperBoundMS(issuedAtMS: issued, nowMS: now) < Double(min(message.ttl_ms ?? 0, 2000))
        else { return nil }
        seen = Array(seen.suffix(63)) + [id]
        return RouteFeedback(kind: kind, routeVersion: routeVersion, eventID: id, reason: message.reason)
    }
}
