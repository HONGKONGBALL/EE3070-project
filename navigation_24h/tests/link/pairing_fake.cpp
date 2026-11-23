#include "pairing_fake.hpp"
extern "C" {
#include "pairing_sdk.hpp"
}
#include <algorithm>
#include <cstring>
#include <deque>
#include <stdexcept>
namespace {
std::uint16_t handle = BLE_HS_CONN_HANDLE_NONE;
std::uint32_t generation = 1;
ble_gap_conn_desc description{};
bool bonded_peer = false;
std::uint64_t milliseconds = 1000;
std::uint64_t after_clock_read_jump = 0;
std::deque<ble_npl_event*> events;
ble_npl_eventq queue;
ble_npl_callout* active_timer = nullptr;
void notice(const ble_link::PairingStatus& value, void*) { pairing_test::notices.push_back(value); }
}
namespace pairing_test {
std::vector<ble_link::PairingStatus> notices;
unsigned accepted_injections = 0, rejected_injections = 0, terminations = 0;
unsigned event_inits = 0, event_deinits = 0, live_events = 0;
void drain() { while (!events.empty()) { auto* event = events.front(); events.pop_front(); event->callback(event); } }
void initialize() { ble_link::observe_pairing(notice, nullptr); ble_link::pairing_adapter::initialize(); }
void reset() {
    drain(); handle = BLE_HS_CONN_HANDLE_NONE; ++generation; milliseconds = 1000;
    after_clock_read_jump = 0;
    ble_link::pairing_adapter::reset(); notices.clear(); bonded_peer = false;
    accepted_injections = rejected_injections = terminations = 0;
    if (active_timer) active_timer->due = 0;
}
void connect(bool known) {
    handle = 7; ++generation; bonded_peer = known;
    description = {{0, {1,2,3,4,5,6}}, {0, {1,2,3,4,5,6}}, {false,false,false}};
}
void disconnect() {
    ble_link::pairing_adapter::disconnected(handle, generation);
    handle = BLE_HS_CONN_HANDLE_NONE; ++generation;
}
void secure(bool encrypted, bool authenticated, bool bonded) {
    description.sec_state = {encrypted, authenticated, bonded}; bonded_peer = bonded;
}
void change_peer() { ++description.peer_ota_addr.val[0]; }
void jump_after_clock_read(std::uint64_t value) { after_clock_read_jump = value; }
void advance(std::uint64_t value, bool run_timer) {
    milliseconds += value;
    if (run_timer && active_timer && active_timer->due && milliseconds >= active_timer->due) {
        active_timer->due = 0; active_timer->event.callback(&active_timer->event);
    }
}
}
namespace ble_link {
std::uint32_t epoch() { return generation; }
namespace detail { std::uint16_t current_handle() { return handle; } }
}
void esp_fill_random(void* bytes, std::size_t size) { std::memset(bytes, 0xa5, size); }
std::int64_t esp_timer_get_time() {
    const auto result = static_cast<std::int64_t>(milliseconds * 1000);
    milliseconds += after_clock_read_jump; after_clock_read_jump = 0; return result;
}
extern "C" {
int ble_gap_conn_find(std::uint16_t value, ble_gap_conn_desc* output) {
    if (value != handle || handle == BLE_HS_CONN_HANDLE_NONE) return 1;
    *output = description; return 0;
}
int ble_store_util_bonded_peers(ble_addr_t* peers, int* count, int maximum) {
    *count = bonded_peer && maximum > 0 ? 1 : 0;
    if (*count) peers[0] = description.peer_id_addr;
    return 0;
}
int ble_addr_cmp(const ble_addr_t* first, const ble_addr_t* second) { return std::memcmp(first, second, sizeof(*first)); }
int ble_gap_terminate(std::uint16_t, int) { ++pairing_test::terminations; return 0; }
int ble_sm_inject_io(std::uint16_t, ble_sm_io* io) {
    if (io->numcmp_accept) ++pairing_test::accepted_injections; else ++pairing_test::rejected_injections;
    return 0;
}
void ble_npl_event_init(ble_npl_event* event, void (*callback)(ble_npl_event*), void* argument) {
    if (!event->allocated) {
        if (pairing_test::live_events == pairing_test::event_pool_capacity)
            throw std::runtime_error("finite NPL event pool exhausted");
        ++pairing_test::live_events; ++pairing_test::event_inits;
    }
    *event = {callback, argument, true};
}
void ble_npl_event_deinit(ble_npl_event* event) {
    if (!event->allocated) return;
    event->allocated = false; --pairing_test::live_events; ++pairing_test::event_deinits;
}
void* ble_npl_event_get_arg(ble_npl_event* event) { return event->argument; }
void ble_npl_eventq_put(ble_npl_eventq*, ble_npl_event* event) { events.push_back(event); }
ble_npl_eventq* nimble_port_get_dflt_eventq() { return &queue; }
int ble_npl_callout_init(ble_npl_callout* timer, ble_npl_eventq*, void (*callback)(ble_npl_event*), void* argument) {
    ble_npl_event_init(&timer->event, callback, argument); timer->due = 0; active_timer = timer; return 0;
}
int ble_npl_callout_reset(ble_npl_callout* timer, std::uint32_t ticks) { timer->due = milliseconds + ticks; return 0; }
std::uint32_t ble_npl_time_ms_to_ticks32(std::uint32_t value) { return value; }
}
