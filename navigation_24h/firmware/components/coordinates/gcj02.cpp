#include "coordinates.hpp"
#include <cmath>

// Adapted from wandergis/coordtransform, Copyright (c) 2015 记忆的残骸, MIT.
// Source revision 606c6f3b57b6f1d60458793fea39928d2b11b637; third_party/LICENSE.
namespace nav::coordinates {
namespace {
constexpr double pi = 3.1415926535897932384626;
constexpr double axis = 6378245.0;
constexpr double eccentricity = 0.00669342162296594323;
double latitude_delta(double x, double y) {
    double value = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*std::sqrt(std::abs(x));
    value += (20*std::sin(6*x*pi) + 20*std::sin(2*x*pi))*2/3;
    value += (20*std::sin(y*pi) + 40*std::sin(y/3*pi))*2/3;
    value += (160*std::sin(y/12*pi) + 320*std::sin(y*pi/30))*2/3;
    return value;
}
double longitude_delta(double x, double y) {
    double value = 300 + x + 2*y + .1*x*x + .1*x*y + .1*std::sqrt(std::abs(x));
    value += (20*std::sin(6*x*pi) + 20*std::sin(2*x*pi))*2/3;
    value += (20*std::sin(x*pi) + 40*std::sin(x/3*pi))*2/3;
    value += (150*std::sin(x/12*pi) + 300*std::sin(x/30*pi))*2/3;
    return value;
}
bool transformed_region(Point point) {
    return point.lng > 73.66 && point.lng < 135.05 && point.lat > 3.86 && point.lat < 53.55;
}
}
Point wgs84_to_gcj02(Point source) {
    if (!transformed_region(source)) return source;
    double lat = latitude_delta(source.lng - 105, source.lat - 35);
    double lng = longitude_delta(source.lng - 105, source.lat - 35);
    const double radians = source.lat / 180*pi;
    const double sine = std::sin(radians);
    const double magic = 1 - eccentricity*sine*sine;
    const double root = std::sqrt(magic);
    lat = lat*180 / ((axis*(1 - eccentricity))/(magic*root)*pi);
    lng = lng*180 / (axis/root*std::cos(radians)*pi);
    return {source.lng + lng, source.lat + lat};
}
Point gcj02_to_wgs84(Point source) {
    if (!transformed_region(source)) return source;
    Point estimate = source;
    for (unsigned iteration = 0; iteration < 12; ++iteration) {
        const auto projected = wgs84_to_gcj02(estimate);
        const double lng_error = projected.lng - source.lng;
        const double lat_error = projected.lat - source.lat;
        estimate.lng -= lng_error;
        estimate.lat -= lat_error;
        if (std::abs(lng_error) < 1e-11 && std::abs(lat_error) < 1e-11) break;
    }
    return estimate;
}
}
