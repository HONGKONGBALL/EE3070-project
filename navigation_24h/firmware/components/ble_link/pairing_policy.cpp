#include "pairing_policy.hpp"
#include <algorithm>
#include <limits>
namespace ble_link::pairing {
namespace {
std::uint64_t deadline(std::uint64_t now, std::uint64_t duration) {
    return now + std::min(duration, std::numeric_limits<std::uint64_t>::max() - now);
}
}
const char* name(State state) {
    switch (state) {
    case State::Closed: return "CLOSED";
    case State::Armed: return "ARMED";
    case State::Candidate: return "CANDIDATE";
    case State::Comparing: return "COMPARING";
    case State::Approved: return "APPROVED";
    case State::Authorized: return "AUTHORIZED";
    }
    return "CLOSED";
}
bool Policy::open(const std::string& window_id, std::uint64_t now) {
    tick(now);
    if (snapshot_.state != State::Closed || window_id.empty() || window_id.size() > 96) return false;
    snapshot_ = {};
    snapshot_.state = State::Armed; snapshot_.window_id = window_id;
    snapshot_.deadline_ms = arm_deadline_ = deadline(now, 60000);
    snapshot_.reason = "PAIRING_WINDOW_OPEN";
    return true;
}
bool Policy::candidate(const Identity& identity, std::uint64_t now) {
    if (tick(now) || snapshot_.state != State::Armed) return false;
    snapshot_.identity = identity; snapshot_.state = State::Candidate;
    snapshot_.reason = "PAIRING_CANDIDATE";
    return true;
}
bool Policy::challenge(const Identity& identity, std::uint32_t number, std::uint64_t now) {
    if (tick(now) || snapshot_.state != State::Candidate || !(identity == snapshot_.identity) || number > 999999)
        return false;
    snapshot_.state = State::Comparing; snapshot_.number = number;
    snapshot_.deadline_ms = std::min(arm_deadline_, deadline(now, 25000));
    snapshot_.reason = "PAIRING_COMPARE_NUMBER";
    return true;
}
bool Policy::matches(const std::string& window_id, const Identity& identity,
                     std::uint32_t number, std::uint64_t now) {
    return !tick(now) && snapshot_.state == State::Comparing && snapshot_.window_id == window_id &&
        identity == snapshot_.identity && snapshot_.number == number;
}
bool Policy::confirm(const std::string& window_id, const Identity& identity,
                     std::uint32_t number, std::uint64_t now) {
    if (!matches(window_id, identity, number, now)) return false;
    snapshot_.state = State::Approved; snapshot_.number.reset();
    snapshot_.reason = "PAIRING_APPROVED";
    return true;
}
bool Policy::reject(const std::string& window_id, const Identity& identity,
                    std::uint32_t number, std::uint64_t now) {
    if (!matches(window_id, identity, number, now)) return false;
    close("PAIRING_REJECTED"); return true;
}
bool Policy::security(const Identity& identity, bool encrypted, bool authenticated, bool bonded, std::uint64_t now) {
    if (tick(now) || (snapshot_.state != State::Approved && snapshot_.state != State::Authorized) || !(identity == snapshot_.identity) ||
        !encrypted || !authenticated || !bonded) return false;
    snapshot_.state = State::Authorized; snapshot_.deadline_ms = 0;
    snapshot_.reason = "PAIRING_AUTHORIZED";
    return true;
}
bool Policy::tick(std::uint64_t now) {
    if (snapshot_.state == State::Closed || snapshot_.state == State::Authorized || now < snapshot_.deadline_ms)
        return false;
    close("PAIRING_TIMEOUT"); return true;
}
void Policy::disconnect(const Identity& identity) {
    if (identity == snapshot_.identity) close("PAIRING_DISCONNECTED");
}
void Policy::close(const char* reason) {
    snapshot_.state = State::Closed; snapshot_.deadline_ms = 0;
    snapshot_.number.reset(); snapshot_.reason = reason;
}
}
