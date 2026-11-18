#pragma once
#include "app_runtime.hpp"
#include <vector>
namespace wire_test {
extern nav::Millis now;
extern std::vector<std::string> outputs;
extern std::vector<ble_link::PairingCommand> pairing_requests;
extern std::uint32_t connection_epoch;
extern bool link_authorized;
extern unsigned ble_outputs;
void reset();
}
