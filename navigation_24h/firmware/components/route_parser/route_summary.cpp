#include "route_parser.hpp"
#include "provider_status.hpp"
#include "tool_payload.hpp"

namespace nav::mcp {
namespace {
bool endpoint(const cJSON* field, Point& result) {
    const auto value = detail::text(field);
    if (value.empty()) return false;
    const std::string storage(value);
    char* separator = nullptr;
    result.lng = std::strtod(storage.c_str(), &separator);
    if (separator == storage.c_str() || *separator != ',') return false;
    const char* lat = separator + 1;
    char* end = nullptr;
    result.lat = std::strtod(lat, &end);
    return end != lat && *end == '\0' && std::isfinite(result.lng) && std::isfinite(result.lat) &&
        result.lng >= -180 && result.lng <= 180 && result.lat >= -90 && result.lat <= 90;
}
}
ParseStatus parse_walking_summary(std::string_view response, std::string_view request_id, RouteSummary& summary) {
    const auto payload = detail::tool_payload(response, request_id);
    if (payload.status != ParseStatus::Ok) return payload.status;
    const auto service = detail::provider_parse_status(payload.data);
    if (service != ParseStatus::Ok) return service;
    const auto* route = detail::member(payload.data, "route");
    if (!route) route = payload.data;
    RouteSummary parsed;
    parsed.request_id = std::string(request_id);
    if (!endpoint(detail::member(route, "origin"), parsed.origin) ||
        !endpoint(detail::member(route, "destination"), parsed.destination)) return ParseStatus::InvalidRoute;
    const auto* paths = detail::member(route, "paths");
    if (!cJSON_IsArray(paths) || cJSON_GetArraySize(paths) == 0) return ParseStatus::RouteError;
    const auto* path = cJSON_GetArrayItem(paths, 0);
    if (!detail::number(detail::member(path, "distance"), parsed.total_distance_m) || parsed.total_distance_m < 0)
        return ParseStatus::InvalidRoute;
    const auto* steps = detail::member(path, "steps");
    if (!cJSON_IsArray(steps) || cJSON_GetArraySize(steps) == 0) return ParseStatus::RouteError;
    if (cJSON_GetArraySize(steps) > static_cast<int>(kStepCapacity)) return ParseStatus::Capacity;
    parsed.has_geometry = true;
    const cJSON* step = nullptr;
    cJSON_ArrayForEach(step, steps) {
        SummaryStep item;
        if (!detail::number(detail::member(step, "distance"), item.distance_m) || item.distance_m < 0)
            return ParseStatus::InvalidRoute;
        double duration = 0;
        if (detail::number(detail::member(step, "duration"), duration) && duration >= 0) item.duration_s = duration;
        item.instruction = std::string(detail::text(detail::member(step, "instruction")));
        item.orientation = std::string(detail::text(detail::member(step, "orientation")));
        parsed.has_geometry = parsed.has_geometry && !detail::text(detail::member(step, "polyline")).empty();
        parsed.steps.push_back(std::move(item));
    }
    const auto frame = detail::text(detail::member(payload.data, "coord_frame"));
    if (!frame.empty()) {
        if (frame != "WGS84" && frame != "GCJ02") return ParseStatus::InvalidRoute;
        parsed.coord_frame = std::string(frame);
    }
    summary = std::move(parsed);
    return ParseStatus::Ok;
}
}
