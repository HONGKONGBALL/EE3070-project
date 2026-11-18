#include "pairing_policy.hpp"
#include <functional>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
using ble_link::pairing::Identity;
using ble_link::pairing::Policy;
using ble_link::pairing::State;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
const Identity first{1, 7, {0, {1, 2, 3, 4, 5, 6}}};
void comparing(Policy& policy) {
    require(policy.open("boot-a-window-1", 1000), "USB window opens");
    require(policy.candidate(first, 2000), "candidate consumes window");
    require(policy.challenge(first, 123456, 3000), "numeric comparison starts");
}
struct Case { const char* name; std::function<void()> run; };
}
int main() {
    const std::vector<Case> cases{
        {"closed_window_candidate", [] {
            Policy policy;
            require(!policy.candidate(first, 0), "candidate requires armed window");
        }},
        {"single_candidate_and_sixty_second_boundary", [] {
            Policy policy;
            require(policy.open("boot-a-window-1", 1000), "window opens");
            require(policy.snapshot().deadline_ms == 61000, "arm deadline is sixty seconds");
            require(policy.candidate(first, 2000), "first candidate admitted");
            auto second = first; ++second.epoch;
            require(!policy.candidate(second, 2001), "candidate owns consumed window");
            Policy expired;
            require(expired.open("boot-b-window-1", 1000), "fresh boot window opens");
            require(!expired.candidate(first, 61000), "arm expiry boundary is strict");
        }},
        {"numeric_comparison_identity_and_expiry", [] {
            Policy policy; comparing(policy);
            require(policy.snapshot().deadline_ms == 28000, "comparison deadline is twenty-five seconds");
            auto other = first; other.peer.bytes[0] = 99;
            require(!policy.confirm("boot-a-window-1", other, 123456, 4000), "approval binds peer");
            other = first; ++other.epoch;
            require(!policy.confirm("boot-a-window-1", other, 123456, 4000), "approval binds connection epoch");
            other = first; ++other.handle;
            require(!policy.confirm("boot-a-window-1", other, 123456, 4000), "approval binds connection handle");
            require(!policy.confirm("boot-b-window-1", first, 123456, 4000), "approval binds boot window");
            require(!policy.confirm("boot-a-window-1", first, 123457, 4000), "approval binds comparison number");
            require(!policy.confirm("boot-a-window-1", first, 123456, 28000), "comparison expiry boundary is strict");
            require(policy.snapshot().state == State::Closed, "expired comparison closes");
        }},
        {"authorization_requires_approved_authenticated_bond", [] {
            Policy policy; comparing(policy);
            require(!policy.security(first, true, true, true, 3500), "security follows operator approval");
            require(policy.confirm("boot-a-window-1", first, 123456, 4000), "matching comparison accepted once");
            require(!policy.confirm("boot-a-window-1", first, 123456, 4001), "approval is consumed");
            require(!policy.security(first, true, false, true, 4500), "authentication participates in grant");
            require(!policy.security(first, true, true, false, 4501), "bond participates in grant");
            require(!policy.security(first, false, true, true, 4502), "encryption participates in grant");
            require(policy.security(first, true, true, true, 4503), "approved authenticated bond grants access");
            require(policy.snapshot().state == State::Authorized, "authorized state recorded");
            require(policy.security(first, true, true, true, 4504), "matching security refresh retains authorization");
        }},
        {"disconnect_and_reused_handle", [] {
            Policy policy; comparing(policy);
            policy.disconnect(first);
            require(policy.snapshot().state == State::Closed, "disconnect closes pending comparison");
            auto next = first; ++next.epoch;
            require(policy.open("boot-a-window-2", 5000) && policy.candidate(next, 6000) &&
                    policy.challenge(next, 123456, 7000), "reused handle receives new window identity");
            require(!policy.confirm("boot-a-window-1", first, 123456, 8000), "stale approval keeps new candidate pending");
            require(policy.snapshot().state == State::Comparing, "current candidate retained");
        }},
        {"reject_and_timeout_close", [] {
            Policy policy; comparing(policy);
            require(policy.reject("boot-a-window-1", first, 123456, 4000), "operator rejection consumed");
            require(policy.snapshot().state == State::Closed, "rejection closes state");
            comparing(policy);
            require(policy.tick(28000), "deadline expires without USB input");
            require(policy.snapshot().state == State::Closed, "timeout closes state");
        }}
    };
    unsigned failed = 0;
    for (const auto& item : cases) {
        try { item.run(); std::cout << item.name << " passed\n"; }
        catch (const std::runtime_error& error) { ++failed; std::cout << item.name << ": " << error.what() << '\n'; }
    }
    return failed ? 1 : 0;
}
