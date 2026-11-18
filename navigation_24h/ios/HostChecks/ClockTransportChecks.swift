import Foundation
@testable import NavigationProtocol

func transportChecks() {
    check("device_send_lower_bound_uses_ack_receive") {
        guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1200) else { return false }
        return clock.minimumOffsetMS == -700 && clock.deviceTimeLowerBound(atPhoneMS: 1250) == 550
    }
    check("clock_mapping_freshness_boundary") {
        guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1200) else { return false }
        return clock.deviceTimeLowerBound(atPhoneMS: 31200) == 30500 &&
            clock.deviceTimeLowerBound(atPhoneMS: 31201) == nil
    }
    check("clock_uncertainty_one_second_boundary") {
        DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 2000) != nil &&
            DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 2001) == nil
    }
    check("mapping_waits_for_current_monotonic_epoch") {
        guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1200) else { return false }
        return clock.deviceTimeLowerBound(atPhoneMS: 1199) == nil
    }
    check("device_boot_zero_bound_is_representable") {
        guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 0, receivedAtMS: 1200) else { return false }
        return clock.deviceTimeLowerBound(atPhoneMS: 1200) == 0
    }
    check("location_transit_upper_bound_includes_fragments") {
        let sample = FixSample(sequence: 1, sourceTimestampMS: 1700000000000, ageAtReceiptMS: 100,
                               receivedAtMS: 1000, lng: 114.1, lat: 22.3, accuracyM: 5,
                               speedMPS: nil, courseDeg: nil)
        guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1200),
              let frame = sample.transportFrame(phoneSessionID: "phone", at: 2000, clock: clock),
              let bound = frame.device_sent_lower_bound_ms else { return false }
        let receiverCompletedAt: UInt64 = 1500
        return frame.measurement_age_ms == 1100 && bound == 1300 &&
            frame.measurement_age_ms + receiverCompletedAt - bound == 1300
    }
    check("queued_location_waits_for_clock_reference") {
        let sample = FixSample(sequence: 1, sourceTimestampMS: 1700000000000, ageAtReceiptMS: 100,
                               receivedAtMS: 1000, lng: 114.1, lat: 22.3, accuracyM: 5,
                               speedMPS: nil, courseDeg: nil)
        return sample.transportFrame(phoneSessionID: "phone", at: 2000, clock: nil) == nil
    }
    check("source_wall_clock_change_preserves_monotonic_age") {
        let first = FixSample(sequence: 1, sourceTimestampMS: 1700000000000, ageAtReceiptMS: 500,
                              receivedAtMS: 1000, lng: 114.1, lat: 22.3, accuracyM: 5,
                              speedMPS: nil, courseDeg: nil)
        let changedWallClock = FixSample(sequence: 2, sourceTimestampMS: 1800000000000, ageAtReceiptMS: 500,
                                         receivedAtMS: 1000, lng: 114.1, lat: 22.3, accuracyM: 5,
                                         speedMPS: nil, courseDeg: nil)
        return first.frame(phoneSessionID: "phone", at: 2500).measurement_age_ms == 2000 &&
            changedWallClock.frame(phoneSessionID: "phone", at: 2500).measurement_age_ms == 2000
    }
    check("fresh_fix_waits_for_renewed_clock_after_queue_delay") {
        let sample = FixSample(sequence: 3, sourceTimestampMS: 1700000000000, ageAtReceiptMS: 100,
                               receivedAtMS: 31000, lng: 114.1, lat: 22.3, accuracyM: 5,
                               speedMPS: nil, courseDeg: nil)
        guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1200) else { return false }
        return sample.isFresh(at: 31201) &&
            sample.transportFrame(phoneSessionID: "phone", at: 31201, clock: clock) == nil
    }
    check("fragment_transit_can_cross_fix_expiry_boundary") {
        let sample = FixSample(sequence: 4, sourceTimestampMS: 1700000000000, ageAtReceiptMS: 4800,
                               receivedAtMS: 2000, lng: 114.1, lat: 22.3, accuracyM: 5,
                               speedMPS: nil, courseDeg: nil)
        guard let clock = DeviceClockEstimate(sentAtMS: 1000, deviceAtMS: 500, receivedAtMS: 1200),
              let frame = sample.transportFrame(phoneSessionID: "phone", at: 2000, clock: clock),
              let bound = frame.device_sent_lower_bound_ms else { return false }
        let receiverCompletedAt: UInt64 = 1600
        return frame.measurement_age_ms + receiverCompletedAt - bound == 5100
    }
    check("source_tracker_accepts_new_wall_clock_epoch") {
        var tracker = SourceMeasurementTracker()
        guard tracker.accept(sourceTimestampMS: 10_000, wallNowMS: 10_100, receivedAtMS: 1000) else { return false }
        return tracker.accept(sourceTimestampMS: 5000, wallNowMS: 5100, receivedAtMS: 2000)
    }
    check("source_tracker_preserves_source_order_within_epoch") {
        var tracker = SourceMeasurementTracker()
        guard tracker.accept(sourceTimestampMS: 10_000, wallNowMS: 10_100, receivedAtMS: 1000) else { return false }
        return !tracker.accept(sourceTimestampMS: 9999, wallNowMS: 10_200, receivedAtMS: 1100)
    }
}
