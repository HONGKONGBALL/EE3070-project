#include "route_parser.hpp"
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

using namespace nav::mcp;
static void require(bool value, const char* description) {
    if (!value) { std::cerr << description << '\n'; std::exit(1); }
}
static std::string fixture(const char* relative) {
    std::ifstream file(std::string(NAV_PROJECT_ROOT) + "/" + relative);
    require(file.good(), "fixture opens");
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
int main() {
    const auto actual = fixture("fixtures/geometry/quota_10045.json");
    const auto status = classify_provider_response(actual);
    require(status.state == ServiceState::Quota && status.infocode == 10045 &&
        status.info == "USER_ABROAD_DAILY_QUERY_OVER_LIMIT", "actual provider quota envelope");
    const auto success = classify_provider_response(R"({"status":"1","info":"OK","infocode":"10000"})");
    require(success.state == ServiceState::Ready && success.infocode == 10000, "synthetic success envelope");
    require(classify_provider_response(R"({"status":"0","info":"error","infocode":"bad"})").state == ServiceState::InvalidEnvelope,
        "typed malformed provider envelope");
    const auto summary_reply = fixture("fixtures/routes/candidate_route01.json");
    RouteSummary summary;
    require(parse_walking_summary(summary_reply, "3", summary) == ParseStatus::Ok,
        "actual MCP summary metadata");
    require(summary.total_distance_m == 235 && summary.steps.size() == 7 && !summary.has_geometry,
        "actual MCP summary annotations");
    require(std::abs(summary.origin.lng - 114.177692) < 1e-10 &&
        std::abs(summary.destination.lat - 22.333517) < 1e-10, "actual MCP summary endpoints");
    double step_distance = 0;
    for (const auto& step : summary.steps) step_distance += step.distance_m;
    require(step_distance == 235 && !summary.steps[0].instruction.empty(), "actual MCP step metadata");
    ParsedRoute candidate;
    candidate.request_id = "active-candidate";
    candidate.route.points = {{1,2},{3,4}};
    require(parse_walking_response(summary_reply, "3", candidate) == ParseStatus::GeometryRequired,
        "summary candidate readiness");
    require(candidate.request_id == "active-candidate" && candidate.route.points[0].lng == 1,
        "summary preserves active candidate");
    const auto wrapped_quota = "{\"jsonrpc\":\"2.0\",\"id\":\"quota\",\"result\":{\"structuredContent\":" + actual + "}}";
    require(parse_walking_response(wrapped_quota, "quota", candidate) == ParseStatus::ServiceQuota,
        "provider quota at tool payload boundary");
    require(candidate.request_id == "active-candidate" && candidate.route.points.size() == 2,
        "quota preserves active candidate");
    const auto synthetic = R"({"jsonrpc":"2.0","id":"synthetic","result":{"structuredContent":{"status":"1","info":"OK","infocode":"10000","route":{"paths":[{"distance":"10","steps":[{"instruction":"向东步行10米","polyline":"114.177692,22.332921;114.177792,22.332921"}]}]}}}})";
    require(parse_walking_response(synthetic, "synthetic", candidate) == ParseStatus::Ok &&
        candidate.route.points.size() == 2 && candidate.route.steps.size() == 1,
        "synthetic geometry normalization");
    std::cout << "provider geometry fixture assertions passed\n";
}
