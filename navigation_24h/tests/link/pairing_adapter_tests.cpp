#include "pairing_fake.hpp"
#include <functional>
#include <iostream>
#include <stdexcept>
namespace {
using ble_link::PairingAction;
using ble_link::PairingCommand;
using ble_link::pairing::State;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
void submit(PairingCommand command) { require(ble_link::submit_pairing(command), "command queued"); pairing_test::drain(); }
PairingCommand comparison() {
    submit({PairingAction::Open, "usb-open", {}, 0, 0});
    require(pairing_test::notices.back().snapshot.state == State::Armed, "window armed on host queue");
    pairing_test::connect();
    require(ble_link::pairing_adapter::connected(7, ble_link::epoch()) == ble_link::pairing_adapter::Admission::Candidate,
            "first candidate consumes armed window");
    ble_link::pairing_adapter::comparison(7, ble_link::epoch(), 4, 123456);
    const auto& challenge = pairing_test::notices.back().snapshot;
    require(challenge.state == State::Comparing, "numeric comparison emitted");
    return {PairingAction::Confirm, "usb-confirm", challenge.window_id, ble_link::epoch(), 123456};
}
void authorized_closed_window() {
    pairing_test::reset(); submit(comparison()); pairing_test::secure(true, true, true);
    require(ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "approved epoch authorized");
    submit({PairingAction::Close, "usb-close", {}, 0, 0});
}
struct Case { const char* name; std::function<void()> run; };
}
int main(int argc, char** argv) {
    const std::string requested = argc == 3 && std::string(argv[1]) == "--fixture" ? argv[2] : "";
    pairing_test::initialize();
    const std::vector<Case> cases{
        {"queued_decision_copies_payload_and_waits_for_security", [] {
            pairing_test::reset(); auto approval = comparison();
            require(ble_link::submit_pairing(approval), "decision enqueued");
            approval.number = 999999; approval.window_id = "modified-after-submit";
            require(pairing_test::accepted_injections == 0, "injection belongs to host processing");
            pairing_test::drain();
            require(pairing_test::accepted_injections == 1 && pairing_test::notices.back().snapshot.state == State::Approved,
                    "queued payload retains original identity and number");
            pairing_test::secure(true, true, true);
            require(ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "authenticated bonded encryption completes grant");
            require(pairing_test::notices.back().snapshot.state == State::Authorized, "authorization follows security event");
        }},
        {"disconnect_rechecks_queued_epoch", [] {
            pairing_test::reset(); const auto approval = comparison();
            require(ble_link::submit_pairing(approval), "old decision queued");
            pairing_test::disconnect(); pairing_test::connect();
            pairing_test::drain();
            require(pairing_test::accepted_injections == 0, "reused handle retains current epoch boundary");
        }},
        {"stale_decision_receives_correlated_context_outcome", [] {
            pairing_test::reset(); auto approval = comparison(); --approval.connection_epoch;
            submit(approval);
            const auto& result = pairing_test::notices.back();
            require(!result.accepted && result.command_id == "usb-confirm" && result.reason == "PAIRING_CONTEXT" &&
                    pairing_test::accepted_injections == 0, "stale context receives correlated host disposition");
        }},
        {"queued_decision_rechecks_peer_and_deadline", [] {
            pairing_test::reset(); auto approval = comparison();
            require(ble_link::submit_pairing(approval), "decision queued before peer change");
            pairing_test::change_peer(); pairing_test::drain();
            require(pairing_test::accepted_injections == 0, "queued decision binds copied peer identity");
            pairing_test::reset(); approval = comparison();
            require(ble_link::submit_pairing(approval), "decision queued before deadline");
            pairing_test::advance(25000, false); pairing_test::drain();
            require(pairing_test::accepted_injections == 0 && pairing_test::terminations == 1,
                    "host processing checks expiry before injection");
        }},
        {"deadline_crosses_during_confirmation_processing", [] {
            pairing_test::reset(); const auto approval = comparison();
            require(ble_link::submit_pairing(approval), "decision queued at live deadline");
            pairing_test::jump_after_clock_read(25000); pairing_test::drain();
            require(pairing_test::accepted_injections == 0 && pairing_test::terminations == 1 &&
                    pairing_test::notices.back().snapshot.state == State::Closed,
                    "expiry during matching closes and terminates candidate");
        }},
        {"negotiated_action_requires_numeric_comparison", [] {
            pairing_test::reset(); (void)comparison();
            ble_link::pairing_adapter::comparison(7, ble_link::epoch(), 3, 0);
            require(pairing_test::accepted_injections == 0 && pairing_test::terminations == 1 &&
                    pairing_test::notices.back().snapshot.state == State::Closed,
                    "numeric-comparison action governs candidate authorization");
        }},
        {"timeout_and_rejection_close_candidate", [] {
            pairing_test::reset(); auto approval = comparison();
            pairing_test::advance(25000);
            require(pairing_test::notices.back().snapshot.state == State::Closed && pairing_test::terminations == 1,
                    "comparison timer closes candidate");
            require(pairing_test::accepted_injections == 0, "timeout keeps approval count zero");
            pairing_test::reset(); approval = comparison(); approval.action = PairingAction::Reject;
            submit(approval);
            require(pairing_test::rejected_injections == 1 && pairing_test::accepted_injections == 0 &&
                    pairing_test::notices.back().snapshot.state == State::Closed, "explicit rejection closes candidate");
        }},
        {"authenticated_flag_participates_in_new_peer_grant", [] {
            pairing_test::reset(); const auto approval = comparison(); submit(approval);
            pairing_test::secure(true, false, true);
            require(!ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "new peer security requires authentication");
            require(pairing_test::notices.back().snapshot.state == State::Closed, "security outcome closes pending grant");
        }},
        {"known_peer_restores_only_after_encryption", [] {
            pairing_test::reset(); pairing_test::connect(true);
            require(ble_link::pairing_adapter::connected(7, ble_link::epoch()) == ble_link::pairing_adapter::Admission::Known,
                    "stored peer identity recognized");
            require(!ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "stored identity awaits encryption");
            pairing_test::secure(true, false, true);
            require(ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "stored bond restores encrypted link");
        }},
        {"authorized_epoch_survives_window_close_with_strong_security", [] {
            pairing_test::reset(); submit(comparison()); pairing_test::secure(true, true, true);
            require(ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "new peer authorized");
            submit({PairingAction::Close, "usb-close", {}, 0, 0});
            require(pairing_test::terminations == 0, "window close retains authorized connection");
            require(ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "security refresh retains approved epoch after close");
            submit({PairingAction::Close, "usb-close-again", {}, 0, 0});
            require(pairing_test::terminations == 0, "repeated close retains authorized connection");
            pairing_test::secure(true, false, true);
            require(!ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "approved epoch continues checking authentication");
        }},
        {"status_jobs_release_finite_event_pool", [] {
            pairing_test::reset();
            const auto baseline_live = pairing_test::live_events;
            const auto before_init = pairing_test::event_inits, before_deinit = pairing_test::event_deinits;
            for (unsigned index = 0; index < 128; ++index)
                submit({PairingAction::Status, "status-" + std::to_string(index), {}, 0, 0});
            require(pairing_test::event_inits - before_init == 128 && pairing_test::event_deinits - before_deinit == 128 &&
                    pairing_test::live_events == baseline_live, "each queued command returns its NPL block");
            require(128 > pairing_test::event_pool_capacity, "status sequence exceeds finite pool size");
        }},
        {"approved_epoch_ends_on_disconnect", [] {
            authorized_closed_window(); pairing_test::disconnect(); pairing_test::connect();
            require(ble_link::pairing_adapter::connected(7, ble_link::epoch()) == ble_link::pairing_adapter::Admission::Denied,
                    "new connection starts with a closed window");
            pairing_test::secure(true, true, true);
            require(!ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "new epoch has independent approval identity");
        }},
        {"approved_epoch_ends_on_host_reset", [] {
            authorized_closed_window(); ble_link::pairing_adapter::reset();
            require(!ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "host reset clears the approved identity");
        }},
        {"approved_epoch_checks_peer_after_window_close", [] {
            authorized_closed_window(); pairing_test::change_peer();
            require(!ble_link::pairing_adapter::encrypted(7, ble_link::epoch(), true), "security refresh binds exact approved peer");
        }}
    };
    unsigned failed = 0;
    for (const auto& item : cases) {
        if (!requested.empty() && requested != item.name) continue;
        try { item.run(); std::cout << item.name << " passed\n"; }
        catch (const std::runtime_error& error) { ++failed; std::cout << item.name << ": " << error.what() << '\n'; }
    }
    return failed ? 1 : 0;
}
