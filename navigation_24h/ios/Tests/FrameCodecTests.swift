import Foundation
import XCTest
@testable import NavigationProtocol

final class FrameCodecTests: XCTestCase {
    func testRoundTripAtMinimumATTWriteLength() throws {
        let payload = Data(repeating: 0x41, count: 512)
        let packets = try FrameCodec.fragments(payload, messageID: 65535, maximumWriteLength: 20)
        var receiver = FrameReassembler()
        var result: Data?
        for (index, packet) in packets.enumerated() {
            XCTAssertLessThanOrEqual(packet.count, 20)
            result = try receiver.receive(packet, at: UInt64(index))
        }
        XCTAssertEqual(result, payload)
    }

    func testHeaderUsesLittleEndianAndExactContract() throws {
        let packets = try FrameCodec.fragments(Data("ok".utf8), messageID: 0x1234, maximumWriteLength: 20)
        XCTAssertEqual(Array(packets[0].prefix(10)), [0x4e, 0x31, 1, 0, 0x34, 0x12, 2, 0, 0, 0])
    }

    func testCapacityRejectsOversizedPayload() {
        XCTAssertThrowsError(try FrameCodec.fragments(Data(repeating: 0, count: 513), messageID: 1, maximumWriteLength: 20))
    }

    func testEmptyPayloadRejected() {
        XCTAssertThrowsError(try FrameCodec.fragments(Data(), messageID: 1, maximumWriteLength: 20))
    }

    func testWriteLengthMustFitHeaderAndPayload() {
        XCTAssertThrowsError(try FrameCodec.fragments(Data([1]), messageID: 1, maximumWriteLength: 10))
    }

    func testReorderedFragmentRejected() throws {
        let packets = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
        var receiver = FrameReassembler()
        XCTAssertThrowsError(try receiver.receive(packets[1], at: 0))
    }

    func testTimeoutStartsAtFirstFragment() throws {
        let packets = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
        var receiver = FrameReassembler()
        XCTAssertNil(try receiver.receive(packets[0], at: 100))
        XCTAssertThrowsError(try receiver.receive(packets[1], at: 1100))
    }

    func testWrongMessageIDRejected() throws {
        let first = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
        let other = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 3, maximumWriteLength: 20)
        var receiver = FrameReassembler()
        XCTAssertNil(try receiver.receive(first[0], at: 0))
        XCTAssertThrowsError(try receiver.receive(other[1], at: 1))
    }

    func testNewFirstFragmentReplacesPendingMessage() throws {
        let pending = try FrameCodec.fragments(Data(repeating: 2, count: 30), messageID: 2, maximumWriteLength: 20)
        let next = try FrameCodec.fragments(Data([9]), messageID: 3, maximumWriteLength: 20)
        var receiver = FrameReassembler()
        XCTAssertNil(try receiver.receive(pending[0], at: 0))
        XCTAssertEqual(try receiver.receive(next[0], at: 1), Data([9]))
    }
}
