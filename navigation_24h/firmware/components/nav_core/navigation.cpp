#include "nav_core.hpp"
#include <algorithm>

namespace nav {
void Engine::follow(const Fix& fix, Millis now) {
    const auto match = matcher_.match(route_, fix, now);
    last_match_ = match;
    if (!match.found) { transition(State::Relocalize, "MATCH_EMPTY", now); return; }
    snapshot_.lateral_m = match.lateral_m;
    if (match.ambiguous || match.backward) {
        arrival_count_ = 0;
        transition(State::Relocalize, match.backward ? "BACKWALK" : "MATCH_AMBIGUOUS", now);
        emit("relocalize", now); return;
    }
    snapshot_.segment = match.segment; snapshot_.progress_m = match.progress_m;
    if (match.lateral_m > 25) {
        arrival_count_ = 0; update_offroute(match, now); return;
    }
    offroute_count_ = 0; offroute_since_ = 0;
    if (request_) { transition(State::Rerouting, "REQUEST_PENDING", now); return; }
    switch (snapshot_.state) {
        case State::WaitFix: case State::Paused: case State::Relocalize:
        case State::Routing: case State::Rerouting:
            events_.clear(); current_event_.reset(); snapshot_.current_event_id = 0;
            transition(State::Follow, "MATCH_RECOVERED", now); break;
        case State::Idle: case State::Follow: case State::Approach: case State::AlignConfirm:
        case State::Turn: case State::Verify: case State::CrossingWait:
        case State::ArrivalNear: case State::Arrived: break;
    }
    update_turn(fix, match, now); update_arrival(fix, match, now);
}
void Engine::update_offroute(const Match&, Millis now) {
    if (offroute_count_ == 0) offroute_since_ = now;
    ++offroute_count_;
    if (offroute_count_ >= 3 && now - offroute_since_ >= 3000 && !request_ &&
        (!has_rerouted_ || now - last_reroute_ >= 15000)) request_route(now, true);
}
void Engine::update_turn(const Fix&, const Match& match, Millis now) {
    if (turn_step_ >= route_.steps.size()) return;
    const auto& step = route_.steps[turn_step_];
    if (step.end_point + 1 >= route_.points.size()) return;
    double end_distance = 0;
    for (std::size_t index = 1; index <= step.end_point; ++index)
        end_distance += distance_m(route_.points[index - 1], route_.points[index]);
    const double remaining = end_distance - match.progress_m;
    const bool outgoing = match.segment >= step.end_point && match.progress_m >= end_distance + 5;
    switch (snapshot_.state) {
        case State::Follow:
            if (step.crossing && !crossing_confirmed_ && remaining <= 12) {
                transition(State::CrossingWait, "CROSSING_CONFIRM", now); emit("maneuver", now);
            } else if (step.crossing && crossing_confirmed_ && outgoing) {
                transition(State::Verify, "CROSSING_RESUME_VERIFY", now);
                emit("maneuver", now, "manual_confirm");
            } else if (remaining <= 30 && remaining >= -5) {
                const State next = remaining <= 12 ?
                    (step.crossing ? State::CrossingWait : State::AlignConfirm) : State::Approach;
                transition(next, remaining <= 12 ? "CONFIRM_RECOMPUTED" : "TURN_APPROACH", now);
                emit("maneuver", now);
            }
            break;
        case State::Approach:
            if (remaining <= 12) {
                transition(step.crossing ? State::CrossingWait : State::AlignConfirm,
                           step.crossing ? "CROSSING_CONFIRM" : "TURN_ALIGN_CONFIRM", now);
                emit("maneuver", now);
            }
            break;
        case State::AlignConfirm:
            if (outgoing) {
                transition(State::Turn, "TURN_MOVEMENT", now);
                emit("maneuver", now, "valid_movement_evidence");
            } else if (!current_event_ || !event_valid(*current_event_, now)) emit("maneuver", now);
            break;
        case State::Turn:
            if (outgoing) {
                transition(State::Verify, "OUTGOING_SEGMENT", now);
                emit("maneuver", now, current_event_ ? current_event_->confirmation_source : "");
            }
            break;
        case State::Verify:
            if (outgoing) {
                ++turn_step_; crossing_confirmed_ = false;
                events_.clear(); current_event_.reset(); snapshot_.current_event_id = 0;
                transition(State::Follow, "TURN_VERIFIED", now);
            }
            break;
        case State::CrossingWait:
            if (!current_event_ || !event_valid(*current_event_, now)) emit("maneuver", now);
            break;
        case State::Idle: case State::WaitFix: case State::Routing:
        case State::Relocalize: case State::Rerouting:
        case State::Paused: case State::ArrivalNear: case State::Arrived: break;
    }
}
void Engine::update_arrival(const Fix& fix, const Match& match, Millis now) {
    if (turn_step_ < route_.steps.size() && route_.steps[turn_step_].crossing &&
        !crossing_confirmed_) { arrival_count_ = 0; return; }
    const bool final_segment = match.segment + 2 == route_.points.size();
    const bool near = final_segment && fix.horizontal_accuracy_m <= 10 &&
        match.total_m - match.progress_m <= 15 &&
        distance_m(fix.position, route_.points.back()) <= 15 && match.lateral_m <= 10;
    if (!near) {
        arrival_count_ = 0;
        if (snapshot_.state == State::ArrivalNear)
            transition(State::Follow, "ARRIVAL_REGION_LEFT", now);
        return;
    }
    if (arrival_count_ < 3) ++arrival_count_;
    if (arrival_count_ >= 3 && snapshot_.state != State::ArrivalNear) {
        transition(State::ArrivalNear, "FINAL_SEGMENT_STABLE", now); emit("arrival_near", now);
    } else if (snapshot_.state == State::ArrivalNear &&
               (!current_event_ || !event_valid(*current_event_, now))) {
        emit("arrival_near", now);
    }
}
}
