import XCTest
@testable import NavigationProtocol

final class RouteFeedbackTests: XCTestCase {
    private let identity = SessionIdentity(phoneSessionID: "phone", sessionID: "nav", generation: 2)
    private let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1100)

    private func message(_ type: String = "route_ready", session: String = "nav",
                         generation: UInt64 = 2, route: UInt64 = 1,
                         event: UInt64 = 5, issued: UInt64 = 650) -> DeviceMessage {
        DeviceMessage(type: type, session_id: session, generation: generation, route_version: route,
                      event_id: event, ttl_ms: 2000, issued_at_ms: issued)
    }

    func testCurrentReadyEventProducesRouteSuccess() {
        var tracker = RouteFeedbackTracker()
        XCTAssertEqual(tracker.consume(message(), identity: identity, routeVersion: 1,
                                       clock: clock, at: 1200)?.kind, .ready)
    }

    func testRouteReadyRequiresCommittedVersion() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(route: 0), identity: identity, routeVersion: 0,
                                     clock: clock, at: 1200))
    }

    func testInitialRouteFaultProducesFailureFeedback() {
        var tracker = RouteFeedbackTracker()
        var fault = message("fault", route: 0)
        fault.reason = "COORDINATE_REVIEW_REQUIRED"
        let feedback = tracker.consume(fault, identity: identity, routeVersion: 0, clock: clock, at: 1200)
        XCTAssertEqual(feedback?.kind, .failed)
        XCTAssertEqual(feedback?.reason, fault.reason)
    }

    func testPriorSessionIsDiscarded() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(session: "old"), identity: identity, routeVersion: 1,
                                     clock: clock, at: 1200))
    }

    func testPriorGenerationIsDiscarded() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(generation: 1), identity: identity, routeVersion: 1,
                                     clock: clock, at: 1200))
    }

    func testPriorRouteIsDiscarded() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(), identity: identity, routeVersion: 2,
                                     clock: clock, at: 1200))
    }

    func testExpiredEventIsDiscarded() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(issued: 100), identity: identity, routeVersion: 1,
                                     clock: clock, at: 3000))
    }

    func testClockReferenceIsRequired() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(), identity: identity, routeVersion: 1,
                                     clock: nil, at: 1200))
    }

    func testDuplicateEventProducesOneFeedback() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNotNil(tracker.consume(message(), identity: identity, routeVersion: 1,
                                        clock: clock, at: 1200))
        XCTAssertNil(tracker.consume(message(), identity: identity, routeVersion: 1,
                                     clock: clock, at: 1201))
    }

    func testStatusIsSeparateFromRouteFeedback() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message("status"), identity: identity, routeVersion: 1,
                                     clock: clock, at: 1200))
    }

    func testFutureDeviceEventRequiresCurrentClockEpoch() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(issued: 8000), identity: identity, routeVersion: 1,
                                     clock: clock, at: 1200))
    }

    func testStaleClockEstimateRequiresRefresh() {
        var tracker = RouteFeedbackTracker()
        XCTAssertNil(tracker.consume(message(issued: 31_500), identity: identity, routeVersion: 1,
                                     clock: clock, at: 32_000))
    }
}
