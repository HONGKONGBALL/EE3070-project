#include "geometry_completion.hpp"
#include <cmath>
#include <cstdio>
namespace nav::mcp::detail {
std::optional<std::string> walking_geometry_url(std::string_view endpoint, Point origin, Point destination) {
    constexpr std::string_view source = "https://mcp.amap.com/mcp?";
    if (endpoint.substr(0, source.size()) != source || endpoint.find_first_of("\r\n#") != std::string_view::npos)
        return std::nullopt;
    for (const auto point : {origin, destination})
        if (!std::isfinite(point.lng) || !std::isfinite(point.lat) || point.lng < -180 ||
            point.lng > 180 || point.lat < -90 || point.lat > 90) return std::nullopt;
    std::string_view key;
    auto query = endpoint.substr(source.size());
    while (!query.empty()) {
        const auto delimiter = query.find('&');
        const auto field = query.substr(0, delimiter);
        if (field.substr(0, 4) == "key=") {
            if (!key.empty()) return std::nullopt;
            key = field.substr(4);
            if (key.empty() || key.size() > 128) return std::nullopt;
            for (const auto byte : key)
                if (!((byte >= 'a' && byte <= 'z') || (byte >= 'A' && byte <= 'Z') ||
                      (byte >= '0' && byte <= '9') || byte == '_' || byte == '-')) return std::nullopt;
        }
        if (delimiter == std::string_view::npos) break;
        query.remove_prefix(delimiter + 1);
    }
    if (key.empty()) return std::nullopt;
    char points[160];
    std::snprintf(points, sizeof(points), "&origin=%.6f,%.6f&destination=%.6f,%.6f&output=JSON",
        origin.lng, origin.lat, destination.lng, destination.lat);
    return "https://restapi.amap.com/v3/direction/walking?key=" + std::string(key) + points;
}
}
