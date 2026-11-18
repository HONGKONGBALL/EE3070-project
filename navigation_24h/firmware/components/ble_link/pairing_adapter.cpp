#include "pairing_control.hpp"
#include "esp_random.h"
#include "esp_timer.h"
#include <algorithm>
#include <atomic>
#include <memory>
#include <new>
#include <optional>
extern "C" {
#include "host/ble_hs.h"
#include "host/ble_sm.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
}

namespace ble_link {
namespace {
pairing::Policy policy;
PairingObserver observer = nullptr;
void* observer_context = nullptr;
std::atomic<unsigned> queued{0};
std::atomic<bool> ready{false};
ble_npl_callout timer{};
std::string boot_prefix, event_command;
std::uint64_t window_sequence = 0;
bool current_known = false;
std::optional<pairing::Identity> approved_identity;
struct EventLease {
    ble_npl_event* event;
    explicit EventLease(ble_npl_event* value) : event(value) {}
    EventLease(const EventLease&) = delete;
    EventLease& operator=(const EventLease&) = delete;
    ~EventLease() { ble_npl_event_deinit(event); }
};
struct Job {
    ble_npl_event event{};
    PairingCommand command;
    pairing::Identity identity;
    bool identity_valid = false;
};
std::uint64_t now() { return esp_timer_get_time() / 1000; }
bool identity(std::uint16_t handle, std::uint32_t generation, pairing::Identity& result) {
    ble_gap_conn_desc description{};
    if (ble_gap_conn_find(handle, &description) != 0) return false;
    result.epoch = generation; result.handle = handle; result.peer.type = description.peer_ota_addr.type;
    std::copy_n(description.peer_ota_addr.val, 6, result.peer.bytes.begin());
    return true;
}
bool known_peer(std::uint16_t handle) {
    ble_gap_conn_desc description{};
    std::array<ble_addr_t, 4> peers{}; int count = 0;
    if (ble_gap_conn_find(handle, &description) != 0 ||
        ble_store_util_bonded_peers(peers.data(), &count, peers.size()) != 0) return false;
    for (int index = 0; index < count; ++index)
        if (ble_addr_cmp(&peers[index], &description.peer_id_addr) == 0) return true;
    return false;
}
void publish(const std::string& command, bool accepted, const char* reason = nullptr) {
    if (observer) observer({policy.snapshot(), command, accepted,
                            reason ? reason : policy.snapshot().reason}, observer_context);
}
bool same_current(const pairing::Identity& expected) {
    pairing::Identity actual;
    return epoch() == expected.epoch && detail::current_handle() == expected.handle &&
        identity(expected.handle, expected.epoch, actual) && actual == expected;
}
void terminate(const pairing::Identity& expected) {
    if (same_current(expected)) (void)ble_gap_terminate(expected.handle, BLE_ERR_REM_USER_CONN_TERM);
}
void timeout(ble_npl_event*) {
    if (policy.tick(now())) { publish(event_command, false); terminate(policy.snapshot().identity); }
    const auto state = policy.snapshot().state;
    if (state != pairing::State::Closed && state != pairing::State::Authorized)
        (void)ble_npl_callout_reset(&timer, ble_npl_time_ms_to_ticks32(100));
}
void process(ble_npl_event* event) {
    std::unique_ptr<Job> job(static_cast<Job*>(ble_npl_event_get_arg(event)));
    EventLease release(event);
    --queued;
    const auto& command = job->command;
    if (policy.tick(now())) { publish(event_command, false); terminate(policy.snapshot().identity); }
    switch (command.action) {
    case PairingAction::Open: {
        const auto id = boot_prefix + "-" + std::to_string(++window_sequence);
        const bool accepted = detail::current_handle() == BLE_HS_CONN_HANDLE_NONE && policy.open(id, now());
        if (accepted) {
            event_command = command.command_id;
            (void)ble_npl_callout_reset(&timer, ble_npl_time_ms_to_ticks32(100));
        }
        publish(command.command_id, accepted, accepted ? nullptr : "PAIRING_BUSY");
        break;
    }
    case PairingAction::Status:
        publish(command.command_id, true); break;
    case PairingAction::Close: {
        const auto previous = policy.snapshot();
        policy.close(); event_command = command.command_id; publish(command.command_id, true);
        if (!current_known && !approved_identity) terminate(previous.identity);
        break;
    }
    case PairingAction::Confirm:
    case PairingAction::Reject: {
        const bool approval = command.action == PairingAction::Confirm;
        const auto previous = policy.snapshot().state;
        const bool accepted = job->identity_valid && command.connection_epoch == job->identity.epoch && same_current(job->identity) &&
            (approval ? policy.confirm(command.window_id, job->identity, command.number, now()) :
                        policy.reject(command.window_id, job->identity, command.number, now()));
        if (!accepted) {
            if (previous != pairing::State::Closed && policy.snapshot().state == pairing::State::Closed)
                terminate(policy.snapshot().identity);
            publish(command.command_id, false, "PAIRING_CONTEXT"); break;
        }
        event_command = command.command_id; publish(command.command_id, true);
        ble_sm_io io{}; io.action = BLE_SM_IOACT_NUMCMP; io.numcmp_accept = approval ? 1 : 0;
        const auto result = ble_sm_inject_io(job->identity.handle, &io);
        if (result != 0) { policy.close("PAIRING_SECURITY_ERROR"); publish(command.command_id, false); }
        if (!approval || result != 0) terminate(job->identity);
        break;
    }
    }
}
}
void observe_pairing(PairingObserver value, void* context) { observer = value; observer_context = context; }
bool submit_pairing(const PairingCommand& command) {
    if (!ready || command.command_id.empty() || command.command_id.size() > 96 || command.window_id.size() > 96)
        return false;
    auto job = std::unique_ptr<Job>(new (std::nothrow) Job);
    if (!job) return false;
    job->command = command;
    if (command.action == PairingAction::Confirm || command.action == PairingAction::Reject) {
        const auto generation = epoch(); const auto handle = detail::current_handle();
        job->identity_valid = generation == command.connection_epoch &&
            identity(handle, generation, job->identity) && epoch() == generation;
    }
    if (queued.fetch_add(1) >= 4) { --queued; return false; }
    ble_npl_event_init(&job->event, process, job.get());
    ble_npl_eventq_put(nimble_port_get_dflt_eventq(), &job->event);
    (void)job.release(); return true;
}
namespace pairing_adapter {
void initialize() {
    std::array<std::uint8_t, 16> random{}; esp_fill_random(random.data(), random.size());
    const char hex[] = "0123456789abcdef";
    for (const auto byte : random) { boot_prefix += hex[byte >> 4]; boot_prefix += hex[byte & 15]; }
    ready = ble_npl_callout_init(&timer, nimble_port_get_dflt_eventq(), timeout, nullptr) == 0;
}
Admission connected(std::uint16_t handle, std::uint32_t generation) {
    approved_identity.reset();
    current_known = known_peer(handle);
    if (current_known) { policy.close("KNOWN_PEER_CONNECTED"); return Admission::Known; }
    pairing::Identity current;
    if (!identity(handle, generation, current) || !policy.candidate(current, now())) return Admission::Denied;
    publish(event_command, true); return Admission::Candidate;
}
void disconnected(std::uint16_t handle, std::uint32_t generation) {
    const auto expected = policy.snapshot().identity;
    if (expected.handle == handle && expected.epoch == generation) policy.disconnect(expected);
    current_known = false; approved_identity.reset(); publish(event_command, false, "PAIRING_DISCONNECTED");
}
bool encrypted(std::uint16_t handle, std::uint32_t generation, bool success) {
    ble_gap_conn_desc description{}; pairing::Identity current;
    if (!success || ble_gap_conn_find(handle, &description) != 0 || !identity(handle, generation, current)) {
        approved_identity.reset(); policy.close("PAIRING_SECURITY_ERROR"); publish(event_command, false); return false;
    }
    const auto& security = description.sec_state;
    if (current_known) return security.encrypted && known_peer(handle);
    const bool stored_bond = security.bonded && known_peer(handle);
    const bool accepted = approved_identity ? current == *approved_identity && same_current(current) &&
        security.encrypted && security.authenticated && stored_bond :
        policy.security(current, security.encrypted, security.authenticated, stored_bond, now());
    if (accepted) approved_identity = current;
    else { approved_identity.reset(); policy.close("PAIRING_SECURITY_ERROR"); }
    publish(event_command, accepted);
    return accepted;
}
void comparison(std::uint16_t handle, std::uint32_t generation, std::uint8_t action, std::uint32_t number) {
    pairing::Identity current;
    if (action != BLE_SM_IOACT_NUMCMP || !identity(handle, generation, current) ||
        !policy.challenge(current, number, now())) {
        policy.close("PAIRING_ACTION_REQUIRED"); publish(event_command, false);
        if (identity(handle, generation, current)) terminate(current);
        return;
    }
    publish(event_command, true);
    (void)ble_npl_callout_reset(&timer, ble_npl_time_ms_to_ticks32(100));
}
void reset() {
    current_known = false; approved_identity.reset();
    policy.close("PAIRING_HOST_RESET"); publish(event_command, false);
}
}
}
