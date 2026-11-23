#include "mcp_client.hpp"
#include "client_json.hpp"
#include "schema_headers.hpp"
#include "geometry_completion.hpp"
#include <cmath>
#include <cstdio>

namespace nav::mcp {
ClientStatus classify_http_status(int status) {
    if (status >= 200 && status < 300) return ClientStatus::Ok;
    if (status == 401 || status == 403) return ClientStatus::Authentication;
    if (status == 429) return ClientStatus::Quota;
    if (status >= 500) return ClientStatus::Service;
    return ClientStatus::Protocol;
}
bool retryable_exchange(const HttpResult& result) {
    return result.status == ClientStatus::Transport || result.status == ClientStatus::Timeout ||
        (result.status == ClientStatus::Service && result.http_status >= 500);
}
const char* status_name(ClientStatus status) {
    switch (status) {
    case ClientStatus::Ok: return "OK";
    case ClientStatus::Configuration: return "MCP_CONFIGURATION";
    case ClientStatus::Transport: return "MCP_TRANSPORT";
    case ClientStatus::Timeout: return "MCP_TIMEOUT";
    case ClientStatus::Tls: return "MCP_TLS";
    case ClientStatus::Authentication: return "MCP_AUTHENTICATION";
    case ClientStatus::Quota: return "MCP_QUOTA";
    case ClientStatus::Protocol: return "MCP_PROTOCOL";
    case ClientStatus::Schema: return "MCP_SCHEMA";
    case ClientStatus::Service: return "MCP_SERVICE";
    case ClientStatus::Route: return "ROUTE_ERROR";
    case ClientStatus::Capacity: return "ROUTE_CAPACITY";
    }
    return "MCP_UNKNOWN";
}
Client::Client(ClientConfig config, Transport& transport)
    : config_(std::move(config)), transport_(transport) {}
void Client::reset() {
    ready_ = false;
    session_id_.clear();
    discovery_ = {};
}
HttpResult Client::call(std::string_view method, std::string_view params_json,
                        std::string request_id, std::string_view tool) {
    auto params = detail::parse(params_json);
    detail::Json body(cJSON_CreateObject(), cJSON_Delete);
    if (!params || !body) return {ClientStatus::Capacity, 0, {}, {}, 0, 0};
    cJSON_AddStringToObject(body.get(), "jsonrpc", "2.0");
    cJSON_AddStringToObject(body.get(), "id", request_id.c_str());
    cJSON_AddStringToObject(body.get(), "method", std::string(method).c_str());
    if (!detail::legacy(config_.protocol_version)) {
        auto* meta = cJSON_AddObjectToObject(params.get(), "_meta");
        cJSON_AddStringToObject(meta, "io.modelcontextprotocol/protocolVersion", config_.protocol_version.c_str());
        cJSON_AddObjectToObject(meta, "io.modelcontextprotocol/clientCapabilities");
        auto* info = cJSON_AddObjectToObject(meta, "io.modelcontextprotocol/clientInfo");
        cJSON_AddStringToObject(info, "name", "Navigation24h");
        cJSON_AddStringToObject(info, "version", "1.0");
    }
    cJSON_AddItemToObject(body.get(), "params", params.release());
    HttpRequest request{detail::serialize(body.get()), std::move(request_id), std::string(method),
                        std::string(tool), config_.protocol_version, session_id_, false};
    if (method == "tools/call") {
        auto schema = detail::parse(discovery_.input_schema_json);
        const auto* arguments = detail::member(detail::member(body.get(), "params"), "arguments");
        if (!schema || !detail::valid_arguments(schema.get(), arguments) ||
            !detail::schema_headers(schema.get(), arguments, request.extra_headers))
            return {ClientStatus::Schema, 0, {}, {}, 0, 0};
    }
    if (request.body.empty()) return {ClientStatus::Capacity, 0, {}, {}, 0, 0};
    auto exchange = transport_.post(request);
    if (exchange.status != ClientStatus::Ok) return exchange;
    auto message = detail::parse(exchange.body);
    if (!message || detail::text(detail::member(message.get(), "jsonrpc")) != "2.0" ||
        !detail::id_matches(detail::member(message.get(), "id"), request.request_id)) {
        exchange.status = ClientStatus::Protocol;
        return exchange;
    }
    const bool result = cJSON_IsObject(detail::member(message.get(), "result"));
    const bool error = cJSON_IsObject(detail::member(message.get(), "error"));
    if (result == error) exchange.status = ClientStatus::Protocol;
    else if (error) exchange.status = ClientStatus::Service;
    else if (!detail::legacy(config_.protocol_version) &&
             detail::text(detail::member(detail::member(message.get(), "result"), "resultType")) != "complete")
        exchange.status = ClientStatus::Protocol;
    return exchange;
}
ClientStatus Client::initialize() {
    const std::string params = "{\"protocolVersion\":\"" + config_.protocol_version +
        "\",\"capabilities\":{},\"clientInfo\":{\"name\":\"Navigation24h\",\"version\":\"1.0\"}}";
    last_ = call("initialize", params, "init-" + std::to_string(++control_seq_));
    if (last_.status != ClientStatus::Ok) return last_.status;
    auto message = detail::result_json(last_);
    if (!message) return ClientStatus::Protocol;
    const auto* result = detail::member(message.get(), "result");
    const auto version = detail::text(detail::member(result, "protocolVersion"));
    if (!detail::legacy(version)) return ClientStatus::Protocol;
    config_.protocol_version = std::string(version);
    session_id_ = last_.session_id;
    discovery_.server_info_json = detail::serialize(detail::member(result, "serverInfo"));
    HttpRequest notification{R"({"jsonrpc":"2.0","method":"notifications/initialized"})", {},
        "notifications/initialized", {}, config_.protocol_version, session_id_, true};
    last_ = transport_.post(notification);
    if (last_.status != ClientStatus::Ok) return last_.status;
    if (last_.http_status != 202) return ClientStatus::Protocol;
    return ClientStatus::Ok;
}
ClientStatus Client::discover() {
    reset();
    if (!detail::legacy(config_.protocol_version) && config_.protocol_version != "2026-07-28")
        return ClientStatus::Configuration;
    if (detail::legacy(config_.protocol_version)) {
        const auto status = initialize();
        if (status != ClientStatus::Ok) return status;
    }
    const auto status = list_tools();
    if (status == ClientStatus::Ok) {
        ready_ = true;
        discovery_.protocol_version = config_.protocol_version;
    }
    return status;
}
WalkingReply Client::walking(const RequestToken& token, Point origin, Point destination) {
    WalkingReply reply;
    reply.token = token;
    if (!ready_) { reply.status = ClientStatus::Configuration; return reply; }
    for (const auto point : {origin, destination}) {
        if (!std::isfinite(point.lng) || !std::isfinite(point.lat) || point.lng < -180 || point.lng > 180 || point.lat < -90 || point.lat > 90) {
            reply.status = ClientStatus::Schema; return reply;
        }
    }
    char source[64], target[64];
    std::snprintf(source, sizeof(source), "%.6f,%.6f", origin.lng, origin.lat);
    std::snprintf(target, sizeof(target), "%.6f,%.6f", destination.lng, destination.lat);
    detail::Json params(cJSON_CreateObject(), cJSON_Delete);
    if (!params) { reply.status = ClientStatus::Capacity; return reply; }
    cJSON_AddStringToObject(params.get(), "name", discovery_.walking_tool.c_str());
    auto* arguments = cJSON_AddObjectToObject(params.get(), "arguments");
    cJSON_AddStringToObject(arguments, "origin", source);
    cJSON_AddStringToObject(arguments, "destination", target);
    const auto request_id = std::to_string(token.request_id);
    last_ = call("tools/call", detail::serialize(params.get()), request_id, discovery_.walking_tool);
    reply.exchange = last_;
    reply.status = last_.status;
    if (last_.http_status == 404 && !session_id_.empty()) reset();
    if (reply.status != ClientStatus::Ok) return reply;
    auto parsed = parse_walking_response(last_.body, request_id, reply.candidate);
    reply.parse_status = parsed;
    if (parsed == ParseStatus::GeometryRequired) {
        RouteSummary summary;
        parsed = parse_walking_summary(last_.body, request_id, summary);
        if (parsed == ParseStatus::Ok && (summary.coord_frame != "GCJ02" ||
            !detail::same_endpoint(summary.origin, origin) || !detail::same_endpoint(summary.destination, destination)))
            parsed = ParseStatus::InvalidRoute;
        if (parsed == ParseStatus::Ok) {
            reply.geometry_fallback_used = true;
            last_ = transport_.walking_geometry(origin, destination, request_id);
            reply.exchange = last_;
            if (last_.status != ClientStatus::Ok) { reply.status = last_.status; return reply; }
            parsed = detail::complete_geometry(last_.body, summary, request_id, reply.candidate);
        }
    }
    reply.parse_status = parsed;
    if (parsed == ParseStatus::Capacity) reply.status = ClientStatus::Capacity;
    else if (parsed == ParseStatus::ServiceQuota) reply.status = ClientStatus::Quota;
    else if (parsed != ParseStatus::Ok) reply.status = ClientStatus::Route;
    return reply;
}
}
