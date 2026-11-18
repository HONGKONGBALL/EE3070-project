#pragma once
#include "mcp_client.hpp"
#include <optional>
namespace nav::mcp::detail {
bool same_endpoint(Point left, Point right);
std::optional<std::string> walking_geometry_url(std::string_view endpoint, Point origin, Point destination);
ParseStatus complete_geometry(std::string_view body, const RouteSummary& expected,
                             std::string_view request_id, ParsedRoute& candidate);
}
