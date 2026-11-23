#include "mcp_client.hpp"
#include "http_fake.hpp"
#include "esp_http_client.h"
#include "test_support.hpp"
#include <iostream>
#include <limits>
using namespace nav::mcp;
namespace {
const std::string provider = R"({"status":"1","info":"OK","infocode":"10000","route":{"paths":[]}})";
const std::string endpoint = "https://mcp.amap.com/mcp?key=0123456789abcdef0123456789abcdef";
HttpResult fetch(http_test::Response response, std::string target = endpoint,
                 nav::Point origin = {114.1000004,22.3000004}) {
    http_test::configure(std::move(response));
    ClientConfig config; config.endpoint = std::move(target);
    EspHttpTransport transport(config);
    return transport.walking_geometry(origin, {114.1010004,22.3000004}, "3");
}
}
int main(int argc, char** argv) {
    const std::string name = argc == 2 ? argv[1] : "";
    try {
        if (name == "cached" || name == "chunked") {
            const auto result = fetch({"application/json", {}, provider, provider.size(), 7, name == "chunked"});
            test::require(result.status == ClientStatus::Ok && result.body == provider, "cached v3 JSON drained completely");
            const auto seen = http_test::observation();
            test::require(seen.url == "https://restapi.amap.com/v3/direction/walking?key=0123456789abcdef0123456789abcdef&origin=114.100000,22.300000&destination=114.101000,22.300000&output=JSON" &&
                seen.method == HTTP_METHOD_GET, "fixed HTTPS target and six decimal provider coordinates");
            test::require(seen.verify_ca && seen.verify_hostname && seen.redirects_disabled, "TLS peer and fixed destination enforced");
            test::require(seen.headers.size() == 1 && seen.headers[0].first == "Accept" &&
                seen.headers[0].second == "application/json", "REST request uses isolated JSON headers");
        } else if (name == "mime") {
            test::require(fetch({"text/html", {}, provider}).status == ClientStatus::Protocol, "v3 MIME checked");
        } else if (name == "quota") {
            const auto result = fetch({"application/json", {}, "quota", 0, 2048, false, 429});
            test::require(result.status == ClientStatus::Quota, "HTTP quota preserved");
        } else if (name == "capacity") {
            test::require(fetch({"application/json", {}, std::string(kResponseCapacity + 1, 'x')}).status ==
                ClientStatus::Capacity, "v3 response bounded");
        } else if (name == "endpoint") {
            test::require(fetch({}, "https://mcp.amap.com.attacker.invalid/mcp?key=abc").status ==
                ClientStatus::Configuration && http_test::observation().url.empty(), "credential source origin checked");
        } else if (name == "key") {
            test::require(fetch({}, "https://mcp.amap.com/mcp?key=abc%26destination%3D1").status ==
                ClientStatus::Configuration && http_test::observation().url.empty(), "credential query syntax checked");
        } else if (name == "duplicate") {
            test::require(fetch({}, endpoint + "&key=other").status == ClientStatus::Configuration &&
                http_test::observation().url.empty(), "single credential identity checked");
        } else if (name == "coordinates") {
            test::require(fetch({}, endpoint, {std::numeric_limits<double>::quiet_NaN(),22.3}).status ==
                ClientStatus::Schema && http_test::observation().url.empty(), "provider coordinates parsed before URL creation");
        } else return 2;
        std::cout << "{\"fixture\":\"" << name << "\",\"result\":\"passed\"}\n";
        return 0;
    } catch (const test::Failure& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
