#include "coordinates.hpp"
#include <algorithm>
#include <cmath>

namespace nav::coordinates {
Frame parse_frame(std::string_view value) {
    if (value == "WGS84") return Frame::Wgs84;
    if (value == "GCJ02") return Frame::Gcj02;
    return Frame::Unknown;
}
Policy parse_policy(std::string_view value) {
    if (value == "verified_identity") return Policy::VerifiedIdentity;
    if (value == "verified_gcj02") return Policy::VerifiedGcj02;
    return Policy::ReviewRequired;
}
const char* name(Frame frame) {
    switch (frame) {
    case Frame::Wgs84: return "WGS84";
    case Frame::Gcj02: return "GCJ02";
    case Frame::Unknown: return "UNKNOWN";
    }
    return "UNKNOWN";
}
const char* name(Policy policy) {
    switch (policy) {
    case Policy::ReviewRequired: return "review_required";
    case Policy::VerifiedIdentity: return "verified_identity";
    case Policy::VerifiedGcj02: return "verified_gcj02";
    }
    return "review_required";
}
const char* name(Status status) {
    switch (status) {
    case Status::Ok: return "OK";
    case Status::ReviewRequired: return "COORDINATE_REVIEW_REQUIRED";
    case Status::FrameMismatch: return "COORDINATE_FRAME_MISMATCH";
    case Status::InvalidPoint: return "COORDINATE_INVALID_POINT";
    case Status::ConversionCount: return "COORDINATE_CONVERSION_COUNT";
    case Status::Roundtrip: return "COORDINATE_ROUNDTRIP";
    }
    return "COORDINATE_CONFIGURATION";
}
Boundary::Boundary(Policy policy, Frame frame, std::string control_reference)
    : policy_(policy), provider_frame_(frame), control_reference_(std::move(control_reference)) {}
Status Boundary::readiness() const {
    if (policy_ == Policy::ReviewRequired || control_reference_.empty() || control_reference_.size() > 128)
        return Status::ReviewRequired;
    if ((policy_ == Policy::VerifiedIdentity && provider_frame_ != Frame::Wgs84) ||
        (policy_ == Policy::VerifiedGcj02 && provider_frame_ != Frame::Gcj02)) return Status::FrameMismatch;
    return Status::Ok;
}
Status Boundary::to_provider(const TaggedPoint& source, TaggedPoint& destination) const {
    const auto ready = readiness();
    if (ready != Status::Ok) return ready;
    if (source.frame != Frame::Wgs84) return Status::FrameMismatch;
    if (source.conversion_count != 0) return Status::ConversionCount;
    if (!valid_point(source.position)) return Status::InvalidPoint;
    destination = {policy_ == Policy::VerifiedGcj02 ? wgs84_to_gcj02(source.position) : source.position,
                   provider_frame_, policy_ == Policy::VerifiedGcj02 ? 1U : 0U};
    return Status::Ok;
}
Status Boundary::normalize_route(const Route& source, NormalizedRoute& destination) const {
    const auto ready = readiness();
    if (ready != Status::Ok) return ready;
    if (parse_frame(source.coord_frame) != provider_frame_) return Status::FrameMismatch;
    if (source.points.size() < 2 || source.points.size() > 2048 || source.steps.size() > 256)
        return Status::InvalidPoint;
    NormalizedRoute normalized;
    normalized.route = source;
    const bool transform = policy_ == Policy::VerifiedGcj02;
    normalized.point_conversion_counts.assign(source.points.size(), transform ? 1 : 0);
    for (auto& point : normalized.route.points) {
        if (!valid_point(point)) return Status::InvalidPoint;
        if (transform) {
            const auto converted = gcj02_to_wgs84(point);
            const auto projected = wgs84_to_gcj02(converted);
            if (!valid_point(converted) || std::abs(projected.lng - point.lng) > 1e-8 ||
                std::abs(projected.lat - point.lat) > 1e-8) return Status::Roundtrip;
            point = converted;
        }
    }
    normalized.route.coord_frame = "WGS84";
    destination = std::move(normalized);
    return Status::Ok;
}
}
