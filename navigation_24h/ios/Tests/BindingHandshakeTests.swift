import Foundation
import XCTest
@testable import NavigationProtocol

final class BindingHandshakeTests: XCTestCase {
    private let peer = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
    private let otherPeer = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!
    private let identity = SessionIdentity(phoneSessionID: "phone", sessionID: "navigation", generation: 1)

    private var link: BLELinkIdentity { BLELinkIdentity(peripheralID: peer, generation: 1) }
    private var trustedStatus: DeviceMessage {
        DeviceMessage(type: "status", session_id: "", generation: 0, state: "IDLE", ble: "CONNECTED")
    }

    func testIdleTrustedStatusStartsPendingBindBeforeNavigationSessionMatches() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        binding.begin(command: command, link: link)

        let next = binding.command(for: trustedStatus, from: link, at: 1000)

        XCTAssertEqual(next?.command_id, command.command_id)
    }

    func testManeuverKeepsBindingWaitingForTrustedStatus() {
        var binding = BindHandshake()
        binding.begin(command: identity.command(.bind), link: link)

        let next = binding.command(for: DeviceMessage(type: "maneuver"), from: link, at: 1000)

        XCTAssertNil(next)
    }

    func testDisconnectedStatusKeepsBindingWaitingForTrustedStatus() {
        var binding = BindHandshake()
        binding.begin(command: identity.command(.bind), link: link)

        let next = binding.command(for: DeviceMessage(type: "status", ble: "DISCONNECTED"), from: link, at: 1000)

        XCTAssertNil(next)
    }

    func testRetryUsesSameCommandAtTwoSecondBoundary() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        binding.begin(command: command, link: link)
        _ = binding.command(for: trustedStatus, from: link, at: 1000)
        XCTAssertNil(binding.command(for: trustedStatus, from: link, at: 2999))

        let retry = binding.command(for: trustedStatus, from: link, at: 3000)

        XCTAssertEqual(retry?.command_id, command.command_id)
    }

    func testTrustedStatusesBoundBindTransmissionsToThreeAttempts() {
        var binding = BindHandshake()
        binding.begin(command: identity.command(.bind), link: link)
        _ = binding.command(for: trustedStatus, from: link, at: 1000)
        _ = binding.command(for: trustedStatus, from: link, at: 3000)
        _ = binding.command(for: trustedStatus, from: link, at: 5000)

        let next = binding.command(for: trustedStatus, from: link, at: 7000)

        XCTAssertNil(next)
        XCTAssertTrue(binding.isExhausted(at: 7000))
    }

    func testMatchingBoundAckReturnsCapturedPeripheralAndFinishesHandshake() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        binding.begin(command: command, link: link)
        _ = binding.command(for: trustedStatus, from: link, at: 1000)
        let ack = DeviceMessage(type: "ack", command_id: command.command_id, accepted: true, reason: "BOUND")

        let result = binding.acknowledge(ack, from: link)

        XCTAssertEqual(result, peer)
        XCTAssertNil(binding.commandID)
    }

    func testSuccessfulAckUsesBoundReasonToCompleteBinding() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        binding.begin(command: command, link: link)
        _ = binding.command(for: trustedStatus, from: link, at: 1000)
        let ack = DeviceMessage(type: "ack", command_id: command.command_id, accepted: true, reason: "OK")

        let result = binding.acknowledge(ack, from: link)

        XCTAssertNil(result)
        XCTAssertEqual(binding.commandID, command.command_id)
    }

    func testBoundAckWaitsForFirstTransmittedBind() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        binding.begin(command: command, link: link)
        let ack = DeviceMessage(type: "ack", command_id: command.command_id, accepted: true, reason: "BOUND")

        let result = binding.acknowledge(ack, from: link)

        XCTAssertNil(result)
        XCTAssertEqual(binding.commandID, command.command_id)
    }

    func testStatusFromPreviousPeripheralKeepsCurrentSelectionPending() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        let selected = BLELinkIdentity(peripheralID: otherPeer, generation: 2)
        binding.begin(command: command, link: selected)

        let next = binding.command(for: trustedStatus, from: link, at: 1000)

        XCTAssertNil(next)
        XCTAssertEqual(binding.commandID, command.command_id)
    }

    func testStatusFromPreviousEpochKeepsReconnectedPeerPending() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        let reconnected = BLELinkIdentity(peripheralID: peer, generation: 2)
        binding.begin(command: command, link: reconnected)

        let next = binding.command(for: trustedStatus, from: link, at: 1000)

        XCTAssertNil(next)
        XCTAssertEqual(binding.commandID, command.command_id)
    }

    func testAckFromPreviousPeripheralKeepsCurrentSelectionPending() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        let selected = BLELinkIdentity(peripheralID: otherPeer, generation: 2)
        binding.begin(command: command, link: selected)
        _ = binding.command(for: trustedStatus, from: selected, at: 1000)
        let ack = DeviceMessage(type: "ack", command_id: command.command_id, accepted: true, reason: "BOUND")

        let result = binding.acknowledge(ack, from: link)

        XCTAssertNil(result)
        XCTAssertEqual(binding.commandID, command.command_id)
    }

    func testAckFromPreviousEpochKeepsReconnectedPeerPending() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        let reconnected = BLELinkIdentity(peripheralID: peer, generation: 2)
        binding.begin(command: command, link: reconnected)
        _ = binding.command(for: trustedStatus, from: reconnected, at: 1000)
        let ack = DeviceMessage(type: "ack", command_id: command.command_id, accepted: true, reason: "BOUND")

        let result = binding.acknowledge(ack, from: link)

        XCTAssertNil(result)
        XCTAssertEqual(binding.commandID, command.command_id)
    }

    func testEarlierCommandAckKeepsNewBindingPending() {
        var binding = BindHandshake()
        let earlier = identity.command(.bind)
        let current = identity.command(.bind)
        binding.begin(command: current, link: link)
        _ = binding.command(for: trustedStatus, from: link, at: 1000)
        let ack = DeviceMessage(type: "ack", command_id: earlier.command_id, accepted: true, reason: "BOUND")

        let result = binding.acknowledge(ack, from: link)

        XCTAssertNil(result)
        XCTAssertEqual(binding.commandID, current.command_id)
    }

    func testApprovalPendingAckPreservesRetry() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        binding.begin(command: command, link: link)
        _ = binding.command(for: trustedStatus, from: link, at: 1000)
        let ack = DeviceMessage(type: "ack", command_id: command.command_id, accepted: false,
                                reason: "DEVICE_CONFIRMATION_REQUIRED")
        XCTAssertNil(binding.acknowledge(ack, from: link))

        let retry = binding.command(for: trustedStatus, from: link, at: 3000)

        XCTAssertEqual(retry?.command_id, command.command_id)
    }

    func testResetClearsPendingBindAndDelayedAck() {
        var binding = BindHandshake()
        let command = identity.command(.bind)
        binding.begin(command: command, link: link)
        _ = binding.command(for: trustedStatus, from: link, at: 1000)
        let ack = DeviceMessage(type: "ack", command_id: command.command_id, accepted: true, reason: "BOUND")

        binding.reset()

        XCTAssertNil(binding.commandID)
        XCTAssertNil(binding.acknowledge(ack, from: link))
        XCTAssertNil(binding.command(for: trustedStatus, from: link, at: 3000))
    }
}
