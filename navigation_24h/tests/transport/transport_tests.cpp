#include "mcp_client.hpp"
#include "http_fake.hpp"
#include "esp_http_client.h"
#include "test_support.hpp"
#include <array>
#include <iostream>

namespace {
const std::string rpc = R"({"jsonrpc":"2.0","id":"init-1","result":{"protocolVersion":"2025-11-25","capabilities":{},"serverInfo":{"name":"fixture","version":"1"}}})";
nav::mcp::HttpResult post(http_test::Response response) {
    http_test::configure(std::move(response));
    nav::mcp::ClientConfig config;
    config.endpoint = "https://fixture.invalid/mcp";
    nav::mcp::EspHttpTransport transport(config);
    return transport.post({R"({"jsonrpc":"2.0","id":"init-1","method":"initialize","params":{}})",
                           "init-1", "initialize", {}, "2025-11-25", {}, false});
}
void selected(const nav::mcp::HttpResult& result) {
    test::require(result.status == nav::mcp::ClientStatus::Ok,
                  std::string("RPC body selected; observed ") + nav::mcp::status_name(result.status));
    test::require(result.body == rpc, "selected response equals independent initialize fixture");
    test::require(http_test::observation().unread_cached_bytes == 0, "prefetched bytes drained");
}
}
int main(int argc, char** argv) {
    const std::string requested = argc == 3 && std::string(argv[1]) == "--fixture" ? argv[2] : "";
    const std::vector<test::Case> cases{
        {"sdk_cache", "sdk_completion_precedes_cached_body_drain", [] {
            http_test::configure({"application/json", {}, rpc, rpc.size()});
            esp_http_client_config_t config{};
            auto client = esp_http_client_init(&config);
            test::require(esp_http_client_fetch_headers(client) == static_cast<std::int64_t>(rpc.size()),
                          "fixed response advertises body length");
            test::require(esp_http_client_is_complete_data_received(client), "SDK parser completed cached response");
            std::array<char, 2048> bytes{};
            const auto count = esp_http_client_read(client, bytes.data(), bytes.size());
            test::require(count == static_cast<int>(rpc.size()) && std::string(bytes.data(), count) == rpc,
                          "complete SDK response still yields cached body");
            (void)esp_http_client_cleanup(client);
        }},
        {"cached_json", "initialize_json_cached_with_headers", [] {
            selected(post({"application/json", "session-fixture", rpc, rpc.size()}));
            test::require(http_test::observation().read_calls > 0, "cached response reaches application read");
        }},
        {"cached_chunked_sse", "complete_chunked_sse_cached_with_headers", [] {
            const std::string body = ": keepalive\r\n\r\ndata: " + rpc + "\r\n\r\n";
            selected(post({"text/event-stream", {}, body, body.size(), 2048, true}));
        }},
        {"partial_json", "partial_header_cache_followed_by_fragmented_body", [] {
            selected(post({"application/json", {}, rpc, 13, 7}));
            test::require(http_test::observation().read_calls > 1, "later fragments complete response");
        }},
        {"uncached_json", "initialize_json_arrives_after_headers", [] {
            selected(post({"application/json", {}, rpc, 0}));
        }},
        {"mime_header", "mime_header_validation_preserves_protocol_outcome", [] {
            const auto result = post({"text/html", {}, rpc, rpc.size()});
            test::require(result.status == nav::mcp::ClientStatus::Protocol, "MIME classified at header boundary");
            test::require(http_test::observation().read_calls == 0, "header outcome precedes RPC reading");
        }},
        {"session_header", "session_header_validation_preserves_protocol_outcome", [] {
            const auto result = post({"application/json", "invalid session", rpc, rpc.size()});
            test::require(result.status == nav::mcp::ClientStatus::Protocol, "session classified at header boundary");
            test::require(http_test::observation().read_calls == 0, "session outcome precedes RPC reading");
        }},
        {"empty_json", "empty_http200_body_has_protocol_outcome", [] {
            const auto result = post({"application/json", {}, {}, 0});
            test::require(result.status == nav::mcp::ClientStatus::Protocol && result.body.empty(),
                          "complete empty body retains protocol diagnosis");
        }}
    };
    unsigned failed = 0, selected_count = 0;
    for (const auto& item : cases) {
        if (!requested.empty() && requested != item.id) continue;
        ++selected_count;
        try {
            item.run();
            std::cout << "{\"fixture\":\"" << item.name << "\",\"result\":\"passed\"}\n";
        } catch (const test::Failure& failure) {
            ++failed;
            std::cout << "{\"fixture\":\"" << item.name << "\",\"result\":\"failed\",\"detail\":\""
                      << failure.what() << "\"}\n";
        }
    }
    return selected_count == 0 ? 2 : failed == 0 ? 0 : 1;
}
