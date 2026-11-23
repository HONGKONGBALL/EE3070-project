#include "mcp_client.hpp"
#include "schema_headers.hpp"
#include "client_json.hpp"
#include <cstdlib>
#include <iostream>

using namespace nav::mcp;
static void require(bool condition, const char* description) {
    if (!condition) { std::cerr << description << '\n'; std::exit(1); }
}
int main() {
    require(detail::header_value("provider_walking") == "provider_walking", "ASCII header");
    require(detail::header_value("Hello, 世界") == "=?base64?SGVsbG8sIOS4lueVjA==?=", "UTF8 header");
    require(detail::header_value(" padded ") == "=?base64?IHBhZGRlZCA=?=", "whitespace header");
    require(detail::header_value("=?base64?literal?=") == "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?=", "sentinel header");
    auto schema = detail::parse(R"({"type":"object","properties":{"origin":{"type":"string","x-mcp-header":"Origin","maxLength":30},"destination":{"type":"string"}},"required":["origin","destination"],"additionalProperties":false})");
    auto arguments = detail::parse(R"({"origin":"114.1,22.3","destination":"114.2,22.3"})");
    require(detail::valid_arguments(schema.get(), arguments.get()), "schema arguments");
    std::vector<std::pair<std::string, std::string>> headers;
    require(detail::schema_headers(schema.get(), arguments.get(), headers) && headers.size() == 1 &&
        headers[0].first == "Mcp-Param-Origin" && headers[0].second == "114.1,22.3", "annotated headers");
    auto missing = detail::parse(R"({"origin":"114.1,22.3"})");
    require(!detail::valid_arguments(schema.get(), missing.get()), "required arguments");
    auto duplicate = detail::parse(R"({"type":"object","properties":{"origin":{"type":"string","x-mcp-header":"Place"},"destination":{"type":"string","x-mcp-header":"place"}}})");
    headers.clear();
    require(!detail::schema_headers(duplicate.get(), arguments.get(), headers), "header uniqueness");
    auto unsafe = detail::parse(R"({"type":"object","properties":{"origin":{"type":"string","x-mcp-header":"Place\r\n"}}})");
    headers.clear();
    require(!detail::schema_headers(unsafe.get(), arguments.get(), headers), "header token");
    require(classify_http_status(200) == ClientStatus::Ok, "HTTP success");
    require(classify_http_status(401) == ClientStatus::Authentication && classify_http_status(403) == ClientStatus::Authentication, "TC43 authentication");
    require(classify_http_status(429) == ClientStatus::Quota, "TC43 quota");
    require(classify_http_status(503) == ClientStatus::Service, "TC43 service");
    require(classify_http_status(400) == ClientStatus::Protocol, "TC43 protocol");
    HttpResult failure;
    failure.status = ClientStatus::Tls;
    require(!retryable_exchange(failure), "TC43 TLS lifecycle");
    failure.status = ClientStatus::Timeout;
    require(retryable_exchange(failure), "timeout retry");
    std::cout << "TC43 schema/status fixtures passed\n";
}
