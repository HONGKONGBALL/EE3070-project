#include "mcp_client.hpp"
#include <cstdlib>
#include <iostream>
#include <regex>
#include <string>
#include <vector>
using namespace nav::mcp;
static void require(bool condition, const char* description) {
    if (!condition) { std::cerr << description << '\n'; std::exit(1); }
}
class ScriptedTransport final : public Transport {
public:
    std::vector<HttpRequest> requests;
    std::string negotiated_version = "2025-11-25";
    HttpResult post(const HttpRequest& request) override {
        requests.push_back(request);
        HttpResult result;
        result.http_status = request.notification ? 202 : 200;
        if (request.method == "initialize") {
            result.session_id = "opaque-session";
            result.body = "{\"jsonrpc\":\"2.0\",\"id\":\"" + request.request_id + "\",\"result\":{\"protocolVersion\":\"" + negotiated_version + "\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"synthetic\",\"version\":\"1\"}}}";
        } else if (request.method == "tools/list") {
            result.body = "{\"jsonrpc\":\"2.0\",\"id\":\"" + request.request_id + "\",\"result\":{\"tools\":[{\"name\":\"provider_walking\",\"inputSchema\":{\"type\":\"object\",\"required\":[\"origin\",\"destination\"],\"properties\":{\"origin\":{\"type\":\"string\"},\"destination\":{\"type\":\"string\"}}}}]}}";
        } else if (request.method == "tools/call") {
            result.body = "{\"jsonrpc\":\"2.0\",\"id\":\"" + request.request_id + "\",\"result\":{\"structuredContent\":{\"paths\":[{\"distance\":\"100\",\"steps\":[{\"polyline\":\"114.1,22.3;114.2,22.3\"}]}]}}}";
        }
        if (request.protocol_version == "2026-07-28") {
            const auto at = result.body.find("\"result\":{");
            if (at != std::string::npos) result.body.insert(at + 10, "\"resultType\":\"complete\",");
        }
        return result;
    }
};
static void version(const std::string& protocol, const std::string& negotiated = "2025-11-25") {
    ScriptedTransport transport;
    transport.negotiated_version = negotiated;
    ClientConfig config;
    config.protocol_version = protocol;
    Client client(config, transport);
    require(client.discover() == ClientStatus::Ok, "discovery");
    require(client.discovery().walking_tool == "provider_walking", "dynamic tool name");
    nav::RequestToken token;
    token.session_id = "navigation-session";
    token.generation = 7;
    token.request_id = 33;
    auto reply = client.walking(token, {114.1,22.3}, {114.2,22.3});
    require(reply.status == ClientStatus::Ok, "walking response");
    require(reply.token.session_id == token.session_id && reply.token.generation == 7 && reply.token.request_id == 33, "candidate token");
    require(reply.candidate.route.points.size() == 2, "candidate route");
    for (const auto* field : {"origin", "destination"}) {
        const std::regex coordinate(std::string("\"") + field +
            "\"\\s*:\\s*\"(-?[0-9]+(?:\\.[0-9]{1,6})?,-?[0-9]+(?:\\.[0-9]{1,6})?)\"");
        require(std::regex_search(transport.requests.back().body, coordinate),
                "provider coordinates use at most six fractional digits");
    }
    if (protocol == "2025-11-25") {
        require(transport.requests.size() == 4, "legacy request lifecycle");
        require(transport.requests[0].method == "initialize" && transport.requests[0].session_id.empty(), "legacy initialize");
        require(transport.requests[1].notification && transport.requests[1].method == "notifications/initialized", "legacy initialized");
        require(transport.requests[2].session_id == "opaque-session", "legacy session header");
        require(transport.requests[1].protocol_version == negotiated && transport.requests[2].protocol_version == negotiated &&
                transport.requests[3].protocol_version == negotiated, "negotiated legacy version");
        require(client.discovery().protocol_version == negotiated, "actual legacy provider version");
    } else {
        require(transport.requests.size() == 2, "modern request lifecycle");
        require(transport.requests[0].body.find("io.modelcontextprotocol/protocolVersion") != std::string::npos, "modern version metadata");
        require(transport.requests[1].body.find("io.modelcontextprotocol/clientCapabilities") != std::string::npos, "modern capabilities metadata");
        require(transport.requests[1].session_id.empty(), "modern per-request state");
    }
}
int main() {
    version("2025-11-25");
    version("2025-11-25", "2025-03-26");
    version("2025-11-25", "2025-06-18");
    version("2026-07-28");
    ScriptedTransport incompatible;
    incompatible.negotiated_version = "2024-11-05";
    Client unsupported(ClientConfig{}, incompatible);
    require(unsupported.discover() == ClientStatus::Protocol && incompatible.requests.size() == 1,
            "incompatible negotiated protocol");
    std::cout << "TC18 lifecycle passed\n";
}
