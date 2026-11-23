#pragma once
#include "nav_core.hpp"
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

namespace test {
struct Failure : std::runtime_error { using std::runtime_error::runtime_error; };
struct Case { std::string id; std::string name; std::function<void()> run; };
inline void require(bool condition, const std::string& message) {
    if (!condition) throw Failure(message);
}
inline void state(const nav::Engine& engine, nav::State expected) {
    require(engine.snapshot().state == expected,
        std::string("expected ") + nav::name(expected) + ", actual " +
        nav::name(engine.snapshot().state));
}
inline nav::Point point(double east, double north = 0) {
    return {114.0 + east / 103000.0, 22.0 + north / 111195.0};
}
inline nav::Fix fix(std::uint64_t seq, double east, double north = 0,
                    double accuracy = 3, std::uint64_t age = 0) {
    return {"phone-A", seq, 1700000000000LL + static_cast<std::int64_t>(seq * 1000),
            age, point(east, north), accuracy, 1.0, 90.0};
}
inline nav::Route straight() {
    return {{point(0), point(50), point(100)}, {{2, "arrive", false}}, "WGS84"};
}
inline void begin(nav::Engine& engine, std::uint64_t now = 1000) {
    require(engine.start("nav-A", "phone-A", 1, {"entry", point(100)}, now).accepted,
            "start accepted");
    require(engine.ingest(fix(1, 0), now).accepted, "initial measurement accepted");
}
inline nav::RequestToken activate(nav::Engine& engine, const nav::Route& route,
                                  std::uint64_t now = 1000) {
    begin(engine, now);
    auto token = engine.pending_request();
    require(token.has_value(), "request created after valid fix");
    require(engine.commit_route(*token, route, now), "current candidate committed");
    return *token;
}
inline nav::Command command(nav::CommandKind kind, const nav::Engine& engine) {
    const auto& s = engine.snapshot();
    return {kind, "cmd-" + std::to_string(static_cast<int>(kind)), s.session_id, s.generation, s.route_version,
            s.current_event_id, 3000};
}
std::vector<Case> fix_cases();
std::vector<Case> lifecycle_cases();
std::vector<Case> navigation_cases();
std::vector<Case> matcher_cases();
}
