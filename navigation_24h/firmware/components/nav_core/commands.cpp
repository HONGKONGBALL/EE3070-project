#include "nav_core.hpp"
#include <algorithm>

namespace nav {
Ack Engine::command(const Command& command, Millis now) {
    if (now < clock_) return {false, false, "MONOTONIC_CLOCK"};
    tick(now);
    if (command.command_id.empty() || command.command_id.size() > 64)
        return {false, false, "COMMAND_ID"};
    for (const auto& start : starts_)
        if (start.command_id == command.command_id) return {false, false, "COMMAND_ID_REUSE"};
    if (command.session_id != snapshot_.session_id)
        return {false, false, "SESSION_GENERATION"};
    for (std::size_t index = 0; index < commands_.size(); ++index) {
        const auto& saved = commands_[index];
        if (saved.command_id == command.command_id) {
            if (saved.kind != command.kind || saved.generation != command.generation ||
                saved.route_version != command.route_version || saved.event_id != command.event_id ||
                saved.lease_ms != command.lease_ms) return {false, false, "COMMAND_ID_REUSE"};
            Ack ack = command_acks_[index]; ack.duplicate = true; return ack;
        }
    }
    if (command.generation != snapshot_.generation)
        return {false, false, "SESSION_GENERATION"};
    auto ack = apply_command(command, now);
    commands_[command_cursor_] = command;
    command_acks_[command_cursor_] = ack;
    command_cursor_ = (command_cursor_ + 1) % commands_.size();
    return ack;
}
Ack Engine::apply_command(const Command& command, Millis now) {
    switch (command.kind) {
        case CommandKind::GetStatus: return {true, false, "OK"};
        case CommandKind::Cancel:
            if (snapshot_.generation == UINT64_MAX) return {false, false, "GENERATION_LIMIT"};
            ++snapshot_.generation; active_ = false; request_.reset(); route_ = {};
            events_.clear(); current_event_.reset(); snapshot_.current_event_id = 0;
            transition(State::Idle, "CANCELED", now); return {true, false, "OK"};
        case CommandKind::Pause:
            user_paused_ = true; awaiting_new_fix_ = true;
            events_.clear(); current_event_.reset(); snapshot_.current_event_id = 0;
            transition(State::Paused, "USER_PAUSE", now); return {true, false, "OK"};
        case CommandKind::Resume:
            if (!active_ || !permitted(now)) return {false, false, "PERMISSION_WAIT"};
            user_paused_ = false; resume_required_ = false; awaiting_new_fix_ = true;
            events_.clear(); current_event_.reset(); snapshot_.current_event_id = 0;
            transition(State::WaitFix, "RESUME_WAIT_NEW_FIX", now); return {true, false, "OK"};
        case CommandKind::Permission:
            if (!active_ || command.lease_ms == 0 || command.lease_ms > 3000 ||
                now > UINT64_MAX - command.lease_ms) return {false, false, "LEASE_RANGE"};
            permission_until_ = now + command.lease_ms; return {true, false, "OK"};
        case CommandKind::Repeat:
            if (!current_event_ || !event_valid(*current_event_, now))
                return {false, false, "EVENT_EXPIRED"};
            emit(current_event_->type, now, current_event_->confirmation_source);
            return {true, false, "OK"};
        case CommandKind::ConfirmTurn:
        case CommandKind::ConfirmCrossing:
        case CommandKind::ConfirmArrival:
            if (!active_ || !fresh(now) || awaiting_new_fix_ || !permitted(now) ||
                user_paused_ || command.route_version != snapshot_.route_version ||
                command.event_id != snapshot_.current_event_id || !current_event_ ||
                !event_valid(*current_event_, now)) return {false, false, "CONFIRM_CONTEXT"};
            break;
    }
    switch (command.kind) {
        case CommandKind::ConfirmTurn:
            if (snapshot_.state != State::AlignConfirm) return {false, false, "TURN_STATE"};
            transition(State::Turn, "MANUAL_TURN_CONFIRM", now);
            emit("maneuver", now, "manual_confirm"); return {true, false, "OK"};
        case CommandKind::ConfirmCrossing:
            if (snapshot_.state != State::CrossingWait) return {false, false, "CROSSING_STATE"};
            crossing_confirmed_ = true;
            transition(State::Turn, "MANUAL_CROSSING_CONFIRM", now);
            emit("maneuver", now, "manual_confirm"); return {true, false, "OK"};
        case CommandKind::ConfirmArrival:
            if (snapshot_.state != State::ArrivalNear) return {false, false, "ARRIVAL_STATE"};
            transition(State::Arrived, "ENTRANCE_CONFIRMED", now);
            emit("arrived", now, "manual_confirm"); active_ = false; request_.reset();
            return {true, false, "OK"};
        case CommandKind::Cancel: case CommandKind::Pause: case CommandKind::Resume:
        case CommandKind::Repeat: case CommandKind::GetStatus: case CommandKind::Permission:
            return {false, false, "COMMAND_STATE"};
    }
    return {false, false, "COMMAND_STATE"};
}
void Engine::emit(const std::string& type, Millis now, const std::string& source) {
    Event event{type, snapshot_.session_id, snapshot_.generation, snapshot_.route_version,
        next_event_++, snapshot_.fix_seq, 0, source, 2000, now};
    if (type == "fault") event.reason = snapshot_.reason;
    if (!route_.points.empty() && last_fix_)
        event.distance_m = distance_m(last_fix_->position, route_.points.back());
    if (events_.size() == 32) events_.erase(events_.begin());
    events_.push_back(event); current_event_ = event; snapshot_.current_event_id = event.event_id;
}
bool Engine::event_valid(const Event& event, Millis now) const {
    const bool identity = event.session_id == snapshot_.session_id &&
        event.generation == snapshot_.generation && event.route_version == snapshot_.route_version;
    const bool time = now >= event.issued_at_ms && now - event.issued_at_ms < event.ttl_ms;
    const bool current = event.event_id > 0 && event.event_id == snapshot_.current_event_id;
    return identity && time && current && permitted(now) && !user_paused_ && !resume_required_ && !awaiting_new_fix_ &&
           fresh(now) && link_connected_;
}
std::vector<Event> Engine::drain_events(Millis now) {
    tick(now); std::vector<Event> result;
    for (const auto& event : events_) if (event_valid(event, now)) result.push_back(event);
    events_.clear(); return result;
}
Ack Engine::acknowledge_event(const std::string& session_id, std::uint64_t generation,
                             std::uint64_t route_version, std::uint64_t event_id, Millis now) {
    if (now < clock_) return {false, false, "MONOTONIC_CLOCK"};
    tick(now);
    if (!current_event_ || session_id != snapshot_.session_id ||
        generation != snapshot_.generation || route_version != snapshot_.route_version ||
        event_id != snapshot_.current_event_id || !event_valid(*current_event_, now))
        return {false, false, "EVENT_CONTEXT"};
    const bool duplicate = acknowledged_event_ == event_id;
    acknowledged_event_ = event_id;
    return {true, duplicate, "EVENT_ACKED"};
}
}
