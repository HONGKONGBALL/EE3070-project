#pragma once
#include "nav_core.hpp"
#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace nav::mcp {
inline constexpr std::size_t kResponseCapacity = 256 * 1024;
inline constexpr std::size_t kPointCapacity = 2048;
inline constexpr std::size_t kStepCapacity = 256;
enum class ParseStatus { Ok, InvalidJson, InvalidRpc, IdMismatch, RouteError, Capacity, InvalidRoute,
    ServiceQuota, GeometryRequired };
enum class ServiceState { Ready, Quota, Error, InvalidEnvelope };
struct ServiceEnvelope {
    ServiceState state = ServiceState::InvalidEnvelope;
    unsigned infocode = 0;
    std::string info;
};
struct SummaryStep {
    double distance_m = 0;
    std::optional<double> duration_s;
    std::string instruction;
    std::string orientation;
};
struct RouteSummary {
    Point origin;
    Point destination;
    double total_distance_m = 0;
    std::vector<SummaryStep> steps;
    bool has_geometry = false;
    std::string coord_frame = "GCJ02";
    std::string request_id;
};
enum class Format { Json, Sse };
const char* status_name(ParseStatus status);
struct ParsedRoute {
    Route route;
    double total_distance_m = 0;
    std::string request_id;
};
class RpcStream {
public:
    RpcStream(Format format, std::string request_id);
    ParseStatus feed(std::string_view bytes);
    ParseStatus finish();
    const std::string& response() const { return response_; }
    std::size_t received_bytes() const { return received_bytes_; }
    bool complete() const { return complete_; }
private:
    ParseStatus line_complete();
    ParseStatus event_complete();
    ParseStatus accept_response(std::string_view message);
    Format format_;
    std::string request_id_;
    std::string line_;
    std::string event_;
    std::string response_;
    std::size_t received_bytes_ = 0;
    ParseStatus status_ = ParseStatus::Ok;
    bool complete_ = false;
    bool previous_cr_ = false;
};
ParseStatus parse_walking_response(std::string_view response, std::string_view request_id,
                                  ParsedRoute& candidate);
ServiceEnvelope classify_provider_response(std::string_view response);
ParseStatus parse_walking_summary(std::string_view response, std::string_view request_id,
                                 RouteSummary& summary);
}
