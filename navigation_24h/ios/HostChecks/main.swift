import Foundation
@testable import NavigationProtocol

struct CheckFailure: Error { let message: String }
var passed = 0
var failed = 0

func check(_ name: String, _ body: () throws -> Bool) {
    do {
        guard try body() else { throw CheckFailure(message: name) }
        passed += 1
        print("PASS \(name)")
    } catch {
        failed += 1
        print("FAIL \(name): \(error)")
    }
}

func rejects(_ body: () throws -> Void) -> Bool {
    do { try body(); return false } catch { return true }
}

check("header_little_endian") {
    let packets = try FrameCodec.fragments(Data("ok".utf8), messageID: 0x1234, maximumWriteLength: 20)
    return packets.count == 1 && Array(packets[0].prefix(10)) == [0x4e, 0x31, 1, 0, 0x34, 0x12, 2, 0, 0, 0]
}
check("full_capacity_round_trip") {
    let payload = Data(repeating: 0x41, count: 512)
    let packets = try FrameCodec.fragments(payload, messageID: 65535, maximumWriteLength: 20)
    var receiver = FrameReassembler()
    var result: Data?
    for (index, packet) in packets.enumerated() {
        guard packet.count <= 20 else { return false }
        result = try receiver.receive(packet, at: UInt64(index))
    }
    return result == payload
}
check("capacity_513_rejected") {
    rejects { _ = try FrameCodec.fragments(Data(repeating: 0, count: 513), messageID: 1, maximumWriteLength: 20) }
}
check("empty_payload_rejected") {
    rejects { _ = try FrameCodec.fragments(Data(), messageID: 1, maximumWriteLength: 20) }
}
check("write_length_10_rejected") {
    rejects { _ = try FrameCodec.fragments(Data([1]), messageID: 1, maximumWriteLength: 10) }
}
check("reordered_fragment_rejected") {
    let packets = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
    var receiver = FrameReassembler()
    return rejects { _ = try receiver.receive(packets[1], at: 0) }
}
check("deadline_from_first_fragment") {
    let packets = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
    var receiver = FrameReassembler()
    guard try receiver.receive(packets[0], at: 100) == nil else { return false }
    return rejects { _ = try receiver.receive(packets[1], at: 1100) }
}
check("interleaved_id_rejected") {
    let one = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
    let two = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 3, maximumWriteLength: 20)
    var receiver = FrameReassembler()
    guard try receiver.receive(one[0], at: 0) == nil else { return false }
    return rejects { _ = try receiver.receive(two[1], at: 1) }
}
check("new_first_fragment_replaces_slot") {
    let one = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
    let two = try FrameCodec.fragments(Data([9]), messageID: 3, maximumWriteLength: 20)
    var receiver = FrameReassembler()
    guard try receiver.receive(one[0], at: 0) == nil else { return false }
    return try receiver.receive(two[0], at: 1) == Data([9])
}
check("monotonic_fix_age") {
    let sample = FixSample(sequence: 1, sourceTimestampMS: 1000, ageAtReceiptMS: 500,
                           receivedAtMS: 100, lng: 114.1, lat: 22.3,
                           accuracyM: 5, speedMPS: nil, courseDeg: nil)
    let frame = sample.frame(phoneSessionID: "phone", at: 350)
    return frame.measurement_age_ms == 750 && frame.coord_frame == "WGS84" && frame.fix_seq == 1
}
check("fix_age_five_second_boundary") {
    let sample = FixSample(sequence: 1, sourceTimestampMS: 1000, ageAtReceiptMS: 4000,
                           receivedAtMS: 100, lng: 114.1, lat: 22.3,
                           accuracyM: 5, speedMPS: nil, courseDeg: nil)
    return sample.isFresh(at: 1099) && !sample.isFresh(at: 1100)
}
check("cancel_generation") {
    var identity = SessionIdentity(phoneSessionID: "phone", sessionID: "nav", generation: 4)
    let command = identity.cancelCommand()
    return identity.generation == 5 && command.generation == 5 && command.session_id == "nav"
}
check("event_identity_version_and_expiry") {
    let identity = SessionIdentity(phoneSessionID: "phone", sessionID: "nav", generation: 3)
    let event = DeviceMessage(type: "maneuver", session_id: "nav", generation: 3, route_version: 4,
                              event_id: 1, ttl_ms: 2000)
    return event.isCurrent(for: identity, routeVersion: 4, receivedAtMS: 100, nowMS: 2099) &&
        !event.isCurrent(for: identity, routeVersion: 4, receivedAtMS: 100, nowMS: 2100) &&
        !event.isCurrent(for: identity, routeVersion: 5, receivedAtMS: 100, nowMS: 101)
}
check("destination_coordinate_bounds") {
    Destination(name: "入口", lng: 181, lat: 22.3) == nil && Destination(name: "入口", lng: 114.1, lat: 22.3) != nil
}
check("device_clock_conservative_age") {
    guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1200) else { return false }
    return clock.roundTripMS == 200 && clock.ageUpperBoundMS(issuedAtMS: 600, nowMS: 1500) == 400
}
check("device_clock_rejects_long_round_trip") {
    DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 3001) == nil
}
check("invalid_header_releases_reassembly_slot") {
    let packet = try FrameCodec.fragments(Data([9]), messageID: 3, maximumWriteLength: 20)[0]
    var receiver = FrameReassembler()
    guard rejects({ _ = try receiver.receive(Data([0]), at: 0) }) else { return false }
    return try receiver.receive(packet, at: 1) == Data([9])
}
check("declared_length_bounds") {
    var receiver = FrameReassembler()
    let zero = Data([0x4e, 0x31, 1, 0, 1, 0, 0, 0, 0, 0, 9])
    return rejects { _ = try receiver.receive(zero, at: 1) }
}
check("numeric_event_wire_decode") {
    let bytes = Data(#"{"type":"maneuver","session_id":"nav","generation":3,"route_version":4,"event_id":12,"ttl_ms":2000,"issued_at_ms":400}"#.utf8)
    return try JSONDecoder().decode(DeviceMessage.self, from: bytes).event_id == 12
}
transportChecks()
print("{\"suite\":\"ios_protocol\",\"passed\":\(passed),\"failed\":\(failed)}")
if failed > 0 { exit(1) }
