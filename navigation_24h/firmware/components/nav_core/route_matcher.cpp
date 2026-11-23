#include "nav_core.hpp"
#include <algorithm>
#include <cmath>
#include <limits>

namespace nav {
namespace {
struct Candidate { Match match; double score = std::numeric_limits<double>::infinity();
    double heading_error = 0; bool reachable = true; };
double angle_difference(double a, double b) {
    return std::abs(std::remainder(a - b, 360));
}
}
void Matcher::reset() {
    previous_.reset(); previous_time_ = 0; previous_accuracy_ = 0; backward_count_ = 0;
}
Match Matcher::match(const Route& route, const Fix& fix, Millis now) {
    if (route.points.size() < 2) return {};
    std::array<Candidate, 4> best{};
    double accumulated = 0;
    const bool heading = fix.course_deg.has_value() && fix.speed_mps.value_or(0) >= 0.8 &&
                         fix.horizontal_accuracy_m <= 10;
    for (std::size_t index = 0; index + 1 < route.points.size(); ++index) {
        const auto a = route.points[index], b = route.points[index + 1];
        const double length = distance_m(a, b);
        if (length <= 0.001) continue;
        const double scale_x = 111195 * std::cos(fix.position.lat * 3.141592653589793 / 180);
        const double ax = (a.lng - fix.position.lng) * scale_x;
        const double ay = (a.lat - fix.position.lat) * 111195;
        const double bx = (b.lng - fix.position.lng) * scale_x;
        const double by = (b.lat - fix.position.lat) * 111195;
        const double dx = bx - ax, dy = by - ay;
        const double ratio = std::clamp(-(ax * dx + ay * dy) / (dx * dx + dy * dy), 0.0, 1.0);
        const double lateral = std::hypot(ax + ratio * dx, ay + ratio * dy);
        Candidate candidate;
        candidate.match = {true, false, false, index, accumulated + ratio * length,
                           lateral, 0};
        candidate.heading_error = heading ? angle_difference(*fix.course_deg,
                                                              bearing_deg(a, b)) : 0;
        candidate.score = lateral + candidate.heading_error * 0.12;
        if (previous_) {
            const double elapsed = now >= previous_time_ ? (now - previous_time_) / 1000.0 : 0;
            const double maximum = elapsed * 4 + fix.horizontal_accuracy_m +
                                   previous_accuracy_ + 5;
            const double delta = std::abs(candidate.match.progress_m - previous_->progress_m);
            candidate.reachable = delta <= maximum;
            candidate.score += delta * 0.025 + (candidate.reachable ? 0 : 10000);
        }
        for (std::size_t slot = 0; slot < best.size(); ++slot) {
            if (candidate.score < best[slot].score) {
                for (std::size_t shift = best.size() - 1; shift > slot; --shift)
                    best[shift] = best[shift - 1];
                best[slot] = candidate; break;
            }
        }
        accumulated += length;
    }
    auto result = best[0].match; result.total_m = accumulated;
    if (!result.found) return result;
    for (const auto& candidate : best) {
        if (!candidate.match.found) continue;
        result.candidates[result.candidate_count++] = {candidate.match.segment,
            candidate.match.progress_m, candidate.match.lateral_m, candidate.score,
            candidate.heading_error, candidate.reachable};
    }
    if (!best[0].reachable) result.ambiguous = true;
    for (std::size_t index = 1; index < best.size(); ++index) {
        const auto& other = best[index];
        if (!other.match.found || !other.reachable) continue;
        const auto segment_gap = result.segment > other.match.segment ?
            result.segment - other.match.segment : other.match.segment - result.segment;
        if (segment_gap <= 1) continue;
        const double near = std::max(3.0, fix.horizontal_accuracy_m);
        if (other.match.lateral_m <= result.lateral_m + near &&
            other.score <= best[0].score + near &&
            (!heading || std::abs(other.heading_error - best[0].heading_error) < 35))
            result.ambiguous = true;
    }
    if (previous_ && result.progress_m < previous_->progress_m -
        std::max(4.0, fix.horizontal_accuracy_m)) ++backward_count_;
    else if (previous_ && result.progress_m > previous_->progress_m + 2) backward_count_ = 0;
    result.backward = backward_count_ >= 2;
    if (!result.ambiguous && result.lateral_m <= 25) {
        previous_ = result; previous_time_ = now;
        previous_accuracy_ = fix.horizontal_accuracy_m;
    }
    return result;
}
}
