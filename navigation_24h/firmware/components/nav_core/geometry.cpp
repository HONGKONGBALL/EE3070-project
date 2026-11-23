#include "nav_core.hpp"
#include <algorithm>
#include <cmath>

namespace nav {
namespace { constexpr double pi = 3.14159265358979323846; }
bool valid_point(Point p) {
    return std::isfinite(p.lng) && std::isfinite(p.lat) &&
           p.lng >= -180 && p.lng <= 180 && p.lat >= -90 && p.lat <= 90;
}
double distance_m(Point a, Point b) {
    const double dx = (b.lng - a.lng) * std::cos((a.lat + b.lat) * pi / 360);
    const double dy = b.lat - a.lat;
    return std::hypot(dx, dy) * pi / 180 * 6371000;
}
double bearing_deg(Point a, Point b) {
    const double dx = (b.lng - a.lng) * std::cos((a.lat + b.lat) * pi / 360);
    const double value = std::atan2(dx, b.lat - a.lat) * 180 / pi;
    return std::fmod(value + 360, 360);
}
const char* name(State s) {
    switch (s) {
        case State::Idle: return "IDLE";
        case State::WaitFix: return "WAIT_FIX";
        case State::Routing: return "ROUTING";
        case State::Follow: return "FOLLOW";
        case State::Approach: return "APPROACH";
        case State::AlignConfirm: return "ALIGN_CONFIRM";
        case State::Turn: return "TURN";
        case State::Verify: return "VERIFY";
        case State::Relocalize: return "RELOCALIZE";
        case State::Rerouting: return "REROUTING";
        case State::CrossingWait: return "CROSSING_WAIT";
        case State::Paused: return "PAUSED";
        case State::ArrivalNear: return "ARRIVAL_NEAR";
        case State::Arrived: return "ARRIVED";
    }
    return "IDLE";
}
const char* name(Quality q) {
    switch (q) {
        case Quality::Valid: return "VALID";
        case Quality::Degraded: return "DEGRADED";
        case Quality::Lost: return "LOST";
    }
    return "LOST";
}
const char* name(FixReason reason) {
    switch (reason) {
        case FixReason::Accepted: return "ACCEPTED";
        case FixReason::Identity: return "PHONE_SESSION";
        case FixReason::Sequence: return "FIX_SEQUENCE";
        case FixReason::SourceDuplicate: return "FIX_SOURCE_DUPLICATE";
        case FixReason::Expired: return "FIX_EXPIRED";
        case FixReason::Accuracy: return "FIX_ACCURACY";
        case FixReason::Jump: return "FIX_JUMP";
        case FixReason::Coordinates: return "FIX_COORDINATES";
        case FixReason::Clock: return "MONOTONIC_CLOCK";
        case FixReason::Link: return "LINK_WAIT";
    }
    return "FIX_COORDINATES";
}
}
