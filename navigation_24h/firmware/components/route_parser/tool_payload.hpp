#pragma once
#include "route_parser.hpp"
#include "json_helpers.hpp"

namespace nav::mcp::detail {
struct ToolPayload {
    ParseStatus status = ParseStatus::Ok;
    Json envelope{nullptr, cJSON_Delete};
    Json embedded{nullptr, cJSON_Delete};
    const cJSON* data = nullptr;
};
inline ToolPayload tool_payload(std::string_view response, std::string_view request_id) {
    ToolPayload payload;
    if (response.size() > kResponseCapacity) { payload.status = ParseStatus::Capacity; return payload; }
    payload.envelope = parse(response);
    if (!payload.envelope) { payload.status = ParseStatus::InvalidJson; return payload; }
    const auto* message = payload.envelope.get();
    if (text(member(message, "jsonrpc")) != "2.0" || !member(message, "id")) {
        payload.status = ParseStatus::InvalidRpc; return payload;
    }
    if (!id_matches(member(message, "id"), request_id)) { payload.status = ParseStatus::IdMismatch; return payload; }
    if (cJSON_IsObject(member(message, "error"))) { payload.status = ParseStatus::RouteError; return payload; }
    const auto* result = member(message, "result");
    if (!cJSON_IsObject(result)) { payload.status = ParseStatus::InvalidRpc; return payload; }
    if (cJSON_IsTrue(member(result, "isError"))) { payload.status = ParseStatus::RouteError; return payload; }
    const auto* structured = member(result, "structuredContent");
    if (cJSON_IsObject(structured)) { payload.data = structured; return payload; }
    const auto* content = member(result, "content");
    const cJSON* item = nullptr;
    cJSON_ArrayForEach(item, content) {
        if (text(member(item, "type")) != "text") continue;
        payload.embedded = parse(text(member(item, "text")));
        if (payload.embedded) { payload.data = payload.embedded.get(); return payload; }
    }
    payload.status = ParseStatus::RouteError;
    return payload;
}
}
