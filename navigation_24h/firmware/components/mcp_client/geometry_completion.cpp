#include "geometry_completion.hpp"
#include "client_json.hpp"
#include <algorithm>
#include <cmath>

namespace nav::mcp::detail {
bool same_endpoint(Point left, Point right) {
    return std::abs(left.lng - right.lng) <= 0.000001 && std::abs(left.lat - right.lat) <= 0.000001;
}
namespace {
double meters(Point left, Point right) {
    constexpr double radians = 3.14159265358979323846 / 180.0;
    const auto latitude = std::sin((right.lat - left.lat) * radians / 2);
    const auto longitude = std::sin((right.lng - left.lng) * radians / 2);
    const auto arc = latitude * latitude + std::cos(left.lat * radians) *
        std::cos(right.lat * radians) * longitude * longitude;
    return 12742000 * std::asin(std::sqrt(std::clamp(arc, 0.0, 1.0)));
}
bool same_path(const RouteSummary& expected, const RouteSummary& actual) {
    if (expected.coord_frame != "GCJ02" || actual.coord_frame != expected.coord_frame ||
        !actual.has_geometry || !same_endpoint(expected.origin, actual.origin) ||
        !same_endpoint(expected.destination, actual.destination) ||
        expected.total_distance_m != actual.total_distance_m || expected.steps.size() != actual.steps.size()) return false;
    for (std::size_t index = 0; index < expected.steps.size(); ++index) {
        const auto& left = expected.steps[index]; const auto& right = actual.steps[index];
        if (left.distance_m != right.distance_m || left.instruction != right.instruction ||
            left.orientation != right.orientation) return false;
    }
    return true;
}
bool connected_steps(const cJSON* data, const RouteSummary& summary, const Route& geometry) {
    const auto* route = member(data, "route");
    if (!route) route = data;
    const auto* steps = member(cJSON_GetArrayItem(member(route, "paths"), 0), "steps");
    std::size_t previous = 0;
    for (std::size_t index = 0; index < summary.steps.size(); ++index) {
        const auto raw = text(member(cJSON_GetArrayItem(steps, static_cast<int>(index)), "polyline"));
        char* latitude = nullptr;
        const auto longitude = std::strtod(raw.data(), &latitude);
        const Point beginning{longitude, std::strtod(latitude + 1, nullptr)};
        if (index && meters(geometry.points[previous], beginning) > 1) return false;
        const auto end = geometry.steps[index].end_point;
        double length = 0;
        for (auto point = previous + 1; point <= end; ++point)
            length += meters(geometry.points[point - 1], geometry.points[point]);
        if (std::abs(length - summary.steps[index].distance_m) >
            std::max(5.0, summary.steps[index].distance_m * 0.1)) return false;
        previous = end;
    }
    return true;
}
}
ParseStatus complete_geometry(std::string_view body, const RouteSummary& expected,
                             std::string_view request_id, ParsedRoute& candidate) {
    if (body.size() > kResponseCapacity) return ParseStatus::Capacity;
    const auto service = classify_provider_response(body);
    if (service.state == ServiceState::Quota) return ParseStatus::ServiceQuota;
    if (service.state != ServiceState::Ready) return ParseStatus::RouteError;
    auto document = parse(body);
    Json rpc(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(rpc.get(), "jsonrpc", "2.0");
    cJSON_AddStringToObject(rpc.get(), "id", std::string(request_id).c_str());
    auto* result = cJSON_AddObjectToObject(rpc.get(), "result");
    cJSON_AddItemToObject(result, "structuredContent", document.release());
    const auto wrapped = serialize(rpc.get());
    RouteSummary actual;
    auto status = parse_walking_summary(wrapped, request_id, actual);
    if (status != ParseStatus::Ok) return status;
    if (!same_path(expected, actual)) return ParseStatus::InvalidRoute;
    ParsedRoute parsed;
    status = parse_walking_response(wrapped, request_id, parsed);
    if (status != ParseStatus::Ok) return status;
    if (!connected_steps(member(result, "structuredContent"), actual, parsed.route)) return ParseStatus::InvalidRoute;
    if (meters(parsed.route.points.front(), expected.origin) > 100 ||
        meters(parsed.route.points.back(), expected.destination) > 100) return ParseStatus::InvalidRoute;
    double length = 0;
    for (std::size_t index = 1; index < parsed.route.points.size(); ++index)
        length += meters(parsed.route.points[index - 1], parsed.route.points[index]);
    if (std::abs(length - actual.total_distance_m) > std::max(10.0, actual.total_distance_m * 0.1))
        return ParseStatus::InvalidRoute;
    candidate = std::move(parsed);
    return ParseStatus::Ok;
}
}
