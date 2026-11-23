#include "test_support.hpp"

namespace test {
std::vector<Case> matcher_cases() {
    return {
        {"TC25", "straight_progress_and_history", [] {
            nav::Matcher matcher; auto route = straight();
            auto first = matcher.match(route, fix(1, 10), 1000);
            auto second = matcher.match(route, fix(2, 20), 4000);
            require(first.found && !first.ambiguous && first.segment == 0,
                    "first straight segment matched");
            require(second.progress_m > first.progress_m + 9 && second.progress_m < 22,
                    "progress follows annotated movement");
        }},
        {"TC26", "crossing_and_parallel_ambiguity", [] {
            nav::Route crossing{{point(0, 0), point(100, 100), point(0, 100), point(100, 0)},
                                 {{3, "arrive", false}}, "WGS84"};
            nav::Matcher matcher; auto f = fix(1, 50, 50); f.course_deg.reset();
            auto ambiguous = matcher.match(crossing, f, 1000);
            require(ambiguous.ambiguous, "crossing requires disambiguation");
            require(ambiguous.candidate_count >= 2 &&
                    ambiguous.candidates[0].segment != ambiguous.candidates[1].segment,
                    "independent crossing candidates retained for diagnostics");
            f.course_deg = 45; auto oriented = matcher.match(crossing, f, 2000);
            require(!oriented.ambiguous && oriented.segment == 0,
                    "trusted course chooses northeast segment");
            nav::Route parallel{{point(0), point(100), point(100, 10), point(0, 10)},
                                {{3, "arrive", false}}, "WGS84"};
            nav::Matcher parallel_matcher; auto midpoint = fix(2, 50, 5, 8);
            midpoint.course_deg.reset();
            require(parallel_matcher.match(parallel, midpoint, 1000).ambiguous,
                    "parallel roads within uncertainty remain ambiguous");
        }},
        {"TC27", "history_backwalk_and_unreachable_progress", [] {
            nav::Matcher matcher; auto route = straight();
            auto first = matcher.match(route, fix(1, 40), 1000);
            auto second = matcher.match(route, fix(2, 30), 4000);
            auto third = matcher.match(route, fix(3, 20), 7000);
            require(first.found && !second.backward && third.backward,
                    "two independent reverse steps flag backwalk");
            nav::Matcher jump_matcher;
            static_cast<void>(jump_matcher.match(route, fix(1, 0), 1000));
            require(jump_matcher.match(route, fix(2, 90), 2000).ambiguous,
                    "unreachable along-route jump requests relocalization");
        }},
        {"TC22", "route_capacity_and_atomic_commit", [] {
            nav::Engine e; begin(e); auto token = *e.pending_request();
            auto too_large = straight(); too_large.points.resize(2049, point(50));
            require(!e.commit_route(token, too_large, 1001), "point capacity enforced");
            require(e.snapshot().reason == "ROUTE_CAPACITY" && e.active_route().points.empty(),
                    "capacity status accompanies retained active route");
            require(e.ingest(fix(2, 1), 2000).accepted, "next fresh fix creates retry request");
            auto valid = straight(); auto current = *e.pending_request();
            require(e.commit_route(current, valid, 2001), "valid retry route committed");
            require(e.snapshot().route_version == 1, "only valid commit advances version");
        }}
    };
}
}
