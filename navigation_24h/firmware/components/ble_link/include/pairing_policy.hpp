#pragma once
#include <array>
#include <cstdint>
#include <optional>
#include <string>

namespace ble_link::pairing {
enum class State { Closed, Armed, Candidate, Comparing, Approved, Authorized };
const char* name(State state);
struct Peer {
    std::uint8_t type = 0;
    std::array<std::uint8_t, 6> bytes{};
    bool operator==(const Peer& other) const { return type == other.type && bytes == other.bytes; }
};
struct Identity {
    std::uint32_t epoch = 0;
    std::uint16_t handle = 0;
    Peer peer;
    bool operator==(const Identity& other) const {
        return epoch == other.epoch && handle == other.handle && peer == other.peer;
    }
};
struct Snapshot {
    State state = State::Closed;
    std::string window_id;
    Identity identity;
    std::optional<std::uint32_t> number;
    std::uint64_t deadline_ms = 0;
    const char* reason = "PAIRING_CLOSED";
};
class Policy {
public:
    bool open(const std::string& window_id, std::uint64_t now);
    bool candidate(const Identity& identity, std::uint64_t now);
    bool challenge(const Identity& identity, std::uint32_t number, std::uint64_t now);
    bool confirm(const std::string& window_id, const Identity& identity, std::uint32_t number, std::uint64_t now);
    bool reject(const std::string& window_id, const Identity& identity, std::uint32_t number, std::uint64_t now);
    bool security(const Identity& identity, bool encrypted, bool authenticated, bool bonded, std::uint64_t now);
    bool tick(std::uint64_t now);
    void disconnect(const Identity& identity);
    void close(const char* reason = "PAIRING_CLOSED");
    const Snapshot& snapshot() const { return snapshot_; }
private:
    bool matches(const std::string& window_id, const Identity& identity, std::uint32_t number, std::uint64_t now);
    Snapshot snapshot_;
    std::uint64_t arm_deadline_ = 0;
};
}
