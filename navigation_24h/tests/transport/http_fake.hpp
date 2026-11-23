#pragma once
#include <cstddef>
#include <string>
#include <utility>
#include <vector>

namespace http_test {
struct Response {
    std::string mime = "application/json";
    std::string session;
    std::string body;
    std::size_t prefetched_bytes = 0;
    std::size_t later_fragment_bytes = 2048;
    bool chunked = false;
    int status = 200;
};
struct Observation {
    unsigned read_calls = 0;
    std::size_t emitted_body_bytes = 0;
    std::size_t prefetched_bytes = 0;
    std::size_t unread_cached_bytes = 0;
    std::string url;
    int method = 0;
    bool verify_ca = false, verify_hostname = false, redirects_disabled = false;
    std::vector<std::pair<std::string, std::string>> headers;
};
void configure(Response response);
Observation observation();
}
