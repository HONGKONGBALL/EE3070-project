#include "test_support.hpp"
#include <limits>

namespace test {
std::vector<Case> fix_cases() {
    return {
        {"TC11", "duplicate_and_reordered_measurements", [] {
            nav::Engine e; begin(e);
            auto duplicate = e.ingest(fix(1, 1), 2000);
            require(!duplicate.accepted && duplicate.reason == nav::FixReason::Sequence,
                    "duplicate discarded by measurement sequence");
            require(e.ingest(fix(3, 2), 3000).accepted, "new sequence accepted");
            require(!e.ingest(fix(2, 3), 4000).accepted, "reordered fix discarded");
            require(e.snapshot().fix_seq == 3, "last independent sequence preserved");
        }},
        {"TC11", "cached_source_measurement_keeps_identity", [] {
            nav::Engine e; begin(e);
            auto cached = fix(2, 0); cached.source_timestamp_ms = fix(1, 0).source_timestamp_ms;
            require(!e.ingest(cached, 2000).accepted,
                    "cached measurement remains one independent sample");
            require(e.snapshot().fix_seq == 1, "accepted source measurement identity retained");
        }},
        {"TC12", "source_age_and_monotonic_expiry", [] {
            nav::Engine e; begin(e);
            require(!e.ingest(fix(2, 1, 0, 3, 5000), 2000).accepted,
                    "5000ms age reaches expiry boundary");
            e.tick(5999); require(e.snapshot().quality == nav::Quality::Valid,
                                  "valid before deadline");
            e.tick(6000); state(e, nav::State::WaitFix);
            require(e.snapshot().quality == nav::Quality::Lost, "lost at deadline");
        }},
        {"TC13", "precision_and_jump_quality", [] {
            nav::Engine e; begin(e);
            require(!e.ingest(fix(2, 1, 0, 21), 2000).accepted,
                    "accuracy above 20m excluded from progress");
            require(e.snapshot().quality == nav::Quality::Degraded, "quality degraded");
            require(!e.ingest(fix(3, 1000), 3000).accepted, "unreachable jump rejected");
            require(e.ingest(fix(4, 3), 4000).accepted, "nearby fresh fix recovers");
        }},
        {"TC13", "isolated_jump_marks_degraded_quality", [] {
            nav::Engine e; activate(e, straight());
            require(!e.ingest(fix(2, 90), 2000).accepted, "isolated drift rejected");
            require(e.snapshot().quality == nav::Quality::Degraded,
                    "jump records degraded quality");
            state(e, nav::State::Relocalize);
            require(e.snapshot().fix_seq == 1, "valid measurement retained for age");
        }},
        {"TC14", "wall_clock_change_keeps_monotonic_age", [] {
            nav::Engine e; begin(e);
            auto moved = fix(2, 1); moved.source_timestamp_ms = 1;
            require(e.ingest(moved, 2000).accepted, "wall timestamp change accepted");
            e.tick(6999); require(e.snapshot().fix_age_ms == 4999,
                                  "measurement ages on receive monotonic clock");
            e.tick(7000); state(e, nav::State::WaitFix);
        }},
        {"TC16", "heartbeat_keeps_measurement_age", [] {
            nav::Engine e; begin(e);
            for (std::uint64_t t = 2000; t <= 6000; t += 1000) e.tick(t);
            state(e, nav::State::WaitFix);
            require(e.snapshot().fix_seq == 1, "tick retains measurement identity");
            require(!e.ingest(fix(2, 2), 5999).accepted, "monotonic reversal rejected");
        }},
        {"TC16", "route_origin_requires_fresh_measurement", [] {
            nav::Engine e;
            require(e.start("nav-A", "phone-A", 1, {"entry", point(100)}, 10000).accepted,
                    "destination confirmed");
            require(!e.ingest(fix(10, 0, 0, 3, 5000), 10001).accepted,
                    "stale origin rejected");
            state(e, nav::State::WaitFix);
            require(!e.pending_request(), "planning awaits valid origin");
            require(e.ingest(fix(11, 1), 11000).accepted, "fresh origin accepted");
            state(e, nav::State::Routing);
            require(e.pending_request()->origin.lng == point(1).lng,
                    "request uses fresh origin coordinates");
        }},
        {"TC02", "phone_identity_and_session_start", [] {
            nav::Engine e; begin(e);
            auto wrong = fix(2, 2); wrong.phone_session_id = "phone-B";
            require(!e.ingest(wrong, 2000).accepted, "foreign phone source rejected");
            state(e, nav::State::Routing);
            require(e.snapshot().session_id == "nav-A", "navigation identity retained");
            auto malformed = fix(2, 1); malformed.position.lat =
                std::numeric_limits<double>::quiet_NaN();
            require(!e.ingest(malformed, 3000).accepted, "nonfinite point rejected");
        }}
    };
}
}
