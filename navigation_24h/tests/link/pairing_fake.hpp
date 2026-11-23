#pragma once
#include "pairing_control.hpp"
#include <vector>
namespace pairing_test {
extern std::vector<ble_link::PairingStatus> notices;
extern unsigned accepted_injections, rejected_injections, terminations;
extern unsigned event_inits, event_deinits, live_events;
inline constexpr unsigned event_pool_capacity = 74;
void initialize();
void reset();
void drain();
void connect(bool known = false);
void disconnect();
void secure(bool encrypted, bool authenticated, bool bonded);
void advance(std::uint64_t milliseconds, bool run_timer = true);
void change_peer();
void jump_after_clock_read(std::uint64_t milliseconds);
}
