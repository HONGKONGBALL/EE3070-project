#include "route_parser.hpp"
#include "json_helpers.hpp"
#include "provider_status.hpp"
#include "tool_payload.hpp"
#include <cstring>

namespace nav::mcp {
namespace {
bool coordinate(const char*& cursor, double& value, char separator) {
    char* end = nullptr;
    value = std::strtod(cursor, &end);
    if (end == cursor || !std::isfinite(value) || *end != separator) return false;
    cursor = separator ? end + 1 : end;
    return true;
}
ParseStatus polyline(std::string_view encoded, Route& route) {
    if (encoded.empty()) return ParseStatus::GeometryRequired;
    const std::string storage(encoded);
    const char* cursor = storage.c_str();
    while (*cursor) {
        Point point;
        if (!coordinate(cursor, point.lng, ',')) return ParseStatus::InvalidRoute;
        char* end = nullptr;
        point.lat = std::strtod(cursor, &end);
        if (end == cursor || !std::isfinite(point.lat) || (*end != ';' && *end != '\0') ||
            point.lng < -180 || point.lng > 180 || point.lat < -90 || point.lat > 90)
            return ParseStatus::InvalidRoute;
        if (route.points.empty() || route.points.back().lng != point.lng || route.points.back().lat != point.lat) {
            if (route.points.size() == kPointCapacity) return ParseStatus::Capacity;
            route.points.push_back(point);
        }
        if (*end == '\0') break;
        cursor = end + 1;
        if (!*cursor) return ParseStatus::InvalidRoute;
    }
    return ParseStatus::Ok;
}
ParseStatus parse_path(const cJSON* data, ParsedRoute& parsed) {
    const auto service = detail::provider_parse_status(data);
    if (service != ParseStatus::Ok) return service;
    const auto* route = detail::member(data, "route");
    if (!route) route = data;
    const auto* paths = detail::member(route, "paths");
    if (!cJSON_IsArray(paths) || cJSON_GetArraySize(paths) == 0) return ParseStatus::RouteError;
    const auto* path = cJSON_GetArrayItem(paths, 0);
    const auto* steps = detail::member(path, "steps");
    if (!cJSON_IsArray(steps) || cJSON_GetArraySize(steps) == 0) return ParseStatus::RouteError;
    if (cJSON_GetArraySize(steps) > static_cast<int>(kStepCapacity)) return ParseStatus::Capacity;
    if (!detail::number(detail::member(path, "distance"), parsed.total_distance_m) || parsed.total_distance_m < 0)
        return ParseStatus::InvalidRoute;
    parsed.route.coord_frame = "GCJ02";
    const auto label = detail::text(detail::member(data, "coord_frame"));
    if (!label.empty()) {
        if (label != "GCJ02" && label != "WGS84") return ParseStatus::InvalidRoute;
        parsed.route.coord_frame = std::string(label);
    }
    parsed.route.points.reserve(kPointCapacity);
    parsed.route.steps.reserve(static_cast<std::size_t>(cJSON_GetArraySize(steps)));
    const cJSON* step = nullptr;
    cJSON_ArrayForEach(step, steps) {
        const auto result = polyline(detail::text(detail::member(step, "polyline")), parsed.route);
        if (result != ParseStatus::Ok) return result;
        if (parsed.route.points.size() < 2) return ParseStatus::InvalidRoute;
        const auto end = parsed.route.points.size() - 1;
        if (!parsed.route.steps.empty() && end <= parsed.route.steps.back().end_point) return ParseStatus::InvalidRoute;
        auto action = detail::text(detail::member(step, "action"));
        if (action.empty()) action = detail::text(detail::member(step, "instruction"));
        const auto instruction = detail::text(detail::member(step, "instruction"));
        const bool crossing = cJSON_IsTrue(detail::member(step, "crossing")) ||
            instruction.find("人行横道") != std::string_view::npos || instruction.find("天桥") != std::string_view::npos ||
            instruction.find("地下通道") != std::string_view::npos;
        parsed.route.steps.push_back({end, std::string(action), crossing});
    }
    return ParseStatus::Ok;
}
}
ParseStatus parse_walking_response(std::string_view response, std::string_view request_id, ParsedRoute& candidate) {
    const auto payload = detail::tool_payload(response, request_id);
    if (payload.status != ParseStatus::Ok) return payload.status;
    ParsedRoute parsed;
    parsed.request_id = std::string(request_id);
    const auto status = parse_path(payload.data, parsed);
    if (status == ParseStatus::Ok) candidate = std::move(parsed);
    return status;
}
}
