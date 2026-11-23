#include "route_parser.hpp"
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>
#include <string_view>

using nav::mcp::Format;
using nav::mcp::ParseStatus;
using nav::mcp::ParsedRoute;
using nav::mcp::RpcStream;

static void require(bool condition, std::string_view message) {
    if (!condition) { std::cerr << message << '\n'; std::exit(1); }
}
static std::string route_data() {
    return R"({"route":{"paths":[{"distance":"220","steps":[{"action":"左转","polyline":"114.1700,22.3000;114.1710,22.3000"},{"action":"直行","polyline":"114.1710,22.3000;114.1710,22.3010"}]}]}})";
}
static std::string envelope(std::string_view data) {
    return "{\"jsonrpc\":\"2.0\",\"id\":\"r1\",\"result\":{\"structuredContent\":" + std::string(data) + "}}";
}
static void tool_errors() {
    ParsedRoute candidate;
    candidate.route.points = {{1, 2}, {2, 3}};
    const auto original = candidate.route.points;
    for (const auto& body : {
        R"({"jsonrpc":"2.0","id":"r1","error":{"code":-32603,"message":"service"}})",
        R"({"jsonrpc":"2.0","id":"r1","result":{"isError":true,"content":[{"type":"text","text":"error"}]}})",
        R"({"jsonrpc":"2.0","id":"r1","result":{"structuredContent":{"route":{"paths":[]}}}})"}) {
        require(nav::mcp::parse_walking_response(body, "r1", candidate) == ParseStatus::RouteError, "TC20 route error");
        require(candidate.route.points.size() == original.size(), "TC20 candidate remains atomic");
    }
}
static void fragmentation() {
    const auto body = envelope(route_data());
    const auto sse = ": keepalive\r\nevent: message\r\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{}}\r\n\r\ndata: " + body + "\r\n\r\n";
    for (std::size_t stride = 1; stride <= 73; ++stride) {
        RpcStream stream(Format::Sse, "r1");
        for (std::size_t offset = 0; offset < sse.size(); offset += stride)
            require(stream.feed(std::string_view(sse).substr(offset, stride)) == ParseStatus::Ok, "TC21 SSE feed");
        require(stream.finish() == ParseStatus::Ok, "TC21 SSE finish");
        ParsedRoute parsed;
        require(nav::mcp::parse_walking_response(stream.response(), "r1", parsed) == ParseStatus::Ok, "TC21 normalized parse");
        require(parsed.route.points.size() == 3 && parsed.route.steps.size() == 2, "TC21 geometry counts");
        require(parsed.route.steps[0].end_point == 1 && parsed.route.steps[1].end_point == 2, "TC21 step ranges");
        require(parsed.route.coord_frame == "GCJ02" && parsed.total_distance_m == 220, "TC21 coordinates and distance");
    }
    RpcStream json(Format::Json, "r1");
    for (const auto c : body) require(json.feed(std::string_view(&c, 1)) == ParseStatus::Ok, "TC21 JSON feed");
    require(json.finish() == ParseStatus::Ok && json.response() == body, "TC21 JSON response");
    RpcStream cr_lines(Format::Sse, "r1");
    require(cr_lines.feed("data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\r\rdata: " + body + "\r\r") == ParseStatus::Ok &&
            cr_lines.finish() == ParseStatus::Ok, "TC21 CR line endings");
    std::string escaped;
    for (const char c : route_data()) {
        if (c == '"' || c == '\\') escaped += '\\';
        escaped += c;
    }
    ParsedRoute text_route;
    require(nav::mcp::parse_walking_response("{\"jsonrpc\":\"2.0\",\"id\":\"r1\",\"result\":{\"content\":[{\"type\":\"text\",\"text\":\"" + escaped + "\"}]}}", "r1", text_route) == ParseStatus::Ok,
            "TC21 text tool content");
}
static std::string many_points(std::size_t count) {
    std::string polyline;
    for (std::size_t i = 0; i < count; ++i) {
        if (i) polyline += ';';
        polyline += "114.17," + std::to_string(22.30 + static_cast<double>(i) * .000001);
    }
    return envelope("{\"route\":{\"paths\":[{\"distance\":\"1\",\"steps\":[{\"polyline\":\"" + polyline + "\"}]}]}}");
}
static void capacity() {
    ParsedRoute parsed;
    require(nav::mcp::parse_walking_response(many_points(2048), "r1", parsed) == ParseStatus::Ok, "TC22 exact point capacity");
    require(parsed.route.points.size() == 2048, "TC22 exact count");
    require(nav::mcp::parse_walking_response(many_points(2049), "r1", parsed) == ParseStatus::Capacity, "TC22 point overflow");
    require(parsed.route.points.size() == 2048, "TC22 atomic candidate");
    auto padded = envelope(route_data());
    padded.resize(nav::mcp::kResponseCapacity, ' ');
    RpcStream exact(Format::Json, "r1");
    require(exact.feed(padded) == ParseStatus::Ok && exact.finish() == ParseStatus::Ok, "TC22 exact bytes");
    RpcStream overflow(Format::Json, "r1");
    padded.push_back(' ');
    require(overflow.feed(padded) == ParseStatus::Capacity, "TC22 byte overflow");
    for (std::size_t count : {std::size_t(256), std::size_t(257)}) {
        std::string steps;
        for (std::size_t i = 0; i < count; ++i) {
            if (i) steps += ',';
            steps += "{\"polyline\":\"114.17," + std::to_string(22.30 + i * .000001) +
                ";114.17," + std::to_string(22.30 + (i + 1) * .000001) + "\"}";
        }
        const auto response = envelope("{\"paths\":[{\"distance\":\"1\",\"steps\":[" + steps + "]}]}");
        const auto expected = count == 256 ? ParseStatus::Ok : ParseStatus::Capacity;
        require(nav::mcp::parse_walking_response(response, "r1", parsed) == expected, "TC22 step capacity");
    }
}
static void rpc() {
    ParsedRoute parsed;
    require(nav::mcp::parse_walking_response(envelope(route_data()), "other", parsed) == ParseStatus::IdMismatch, "RPC correlation");
    require(nav::mcp::parse_walking_response("{}", "r1", parsed) == ParseStatus::InvalidRpc, "RPC structure");
    require(nav::mcp::parse_walking_response("{", "r1", parsed) == ParseStatus::InvalidJson, "JSON truncation");
    require(nav::mcp::parse_walking_response(envelope(route_data()) + "junk", "r1", parsed) == ParseStatus::InvalidJson, "JSON trailing text");
    RpcStream incomplete(Format::Sse, "r1");
    require(incomplete.feed("data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\"}\n\n") == ParseStatus::Ok, "notification");
    require(incomplete.finish() == ParseStatus::InvalidRpc, "final response required");
    auto invalid_utf8 = envelope(route_data());
    invalid_utf8[invalid_utf8.find("左")] = static_cast<char>(0xff);
    require(nav::mcp::parse_walking_response(invalid_utf8, "r1", parsed) == ParseStatus::InvalidJson, "UTF8 validation");
}
static void geometry() {
    ParsedRoute parsed;
    auto body = envelope(route_data());
    body.replace(body.find("114.1700"), 8, "999.1700");
    require(nav::mcp::parse_walking_response(body, "r1", parsed) == ParseStatus::InvalidRoute, "coordinate range");
    body = envelope(route_data());
    body.replace(body.find("114.1700"), 8, "NaN");
    require(nav::mcp::parse_walking_response(body, "r1", parsed) == ParseStatus::InvalidRoute, "finite coordinate");
}
int main(int argc, char** argv) {
    const std::string_view selected = argc > 1 ? argv[1] : "all";
    if (selected == "TC20" || selected == "all") tool_errors();
    if (selected == "TC21" || selected == "all") fragmentation();
    if (selected == "TC22" || selected == "all") capacity();
    if (selected == "parser_rpc" || selected == "all") rpc();
    if (selected == "parser_geometry" || selected == "all") geometry();
    std::cout << selected << " passed\n";
}
