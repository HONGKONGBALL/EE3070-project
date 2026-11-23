#include "mcp_client.hpp"
#include "client_json.hpp"
#include "schema_headers.hpp"
#include <algorithm>
#include <cctype>
#include <set>

namespace nav::mcp {
namespace {
bool compatible_schema(const cJSON* schema) {
    if (detail::text(detail::member(schema, "type")) != "object") return false;
    const auto* properties = detail::member(schema, "properties");
    for (const auto* name : {"origin", "destination"})
        if (detail::text(detail::member(detail::member(properties, name), "type")) != "string") return false;
    const auto* required = detail::member(schema, "required");
    if (required && !cJSON_IsArray(required)) return false;
    const cJSON* item = nullptr;
    cJSON_ArrayForEach(item, required) {
        const auto name = detail::text(item);
        if (name != "origin" && name != "destination") return false;
    }
    return true;
}
bool walking_tool(const cJSON* tool, std::string_view configured) {
    const auto name = detail::text(detail::member(tool, "name"));
    if (name.empty() || name.size() > 128) return false;
    if (!configured.empty()) return name == configured;
    std::string lower(name);
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return lower.find("walking") != std::string::npos ||
        detail::text(detail::member(tool, "description")).find("步行") != std::string_view::npos;
}
}
ClientStatus Client::list_tools() {
    std::string cursor;
    std::set<std::string> cursors;
    for (unsigned page = 0; page < 8; ++page) {
        detail::Json params(cJSON_CreateObject(), cJSON_Delete);
        if (!params) return ClientStatus::Capacity;
        if (!cursor.empty()) cJSON_AddStringToObject(params.get(), "cursor", cursor.c_str());
        last_ = call("tools/list", detail::serialize(params.get()), "list-" + std::to_string(++control_seq_));
        if (last_.status != ClientStatus::Ok) return last_.status;
        auto message = detail::result_json(last_);
        if (!message) return ClientStatus::Protocol;
        const auto* result = detail::member(message.get(), "result");
        const auto* tools = detail::member(result, "tools");
        if (!cJSON_IsArray(tools)) return ClientStatus::Protocol;
        const auto* meta = detail::member(result, "_meta");
        const auto* server = detail::member(meta, "io.modelcontextprotocol/serverInfo");
        if (cJSON_IsObject(server)) discovery_.server_info_json = detail::serialize(server);
        const cJSON* tool = nullptr;
        cJSON_ArrayForEach(tool, tools) {
            if (!walking_tool(tool, config_.walking_tool)) continue;
            const auto* schema = detail::member(tool, "inputSchema");
            if (!compatible_schema(schema)) return ClientStatus::Schema;
            std::vector<std::pair<std::string, std::string>> headers;
            if (!detail::schema_headers(schema, nullptr, headers)) return ClientStatus::Schema;
            const auto input = detail::serialize(schema);
            if (input.size() > 16384) return ClientStatus::Capacity;
            discovery_.walking_tool = std::string(detail::text(detail::member(tool, "name")));
            discovery_.input_schema_json = input;
            discovery_.output_schema_json = detail::serialize(detail::member(tool, "outputSchema"));
        }
        const auto next = detail::text(detail::member(result, "nextCursor"));
        if (next.empty()) return discovery_.walking_tool.empty() ? ClientStatus::Schema : ClientStatus::Ok;
        if (next.size() > 256 || !cursors.insert(std::string(next)).second) return ClientStatus::Protocol;
        cursor = std::string(next);
    }
    return ClientStatus::Capacity;
}
}
