#include "nav_core.hpp"
#include <cmath>

namespace nav {
bool Engine::fresh(Millis now) const {
    return last_fix_ && now >= last_received_at_ &&
        now - last_received_at_ < 5000 &&
        last_fix_->measurement_age_ms < 5000 - (now - last_received_at_) &&
        snapshot_.quality == Quality::Valid;
}
FixResult Engine::ingest(const Fix& fix, Millis now) {
    if (now < clock_) return {false, FixReason::Clock};
    tick(now);
    if (!active_ || fix.phone_session_id != phone_session_id_)
        return {false, FixReason::Identity};
    if (!link_connected_) return {false, FixReason::Link};
    if (fix.fix_seq == 0 || fix.fix_seq <= seen_seq_) return {false, FixReason::Sequence};
    seen_seq_ = fix.fix_seq;
    if (last_fix_ && fix.source_timestamp_ms == last_fix_->source_timestamp_ms)
        return {false, FixReason::SourceDuplicate};
    if (!valid_point(fix.position) || !std::isfinite(fix.horizontal_accuracy_m) ||
        fix.horizontal_accuracy_m < 0 ||
        (fix.speed_mps && (!std::isfinite(*fix.speed_mps) || *fix.speed_mps < 0)) ||
        (fix.course_deg && (!std::isfinite(*fix.course_deg) || *fix.course_deg < 0 ||
                            *fix.course_deg >= 360))) return {false, FixReason::Coordinates};
    if (fix.measurement_age_ms >= 5000) return {false, FixReason::Expired};
    if (fix.horizontal_accuracy_m > 20) {
        snapshot_.quality = Quality::Degraded; snapshot_.reason = "FIX_ACCURACY";
        arrival_count_ = 0; return {false, FixReason::Accuracy};
    }
    if (last_fix_) {
        const double elapsed = (now - last_received_at_) / 1000.0;
        const double reachable = elapsed * 4 + fix.horizontal_accuracy_m +
                                 last_fix_->horizontal_accuracy_m + 5;
        if (distance_m(last_fix_->position, fix.position) > reachable) {
            snapshot_.quality = Quality::Degraded; snapshot_.reason = "FIX_JUMP";
            if (!route_.points.empty() && !user_paused_ && permitted(now))
                transition(State::Relocalize, "FIX_JUMP", now);
            arrival_count_ = 0;
            return {false, FixReason::Jump};
        }
    }
    last_fix_ = fix; last_received_at_ = now;
    snapshot_.fix_seq = fix.fix_seq; snapshot_.fix_age_ms = fix.measurement_age_ms;
    snapshot_.quality = Quality::Valid; snapshot_.reason = "FIX_ACCEPTED";
    if (user_paused_ || resume_required_ || !permitted(now)) {
        transition(State::Paused, user_paused_ ? "USER_PAUSE" : "PERMISSION_WAIT", now);
        return {true, FixReason::Accepted};
    }
    awaiting_new_fix_ = false;
    if (route_.points.empty()) {
        if (!request_) request_route(now, false);
    } else follow(fix, now);
    return {true, FixReason::Accepted};
}
void Engine::tick(Millis now) {
    if (now < clock_) return;
    clock_ = now;
    if (last_fix_) {
        const Millis elapsed = now - last_received_at_;
        snapshot_.fix_age_ms = elapsed > UINT64_MAX - last_fix_->measurement_age_ms ?
            UINT64_MAX : last_fix_->measurement_age_ms + elapsed;
        if (snapshot_.fix_age_ms >= 5000) {
            snapshot_.quality = Quality::Lost; arrival_count_ = 0;
            if (active_ && !user_paused_ && !resume_required_)
                transition(State::WaitFix, "FIX_EXPIRED", now);
        }
    }
    if (active_ && integrated_ && !permitted(now)) {
        resume_required_ = true;
        awaiting_new_fix_ = true; events_.clear(); current_event_.reset();
        snapshot_.current_event_id = 0;
        transition(State::Paused, "PERMISSION_EXPIRED", now);
    }
}
}
