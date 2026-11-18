#include "nav_core.hpp"
#include <algorithm>

namespace nav {
Ack Engine::start(std::string session_id, std::string phone_session_id,
                  std::uint64_t generation, Destination destination, Millis now,
                  const std::string& command_id) {
    if (now < clock_) return {false, false, "MONOTONIC_CLOCK"};
    if (command_id.size() > 64) return {false, false, "COMMAND_ID"};
    if (!command_id.empty()) {
        for (const auto& command : commands_)
            if (command.command_id == command_id) return {false, false, "COMMAND_ID_REUSE"};
        for (const auto& record : starts_) {
            if (record.command_id != command_id) continue;
            if (record.session_id != session_id || record.phone_session_id != phone_session_id ||
                record.generation != generation || record.destination.name != destination.name ||
                record.destination.position.lng != destination.position.lng ||
                record.destination.position.lat != destination.position.lat)
                return {false, false, "COMMAND_ID_REUSE"};
            return {true, true, "OK"};
        }
    }
    if (active_) return {false, false, "SESSION_ACTIVE"};
    if (session_id.empty() || session_id.size() > 64 || phone_session_id.empty() ||
        phone_session_id.size() > 64 || generation == 0 || !valid_point(destination.position))
        return {false, false, "START_FIELDS"};
    if (!command_id.empty()) {
        starts_[start_cursor_] = {command_id, session_id, phone_session_id, generation, destination};
        start_cursor_ = (start_cursor_ + 1) % starts_.size();
    }
    snapshot_ = {}; snapshot_.session_id = std::move(session_id);
    snapshot_.generation = generation; phone_session_id_ = std::move(phone_session_id);
    destination_ = std::move(destination); clock_ = now; active_ = true;
    route_ = {}; matcher_.reset(); last_match_ = {}; last_fix_.reset(); request_.reset();
    events_.clear(); current_event_.reset(); seen_seq_ = 0;
    commands_ = {}; command_acks_ = {}; command_cursor_ = 0;
    arrival_count_ = offroute_count_ = 0; turn_step_ = 0; has_rerouted_ = false;
    user_paused_ = false; awaiting_new_fix_ = true;
    permission_until_ = 0; resume_required_ = integrated_; crossing_confirmed_ = false;
    transition(State::WaitFix, "SESSION_STARTED", now);
    return {true, false, "OK"};
}
bool Engine::current_token(const RequestToken& token) const {
    return active_ && request_ && token.session_id == snapshot_.session_id &&
        token.generation == snapshot_.generation && token.request_id == request_->request_id;
}
void Engine::request_route(Millis now, bool reroute) {
    if (request_ || !fresh(now) || !active_) return;
    request_ = RequestToken{snapshot_.session_id, snapshot_.generation, next_request_++,
                            last_fix_->position, destination_};
    if (reroute) {
        last_reroute_ = now; has_rerouted_ = true;
        transition(State::Rerouting, "OFF_ROUTE_CONFIRMED", now); emit("rerouting", now);
    } else transition(State::Routing, "REQUEST_CREATED", now);
}
bool Engine::commit_route(const RequestToken& token, const Route& route, Millis now) {
    if (now < clock_) return false;
    tick(now);
    if (!current_token(token) || !fresh(now)) return false;
    if (route.points.size() > 2048 || route.steps.size() > 256) {
        snapshot_.reason = "ROUTE_CAPACITY"; request_.reset(); return false;
    }
    if (route.points.size() < 2 || route.coord_frame != "WGS84" ||
        !std::all_of(route.points.begin(), route.points.end(), valid_point)) {
        snapshot_.reason = "ROUTE_FORMAT"; request_.reset(); return false;
    }
    std::size_t previous = 0;
    for (const auto& step : route.steps) {
        if (step.end_point == 0 || step.end_point >= route.points.size() ||
            step.end_point <= previous || step.action.size() > 128) {
            snapshot_.reason = "ROUTE_STEPS"; request_.reset(); return false;
        }
        previous = step.end_point;
    }
    if (distance_m(route.points.front(), token.origin) > 100 ||
        distance_m(route.points.back(), destination_.position) > 100) {
        snapshot_.reason = "ROUTE_ENDPOINTS"; request_.reset(); return false;
    }
    double total = 0;
    for (std::size_t index = 1; index < route.points.size(); ++index)
        total += distance_m(route.points[index - 1], route.points[index]);
    if (total < 1) { snapshot_.reason = "ROUTE_LENGTH"; request_.reset(); return false; }
    route_ = route; matcher_.reset(); last_match_ = {}; request_.reset(); ++snapshot_.route_version;
    snapshot_.progress_m = 0; snapshot_.segment = 0; turn_step_ = 0; crossing_confirmed_ = false;
    arrival_count_ = offroute_count_ = 0; events_.clear(); current_event_.reset();
    snapshot_.current_event_id = 0;
    transition(user_paused_ || resume_required_ || !permitted(now) ? State::Paused : State::Follow,
               "ROUTE_COMMITTED", now);
    emit("route_ready", now); return true;
}
void Engine::fail_request(const RequestToken& token, const std::string& reason, Millis now) {
    if (now < clock_ || !current_token(token)) return;
    tick(now); request_.reset();
    State state = route_.points.empty() || !fresh(now) ? State::WaitFix : State::Follow;
    if (user_paused_ || resume_required_ || !permitted(now)) state = State::Paused;
    transition(state, reason, now);
    emit("fault", now);
}
void Engine::transition(State state, const std::string& reason, Millis) {
    snapshot_.state = state; snapshot_.reason = reason;
}
void Engine::set_link(bool connected, Millis now) {
    if (now < clock_) return;
    tick(now);
    if (connected == link_connected_) return;
    link_connected_ = connected;
    if (active_) {
        awaiting_new_fix_ = true; events_.clear(); current_event_.reset();
        snapshot_.current_event_id = 0;
        transition(user_paused_ || resume_required_ || !permitted(now) ? State::Paused : State::WaitFix,
                   connected ? "LINK_WAIT_NEW_FIX" : "LINK_DISCONNECTED", now);
    }
}
void Engine::set_integration(bool enabled, Millis now) {
    if (now < clock_) return;
    integrated_ = enabled; permission_until_ = 0; tick(now);
}
bool Engine::permitted(Millis now) const { return !integrated_ || now < permission_until_; }
}
