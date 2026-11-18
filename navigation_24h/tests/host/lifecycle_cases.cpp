#include "test_support.hpp"

namespace test {
std::vector<Case> lifecycle_cases() {
    return {
        {"TC33", "cancel_while_routing", [] {
            nav::Engine e; begin(e); auto token = *e.pending_request();
            const auto cancel = command(nav::CommandKind::Cancel, e);
            auto ack = e.command(cancel, 2000);
            require(ack.accepted, "cancel acknowledged"); state(e, nav::State::Idle);
            require(e.snapshot().generation == 2, "cancel advances generation");
            require(!e.commit_route(token, straight(), 2001), "late candidate invalidated");
            require(e.drain_events(2001).empty(), "canceled events revoked");
            const auto repeated = e.command(cancel, 2002);
            require(repeated.accepted && repeated.duplicate,
                    "cancel retransmission retains original acknowledgment");
        }},
        {"TC34", "late_response_after_new_session", [] {
            nav::Engine e; begin(e); auto old = *e.pending_request();
            require(e.command(command(nav::CommandKind::Cancel, e), 1100).accepted,
                    "first session canceled");
            require(e.start("nav-B", "phone-A", 1, {"new", point(100)}, 1200).accepted,
                    "new session started");
            require(e.ingest(fix(1, 0), 1300).accepted, "new session fix accepted");
            require(!e.commit_route(old, straight(), 1400), "old request discarded");
            require(e.snapshot().session_id == "nav-B", "current identity retained");
        }},
        {"TC35", "event_generation_and_ttl", [] {
            nav::Engine e; activate(e, straight());
            auto events = e.drain_events(2999); require(!events.empty(), "fresh event delivered");
            require(e.event_valid(events.front(), 2999), "event valid before TTL");
            require(!e.event_valid(events.front(), 3000), "event expired at TTL boundary");
            require(e.command(command(nav::CommandKind::Cancel, e), 3001).accepted,
                    "cancel acknowledged");
            require(!e.event_valid(events.front(), 3001), "old generation invalidated");
        }},
        {"TC36", "pause_resume_requires_new_measurement", [] {
            nav::Engine e; activate(e, straight());
            require(e.command(command(nav::CommandKind::Pause, e), 1200).accepted,
                    "pause acknowledged"); state(e, nav::State::Paused);
            require(e.command(command(nav::CommandKind::Resume, e), 1300).accepted,
                    "resume acknowledged"); state(e, nav::State::WaitFix);
            require(e.ingest(fix(2, 2), 2000).accepted, "fresh resume fix");
            state(e, nav::State::Follow);
        }},
        {"TC36", "late_network_failure_preserves_pause", [] {
            nav::Engine e; begin(e); auto token = *e.pending_request();
            require(e.command(command(nav::CommandKind::Pause, e), 1200).accepted,
                    "pause accepted during planning");
            e.fail_request(token, "SERVICE_TIMEOUT", 1300);
            state(e, nav::State::Paused);
        }},
        {"TC36", "link_recovery_preserves_user_pause", [] {
            nav::Engine e; activate(e, straight());
            require(e.command(command(nav::CommandKind::Pause, e), 1200).accepted,
                    "user pause accepted");
            e.set_link(false, 1300); state(e, nav::State::Paused);
            e.set_link(true, 1400); state(e, nav::State::Paused);
        }},
        {"TC37", "integration_permission_lease", [] {
            nav::Engine e; activate(e, straight()); e.set_integration(true, 1000);
            auto permit = command(nav::CommandKind::Permission, e);
            require(e.command(permit, 1001).accepted, "lease granted");
            require(e.command(command(nav::CommandKind::Resume, e), 1002).accepted,
                    "resume requested");
            require(e.ingest(fix(2, 2), 2000).accepted, "resume fresh fix");
            state(e, nav::State::Follow);
            e.tick(4001); state(e, nav::State::Paused);
            require(e.drain_events(4001).empty(), "expired permission suppresses action output");
        }},
        {"TC37", "renewed_lease_requires_explicit_resume", [] {
            nav::Engine e; activate(e, straight()); e.set_integration(true, 1000);
            require(e.command(command(nav::CommandKind::Permission, e), 1100).accepted,
                    "lease renewed");
            require(e.ingest(fix(2, 1), 2000).accepted, "fresh location accepted while paused");
            state(e, nav::State::Paused);
            require(e.command(command(nav::CommandKind::Resume, e), 2100).accepted,
                    "explicit resume accepted");
            require(e.ingest(fix(3, 2), 3000).accepted, "post-resume measurement accepted");
            state(e, nav::State::Follow);
        }},
        {"TC40", "link_recovery_freshness_fixture", [] {
            nav::Engine e; activate(e, straight());
            e.set_link(false, 2000); state(e, nav::State::WaitFix);
            e.set_link(true, 2100); state(e, nav::State::WaitFix);
            require(!e.ingest(fix(1, 0), 2200).accepted, "reconnect cached measurement rejected");
            require(e.ingest(fix(2, 2), 3000).accepted, "reconnect fresh fix accepted");
            state(e, nav::State::Follow);
        }},
        {"TC40", "unchanged_link_preserves_navigation_state", [] {
            nav::Engine e; activate(e, straight());
            const auto current_event = e.snapshot().current_event_id;
            e.set_link(true, 1100); state(e, nav::State::Follow);
            require(e.snapshot().current_event_id == current_event,
                    "unchanged link preserves current instruction");
        }},
        {"TC44", "command_identity_and_deduplication", [] {
            nav::Engine e; activate(e, straight());
            auto pause = command(nav::CommandKind::Pause, e);
            auto first = e.command(pause, 1200); auto second = e.command(pause, 1300);
            require(first.accepted && second.accepted && second.duplicate,
                    "retransmission returns cached acknowledgment");
            auto reused = pause; reused.kind = nav::CommandKind::Resume;
            require(!e.command(reused, 1350).accepted,
                    "command ID binds to original payload");
            auto foreign = pause; foreign.command_id = "cmd-foreign";
            foreign.session_id = "nav-B";
            require(!e.command(foreign, 1400).accepted, "foreign session command discarded");
        }}
    };
}
}
