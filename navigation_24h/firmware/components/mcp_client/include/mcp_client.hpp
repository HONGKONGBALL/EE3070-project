#pragma once
#include "route_parser.hpp"
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace nav::mcp {
enum class ClientStatus {
    Ok, Configuration, Transport, Timeout, Tls, Authentication, Quota, Protocol,
    Schema, Service, Route, Capacity
};
const char* status_name(ClientStatus status);
struct ClientConfig {
    std::string endpoint;
    std::string protocol_version = "2025-11-25";
    std::string walking_tool;
    std::string service_alias = "amap";
};
struct HttpRequest {
    std::string body;
    std::string request_id;
    std::string method;
    std::string tool_name;
    std::string protocol_version;
    std::string session_id;
    bool notification = false;
    std::vector<std::pair<std::string, std::string>> extra_headers{};
};
struct HttpResult {
    ClientStatus status = ClientStatus::Ok;
    int http_status = 0;
    std::string body;
    std::string session_id;
    std::uint32_t elapsed_ms = 0;
    unsigned retry_count = 0;
};
ClientStatus classify_http_status(int status);
bool retryable_exchange(const HttpResult& result);
class Transport {
public:
    virtual ~Transport() = default;
    virtual HttpResult post(const HttpRequest& request) = 0;
    virtual HttpResult walking_geometry(Point, Point, std::string_view) {
        HttpResult result; result.status = ClientStatus::Configuration; return result;
    }
};
struct Discovery {
    std::string protocol_version;
    std::string server_info_json;
    std::string walking_tool;
    std::string input_schema_json;
    std::string output_schema_json;
};
struct WalkingReply {
    ClientStatus status = ClientStatus::Ok;
    RequestToken token;
    ParsedRoute candidate;
    HttpResult exchange;
    std::optional<ParseStatus> parse_status;
    bool geometry_fallback_used = false;
};
class Client {
public:
    Client(ClientConfig config, Transport& transport);
    ClientStatus discover();
    WalkingReply walking(const RequestToken& token, Point provider_origin, Point provider_destination);
    const Discovery& discovery() const { return discovery_; }
    const HttpResult& last_exchange() const { return last_; }
    void reset();
private:
    ClientStatus initialize();
    ClientStatus list_tools();
    HttpResult call(std::string_view method, std::string_view params_json,
                    std::string request_id, std::string_view tool = {});
    ClientConfig config_;
    Transport& transport_;
    Discovery discovery_;
    std::string session_id_;
    HttpResult last_;
    std::uint64_t control_seq_ = 0;
    bool ready_ = false;
};
struct ExchangeSummary {
    const char* service_alias;
    const char* method;
    const char* request_id;
    ClientStatus status;
    int http_status;
    unsigned attempt;
    std::uint32_t elapsed_ms;
    std::size_t response_bytes;
};
using ExchangeObserver = void (*)(const ExchangeSummary&, void*);
#ifdef ESP_PLATFORM
class EspHttpTransport final : public Transport {
public:
    explicit EspHttpTransport(ClientConfig config, ExchangeObserver observer = nullptr, void* context = nullptr);
    HttpResult post(const HttpRequest& request) override;
    HttpResult walking_geometry(Point origin, Point destination, std::string_view request_id) override;
private:
    HttpResult attempt(const HttpRequest& request);
    ClientConfig config_;
    ExchangeObserver observer_;
    void* context_;
};
#endif
}
