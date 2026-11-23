import Foundation
import XCTest
@testable import NavigationProtocol

final class SessionProtocolTests: XCTestCase {
    func testPhysicalButtonsRequireValidNewSequence() throws {
        let decoder = JSONDecoder()
        let start = try decoder.decode(DeviceMessage.self, from: Data(
            #"{"type":"physical_button","button":"K2","button_seq":5}"#.utf8))
        XCTAssertEqual(start.physicalButton(after: 4)?.0, .record)
        XCTAssertNil(start.physicalButton(after: 5))
        let invalid = try decoder.decode(DeviceMessage.self, from: Data(
            #"{"type":"physical_button","button":"K4","button_seq":6}"#.utf8))
        XCTAssertNil(invalid.physicalButton(after: 5))
    }

    func testFixAgeUsesMonotonicElapsedTime() {
        let sample = FixSample(sequence: 1, sourceTimestampMS: 1000, ageAtReceiptMS: 500,
                               receivedAtMS: 100, lng: 114.1, lat: 22.3,
                               accuracyM: 5, speedMPS: nil, courseDeg: nil)
        XCTAssertEqual(sample.frame(phoneSessionID: "phone", at: 350).measurement_age_ms, 750)
    }

    func testStaleFixGatedAtFiveSeconds() {
        let sample = FixSample(sequence: 1, sourceTimestampMS: 1000, ageAtReceiptMS: 4000,
                               receivedAtMS: 100, lng: 114.1, lat: 22.3,
                               accuracyM: 5, speedMPS: nil, courseDeg: nil)
        XCTAssertTrue(sample.isFresh(at: 1099))
        XCTAssertFalse(sample.isFresh(at: 1100))
    }

    func testCancelInvalidatesGenerationBeforeRecovery() {
        var identity = SessionIdentity(phoneSessionID: "phone", sessionID: "nav", generation: 4)
        let cancellation = identity.cancelCommand()
        XCTAssertEqual(identity.generation, 5)
        XCTAssertEqual(cancellation.generation, 5)
        XCTAssertEqual(cancellation.session_id, "nav")
    }

    func testEventRequiresCurrentSessionGenerationAndVersion() {
        let identity = SessionIdentity(phoneSessionID: "phone", sessionID: "nav", generation: 3)
        let current = DeviceMessage(type: "maneuver", session_id: "nav", generation: 3, route_version: 4,
                                    event_id: 1, ttl_ms: 2000)
        XCTAssertTrue(current.isCurrent(for: identity, routeVersion: 4, receivedAtMS: 100, nowMS: 2099))
        XCTAssertFalse(current.isCurrent(for: identity, routeVersion: 4, receivedAtMS: 100, nowMS: 2100))
        XCTAssertFalse(current.isCurrent(for: identity, routeVersion: 5, receivedAtMS: 100, nowMS: 101))
    }

    func testLocationJSONContainsCoordinateSemantics() throws {
        let sample = FixSample(sequence: 2, sourceTimestampMS: 1700000000000, ageAtReceiptMS: 0,
                               receivedAtMS: 10, lng: 114.1, lat: 22.3,
                               accuracyM: 5, speedMPS: nil, courseDeg: nil)
        let frame = sample.frame(phoneSessionID: "phone", at: 10)
        let data = try JSONEncoder().encode(frame)
        let decoded = try JSONDecoder().decode(LocationFrame.self, from: data)
        XCTAssertEqual(decoded.coord_frame, "WGS84")
        XCTAssertEqual(decoded.fix_seq, 2)
        XCTAssertEqual(decoded.phone_session_id, "phone")
        XCTAssertLessThanOrEqual(data.count, 512)
    }

    func testDestinationRejectsOutOfRangeCoordinates() {
        XCTAssertNil(Destination(name: "入口", lng: 181, lat: 22.3))
        XCTAssertNil(Destination(name: "入口", lng: 114.1, lat: -91))
        XCTAssertNotNil(Destination(name: "入口", lng: 114.1, lat: 22.3))
    }
}
