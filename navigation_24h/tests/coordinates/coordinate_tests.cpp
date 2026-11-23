#include "coordinates.hpp"
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
using namespace nav::coordinates;
static void require(bool value, const char* description) {
    if (!value) { std::cerr << description << '\n'; std::exit(1); }
}
static void close(nav::Point actual, nav::Point expected, double tolerance, const char* description) {
    require(std::abs(actual.lng - expected.lng) < tolerance && std::abs(actual.lat - expected.lat) < tolerance, description);
}
int main() {
    const auto reference = wgs84_to_gcj02({116.404, 39.915});
    close(reference, {116.41024449916938, 39.91640428150164}, 1e-10, "independent upstream Beijing fixture");
    close(wgs84_to_gcj02({114.1694,22.3193}), {114.17436706626148,22.316550799399344}, 1e-10, "independent Hong Kong fixture 01");
    close(wgs84_to_gcj02({114.1730,22.2980}), {114.17795593693272,22.295248981798935}, 1e-10, "independent Hong Kong fixture 02");
    close(wgs84_to_gcj02({114.1095,22.3964}), {114.11460300993123,22.393720307600862}, 1e-10, "independent Hong Kong fixture 03");
    for (const auto point : {nav::Point{114.1694,22.3193}, {114.1730,22.2980}, {114.1095,22.3964}, {116.404,39.915}, {0,51.5}})
        close(gcj02_to_wgs84(wgs84_to_gcj02(point)), point, 1e-8, "roundtrip");
    Boundary reviewed(Policy::VerifiedGcj02, Frame::Gcj02, "field-control-fixture");
    TaggedPoint origin{{114.1694,22.3193}, Frame::Wgs84, 0};
    TaggedPoint converted;
    require(reviewed.to_provider(origin, converted) == Status::Ok && converted.frame == Frame::Gcj02 &&
        converted.conversion_count == 1, "one request conversion");
    require(reviewed.to_provider(converted, converted) == Status::FrameMismatch, "source identity check");
    nav::Route route;
    route.coord_frame = "GCJ02";
    route.points = {wgs84_to_gcj02({114.1694,22.3193}), wgs84_to_gcj02({114.1704,22.3193})};
    route.steps = {{1,"straight",false}};
    NormalizedRoute normalized;
    require(reviewed.normalize_route(route, normalized) == Status::Ok, "route normalization");
    close(normalized.route.points[0], origin.position, 1e-8, "route WGS84");
    require(normalized.route.coord_frame == "WGS84" && normalized.point_conversion_counts.size() == 2 &&
        normalized.point_conversion_counts[0] == 1 && normalized.point_conversion_counts[1] == 1, "per-point count");
    require(reviewed.normalize_route(normalized.route, normalized) == Status::FrameMismatch, "route source identity check");
    Boundary pending(Policy::ReviewRequired, Frame::Gcj02, {});
    require(pending.to_provider(origin, converted) == Status::ReviewRequired, "review gate");
    Boundary evidence(Policy::VerifiedGcj02, Frame::Gcj02, {});
    require(evidence.to_provider(origin, converted) == Status::ReviewRequired, "control reference gate");
    Boundary identity(Policy::VerifiedIdentity, Frame::Wgs84, "field-identity-fixture");
    require(identity.to_provider(origin, converted) == Status::Ok && converted.conversion_count == 0, "identity boundary");
    close(converted.position, origin.position, 1e-12, "identity coordinates");
    TaggedPoint invalid{{std::numeric_limits<double>::quiet_NaN(),22.3},Frame::Wgs84,0};
    require(reviewed.to_provider(invalid, converted) == Status::InvalidPoint, "finite coordinates");
    require(parse_policy("verified_gcj02") == Policy::VerifiedGcj02 && parse_frame("GCJ02") == Frame::Gcj02, "typed configuration");
    std::cout << "TC23 coordinate boundary passed\n";
}
