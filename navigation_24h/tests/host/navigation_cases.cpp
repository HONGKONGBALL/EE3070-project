#include "test_support.hpp"

namespace test {
namespace {
nav::Route corner(bool crossing = false) {
    return {{point(0), point(50), point(50, 50), point(100, 50)},
            {{1, "left", crossing}, {3, "arrive", false}}, "WGS84"};
}
void move(nav::Engine& e, std::uint64_t sequence, double east, double north,
          std::uint64_t now, double course = 90) {
    auto f = fix(sequence, east, north); f.course_deg = course;
    require(e.ingest(f, now).accepted, "independent trajectory measurement accepted");
}
}
std::vector<Case> navigation_cases() {
    return {
        {"TC28", "manual_turn_complete_state_sequence", [] {
            nav::Engine e; activate(e, corner());
            move(e, 2, 25, 0, 6000); state(e, nav::State::Approach);
            move(e, 3, 42, 0, 10000); state(e, nav::State::AlignConfirm);
            require(e.command(command(nav::CommandKind::ConfirmTurn, e), 10001).accepted,
                    "current maneuver manual confirmation accepted"); state(e, nav::State::Turn);
            move(e, 4, 50, 10, 13000, 0); state(e, nav::State::Verify);
            move(e, 5, 50, 16, 14000, 0); state(e, nav::State::Follow);
        }},
        {"TC30", "turn_movement_evidence_and_repeat", [] {
            nav::Engine e; activate(e, corner());
            move(e, 2, 25, 0, 6000); move(e, 3, 42, 0, 10000);
            move(e, 4, 50, 10, 13000, 0); state(e, nav::State::Turn);
            auto events = e.drain_events(13001);
            require(!events.empty() && events.back().confirmation_source == "valid_movement_evidence",
                    "movement confirmation source recorded");
            require(e.command(command(nav::CommandKind::Repeat, e), 13002).accepted,
                    "repeat current fresh instruction");
            move(e, 5, 50, 16, 14000, 0); state(e, nav::State::Verify);
            move(e, 6, 50, 22, 15000, 0); state(e, nav::State::Follow);
        }},
        {"TC30", "completed_turn_revokes_previous_maneuver", [] {
            nav::Engine e; activate(e, corner());
            move(e, 2, 25, 0, 6000); move(e, 3, 42, 0, 10000);
            require(e.command(command(nav::CommandKind::ConfirmTurn, e), 10001).accepted,
                    "current turn confirmed");
            move(e, 4, 50, 10, 13000, 0); auto previous = e.drain_events(13001);
            require(!previous.empty(), "verification prompt captured");
            move(e, 5, 50, 16, 14000, 0); state(e, nav::State::Follow);
            require(!e.event_valid(previous.back(), 14001),
                    "completed maneuver event revoked before original TTL");
            require(!e.command(command(nav::CommandKind::Repeat, e), 14002).accepted,
                    "repeat requires a current instruction");
        }},
        {"TC58", "crossing_manual_handoff", [] {
            nav::Engine e; activate(e, corner(true));
            move(e, 2, 25, 0, 6000); move(e, 3, 42, 0, 10000);
            state(e, nav::State::CrossingWait);
            auto wrong = command(nav::CommandKind::ConfirmTurn, e);
            require(!e.command(wrong, 10001).accepted, "crossing uses crossing confirmation");
            require(e.command(command(nav::CommandKind::ConfirmCrossing, e), 10002).accepted,
                    "crossing handoff accepted"); state(e, nav::State::Turn);
        }},
        {"TC58", "crossing_pause_recomputes_confirmation", [] {
            nav::Engine e; activate(e, corner(true));
            move(e, 2, 25, 0, 6000); move(e, 3, 42, 0, 10000);
            state(e, nav::State::CrossingWait);
            require(e.command(command(nav::CommandKind::Pause, e), 10001).accepted,
                    "crossing paused");
            require(e.command(command(nav::CommandKind::Resume, e), 10002).accepted,
                    "crossing resume requested");
            move(e, 4, 43, 0, 11000); state(e, nav::State::CrossingWait);
            require(e.snapshot().current_event_id > 0, "fresh crossing prompt issued");
        }},
        {"TC58", "crossing_confirmation_precedes_nearby_entrance", [] {
            nav::Route short_crossing{{point(0), point(50), point(50, 10)},
                {{1, "crossing", true}, {2, "arrive", false}}, "WGS84"};
            nav::Engine e; activate(e, short_crossing);
            move(e, 2, 25, 0, 6000); move(e, 3, 42, 0, 10000);
            state(e, nav::State::CrossingWait);
            move(e, 4, 50, 5, 13000, 0); move(e, 5, 50, 7, 14000, 0);
            move(e, 6, 50, 9, 15000, 0); state(e, nav::State::CrossingWait);
            require(e.command(command(nav::CommandKind::ConfirmCrossing, e), 15001).accepted,
                    "pending crossing confirmation accepted");
        }},
        {"TC31", "independent_offroute_samples_and_cooldown", [] {
            nav::Engine e; activate(e, straight());
            move(e, 2, 0, 10, 3000); move(e, 3, 0, 20, 5000);
            move(e, 4, 0, 30, 7000); move(e, 5, 0, 35, 8500);
            require(!e.pending_request(), "two offroute measurements retain route");
            move(e, 6, 0, 40, 10000); state(e, nav::State::Rerouting);
            auto token = *e.pending_request();
            require(!e.ingest(fix(6, 0, 40), 10001).accepted, "duplicate cannot confirm deviation");
            require(e.pending_request()->request_id == token.request_id,
                    "active reroute request coalesced");
            e.fail_request(token, "SERVICE_TIMEOUT", 10002);
            const auto events = e.drain_events(10002);
            require(events.size() == 1 && events.front().type == "fault" &&
                    events.front().reason == "SERVICE_TIMEOUT", "route fault carries its cause");
            move(e, 7, 0, 45, 12000);
            require(!e.pending_request(), "cooldown holds retry");
        }},
        {"TC32", "candidate_commit_preserves_active_route", [] {
            nav::Engine e; activate(e, straight());
            auto old_version = e.snapshot().route_version;
            nav::RequestToken foreign{"nav-A", 1, 999, point(0), {"entry", point(100)}};
            auto malformed = straight(); malformed.points.resize(2049);
            require(!e.commit_route(foreign, malformed, 2000), "unqualified response discarded");
            require(e.snapshot().route_version == old_version && e.active_route().points.size() == 3,
                    "active route and version retained");
        }},
        {"TC38", "arrival_requires_final_segment_three_fixes", [] {
            nav::Engine e; activate(e, straight());
            move(e, 2, 40, 0, 11000); move(e, 3, 80, 0, 21000);
            move(e, 4, 90, 0, 24000); state(e, nav::State::Follow);
            require(!e.ingest(fix(4, 90), 24001).accepted, "duplicate near fix discarded");
            move(e, 5, 92, 0, 25000); state(e, nav::State::Follow);
            move(e, 6, 94, 0, 26000); state(e, nav::State::ArrivalNear);
            require(e.command(command(nav::CommandKind::ConfirmArrival, e), 26001).accepted,
                    "entrance confirmation accepted"); state(e, nav::State::Arrived);
        }},
        {"TC38", "isolated_destination_drift_preserves_session", [] {
            nav::Engine e; activate(e, straight());
            require(!e.ingest(fix(2, 99), 2000).accepted, "isolated destination jump rejected");
            require(e.snapshot().state != nav::State::ArrivalNear &&
                    e.snapshot().state != nav::State::Arrived,
                    "drift cannot complete navigation");
            require(e.active_route().points.size() == 3, "active route preserved");
            move(e, 3, 2, 0, 3000); state(e, nav::State::Follow);
        }},
        {"TC39", "arrival_precision_and_entrance_confirmation_fixture", [] {
            nav::Engine e; activate(e, straight());
            move(e, 2, 40, 0, 11000); move(e, 3, 80, 0, 21000);
            auto f = fix(4, 90, 0, 12);
            require(e.ingest(f, 24000).accepted, "follow precision accepted");
            state(e, nav::State::Follow);
            move(e, 5, 91, 0, 25000); move(e, 6, 93, 0, 26000);
            state(e, nav::State::Follow); move(e, 7, 95, 0, 27000);
            state(e, nav::State::ArrivalNear);
            auto stale = command(nav::CommandKind::ConfirmArrival, e); stale.route_version = 0;
            require(!e.command(stale, 27001).accepted, "confirmation checks route version");
        }},
        {"TC39", "arrival_prompt_refreshes_on_independent_fix", [] {
            nav::Engine e; activate(e, straight());
            move(e, 2, 40, 0, 11000); move(e, 3, 80, 0, 21000);
            move(e, 4, 90, 0, 24000); move(e, 5, 92, 0, 25000);
            move(e, 6, 94, 0, 26000); const auto old = e.snapshot().current_event_id;
            move(e, 7, 95, 0, 29000);
            require(e.snapshot().current_event_id > old, "fresh measurement refreshes expired prompt");
            require(e.command(command(nav::CommandKind::ConfirmArrival, e), 29001).accepted,
                    "current entrance confirmation accepted");
        }}
    };
}
}
