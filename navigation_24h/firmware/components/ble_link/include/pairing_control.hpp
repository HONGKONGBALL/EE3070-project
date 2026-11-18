#pragma once
#include "pairing_policy.hpp"
#include <string>

namespace ble_link {
enum class PairingAction { Open, Confirm, Reject, Status, Close };
struct PairingCommand {
    PairingAction action = PairingAction::Status;
    std::string command_id;
    std::string window_id;
    std::uint32_t connection_epoch = 0;
    std::uint32_t number = 0;
};
struct PairingStatus {
    pairing::Snapshot snapshot;
    std::string command_id;
    bool accepted = false;
    std::string reason;
};
using PairingObserver = void (*)(const PairingStatus&, void*);
void observe_pairing(PairingObserver observer, void* context);
bool submit_pairing(const PairingCommand& command);
std::uint32_t epoch();
bool authorized_epoch(std::uint32_t value);
namespace pairing_adapter {
enum class Admission { Denied, Known, Candidate };
void initialize();
Admission connected(std::uint16_t handle, std::uint32_t epoch);
void disconnected(std::uint16_t handle, std::uint32_t epoch);
bool encrypted(std::uint16_t handle, std::uint32_t epoch, bool success);
void comparison(std::uint16_t handle, std::uint32_t epoch, std::uint8_t action, std::uint32_t number);
void reset();
}
namespace detail {
std::uint16_t current_handle();
}
}
