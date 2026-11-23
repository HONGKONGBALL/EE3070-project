#pragma once
#include "nav_core.hpp"
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace nav::coordinates {
enum class Frame { Wgs84, Gcj02, Unknown };
enum class Policy { ReviewRequired, VerifiedIdentity, VerifiedGcj02 };
enum class Status { Ok, ReviewRequired, FrameMismatch, InvalidPoint, ConversionCount, Roundtrip };
Frame parse_frame(std::string_view value);
Policy parse_policy(std::string_view value);
const char* name(Frame frame);
const char* name(Policy policy);
const char* name(Status status);
struct TaggedPoint {
    Point position;
    Frame frame = Frame::Unknown;
    unsigned conversion_count = 0;
};
struct NormalizedRoute {
    Route route;
    std::vector<std::uint8_t> point_conversion_counts;
};
Point wgs84_to_gcj02(Point source);
Point gcj02_to_wgs84(Point source);
class Boundary {
public:
    Boundary(Policy policy, Frame provider_frame, std::string control_reference);
    Status readiness() const;
    Status to_provider(const TaggedPoint& source, TaggedPoint& destination) const;
    Status normalize_route(const Route& source, NormalizedRoute& destination) const;
private:
    Policy policy_;
    Frame provider_frame_;
    std::string control_reference_;
};
}
