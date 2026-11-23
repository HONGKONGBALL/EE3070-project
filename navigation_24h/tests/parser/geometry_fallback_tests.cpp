#include "mcp_client.hpp"
#include "discovery_schedule.hpp"
#include "cJSON.h"
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <memory>
#include <string>
#include <vector>
using namespace nav::mcp;
namespace {
void require(bool value, const char* message) {
    if (!value) { std::cerr << message << '\n'; std::exit(1); }
}
std::string fixture(const char* relative) {
    std::ifstream stream(std::string(NAV_PROJECT_ROOT) + "/" + relative);
    require(stream.good(), "fixture opens");
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}
const std::string summary = R"({"jsonrpc":"2.0","id":"3","result":{"structuredContent":{"route":{"origin":"114.100000,22.300000","destination":"114.101000,22.300000","paths":[{"distance":"103","steps":[{"distance":"103","instruction":"向东步行103米","orientation":"东"}]}]}}}})";
const std::string geometry = R"({"status":"1","info":"OK","infocode":"10000","route":{"origin":"114.100000,22.300000","destination":"114.101000,22.300000","paths":[{"distance":"103","steps":[{"distance":"103","instruction":"向东步行103米","orientation":"东","polyline":"114.100000,22.300000;114.101000,22.300000"}]}]}})";
class Script final : public Transport {
public:
    std::string walking = summary;
    HttpResult rest{ClientStatus::Ok, 200, geometry, {}, 0, 0};
    unsigned geometry_calls = 0;
    nav::Point origin{114.1,22.3}, destination{114.101,22.3};
    nav::Point seen_origin{}, seen_destination{};
    HttpResult post(const HttpRequest& request) override {
        if (request.method == "tools/list") return {ClientStatus::Ok, 200,
            R"({"jsonrpc":"2.0","id":"list-1","result":{"resultType":"complete","tools":[{"name":"walking","inputSchema":{"type":"object","properties":{"origin":{"type":"string"},"destination":{"type":"string"}},"required":["origin","destination"]}}]}})", {}, 0, 0};
        std::unique_ptr<cJSON, decltype(&cJSON_Delete)> root(cJSON_Parse(walking.c_str()), cJSON_Delete);
        require(root != nullptr, "walking fixture JSON");
        cJSON_ReplaceItemInObject(root.get(), "id", cJSON_CreateString(request.request_id.c_str()));
        cJSON_AddStringToObject(cJSON_GetObjectItem(root.get(), "result"), "resultType", "complete");
        std::unique_ptr<char, decltype(&cJSON_free)> result(cJSON_PrintUnformatted(root.get()), cJSON_free);
        return {ClientStatus::Ok, 200, result.get(), {}, 0, 0};
    }
    HttpResult walking_geometry(nav::Point origin, nav::Point destination, std::string_view) override {
        ++geometry_calls; seen_origin = origin; seen_destination = destination; return rest;
    }
};
WalkingReply run(Script& script) {
    ClientConfig config;
    config.protocol_version = "2026-07-28";
    Client client(config, script);
    require(client.discover() == ClientStatus::Ok, "fixture discovery");
    nav::RequestToken token{"navigation", 4, 3, script.origin, {"door", script.destination}};
    return client.walking(token, token.origin, token.destination.position);
}
void replace(std::string& value, const std::string& before, const std::string& after) {
    const auto at = value.find(before); require(at != std::string::npos, "fixture mutation matches");
    value.replace(at, before.size(), after);
}
}
int main(int argc, char** argv) {
    const std::string name = argc == 2 ? argv[1] : "";
    Script script;
    if (name == "complete") {
        script.walking = R"({"jsonrpc":"2.0","id":"3","result":{"structuredContent":{"paths":[{"distance":"103","steps":[{"polyline":"114.1,22.3;114.101,22.3"}]}]}}})";
        require(run(script).status == ClientStatus::Ok && script.geometry_calls == 0, "complete MCP candidate used directly");
    } else if (name == "success") {
        const auto result = run(script);
        require(result.status == ClientStatus::Ok, "geometry completes successful MCP summary");
        require(script.geometry_calls == 1 && result.token.session_id == "navigation" &&
            result.token.generation == 4 && result.token.request_id == 3 && result.candidate.route.points.size() == 2,
            "whole candidate retains request identity");
        require(script.seen_origin.lng == 114.1 && script.seen_destination.lng == 114.101 &&
            result.candidate.route.coord_frame == "GCJ02", "provider coordinates retain datum");
    } else if (name == "actual_pair_1" || name == "actual_pair_2") {
        const bool first = name == "actual_pair_1";
        script.walking = fixture(first ? "fixtures/geometry/pair_1_mcp.json" :
            "fixtures/geometry/pair_2_mcp.json");
        script.rest.body = fixture(first ? "fixtures/geometry/pair_1_rest.json" :
            "fixtures/geometry/pair_2_rest.json");
        RouteSummary expected;
        require(parse_walking_summary(script.walking, first ? "4" : "5", expected) == ParseStatus::Ok,
            "actual MCP summary parsed");
        script.origin = expected.origin; script.destination = expected.destination;
        const auto result = run(script);
        require(result.status == ClientStatus::Ok && script.geometry_calls == 1 &&
            result.candidate.total_distance_m == (first ? 542 : 522), "actual paired geometry completes route");
    } else if (name == "qps") {
        script.rest.body = fixture("fixtures/geometry/quota_10021.json");
        const auto result = run(script);
        require(result.status == ClientStatus::Quota && script.geometry_calls == 1, "actual QPS 10021 preserves quota");
        require(classify_provider_response(script.rest.body).state == ServiceState::Quota, "shared QPS classification");
        RouteRetrySchedule retry; retry.finish(result.status, 1000);
        require(!retry.due(2999) && retry.due(3000), "QPS result observes route retry deadline");
    } else if (name == "actual_quota") {
        script.walking = fixture("fixtures/routes/candidate_route01.json");
        script.origin = {114.177692,22.332921}; script.destination = {114.179502,22.333517};
        script.rest.body = fixture("fixtures/geometry/quota_10045.json");
        require(run(script).status == ClientStatus::Quota && script.geometry_calls == 1, "actual 10045 retains quota outcome");
    } else if (name == "transport") {
        script.rest.status = ClientStatus::Tls;
        require(run(script).status == ClientStatus::Tls && script.geometry_calls == 1, "REST TLS outcome preserved");
    } else if (name == "endpoints") {
        replace(script.rest.body, "114.101000,22.300000", "114.102000,22.300000");
        require(run(script).status == ClientStatus::Route && script.geometry_calls == 1, "different route endpoints rejected");
    } else if (name == "distance") {
        replace(script.rest.body, "\"distance\":\"103\"", "\"distance\":\"150\"");
        require(run(script).status == ClientStatus::Route && script.geometry_calls == 1, "different route distance rejected");
    } else if (name == "steps") {
        replace(script.rest.body, "向东步行103米", "向西步行103米");
        require(run(script).status == ClientStatus::Route && script.geometry_calls == 1, "different step instructions rejected");
    } else if (name == "datum") {
        replace(script.rest.body, "\"status\":\"1\"", "\"coord_frame\":\"WGS84\",\"status\":\"1\"");
        require(run(script).status == ClientStatus::Route && script.geometry_calls == 1, "different route datum rejected");
    } else if (name == "polyline") {
        replace(script.rest.body, "114.100000,22.300000;114.101000,22.300000", "114.100000,22.301000;114.101000,22.301000");
        require(run(script).status == ClientStatus::Route && script.geometry_calls == 1, "geometry endpoints agree with declared route");
    } else if (name == "snap") {
        replace(script.rest.body, "114.100000,22.300000;114.101000,22.300000", "114.100000,22.300400;114.101000,22.300000");
        require(run(script).status == ClientStatus::Ok && script.geometry_calls == 1, "road snapping fits core endpoint tolerance");
    } else if (name == "join") {
        script.walking = R"({"jsonrpc":"2.0","id":"3","result":{"structuredContent":{"route":{"origin":"114.100000,22.300000","destination":"114.101000,22.300000","paths":[{"distance":"103","steps":[{"distance":"51","instruction":"向东","orientation":"东"},{"distance":"52","instruction":"继续向东","orientation":"东"}]}]}}}})";
        script.rest.body = R"({"status":"1","info":"OK","infocode":"10000","route":{"origin":"114.100000,22.300000","destination":"114.101000,22.300000","paths":[{"distance":"103","steps":[{"distance":"51","instruction":"向东","orientation":"东","polyline":"114.100000,22.300000;114.100500,22.300000"},{"distance":"52","instruction":"继续向东","orientation":"东","polyline":"114.100500,22.300050;114.101000,22.300000"}]}]}})";
        require(run(script).status == ClientStatus::Route && script.geometry_calls == 1, "adjoining step endpoints agree");
    } else return 2;
    std::cout << "{\"fixture\":\"" << name << "\",\"result\":\"passed\"}\n";
}
