#pragma once
#include "json_helpers.hpp"
#include "mcp_client.hpp"

namespace nav::mcp::detail {
inline std::string serialize(const cJSON* json) {
    std::unique_ptr<char, decltype(&cJSON_free)> text(cJSON_PrintUnformatted(json), cJSON_free);
    return text ? text.get() : "";
}
inline bool legacy(std::string_view version) {
    return version == "2025-11-25" || version == "2025-06-18" || version == "2025-03-26";
}
inline Json result_json(const HttpResult& exchange) {
    auto json = parse(exchange.body);
    if (!json || !cJSON_IsObject(member(json.get(), "result"))) return Json(nullptr, cJSON_Delete);
    return json;
}
}
