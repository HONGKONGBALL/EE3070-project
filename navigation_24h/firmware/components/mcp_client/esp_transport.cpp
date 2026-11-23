#include "mcp_client.hpp"
#include "schema_headers.hpp"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_timer.h"
#include "esp_tls_errors.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <algorithm>
#include <cerrno>
#include <climits>
#include <cstring>
#include <memory>
#include <strings.h>

namespace nav::mcp {
namespace {
struct Headers {
    std::string mime;
    std::string session;
    bool valid = true;
};
esp_err_t on_http_event(esp_http_client_event_t* event) {
    if (event->event_id != HTTP_EVENT_ON_HEADER) return ESP_OK;
    auto& headers = *static_cast<Headers*>(event->user_data);
    if (!event->header_key || !event->header_value) return ESP_OK;
    if (strcasecmp(event->header_key, "Content-Type") == 0)
        headers.mime.assign(event->header_value, std::min<std::size_t>(std::strlen(event->header_value), 128));
    if (strcasecmp(event->header_key, "Mcp-Session-Id") == 0) {
        const std::string_view id(event->header_value);
        headers.valid = id.size() <= 256 && !id.empty() &&
            std::all_of(id.begin(), id.end(), [](unsigned char c) { return c >= 0x21 && c <= 0x7e; });
        if (headers.valid) headers.session = std::string(id);
    }
    return ESP_OK;
}
struct HttpCloser {
    void operator()(esp_http_client* client) const { esp_http_client_cleanup(client); }
};
ClientStatus transport_error(esp_http_client_handle_t client, int code) {
    int tls_error = 0, tls_flags = 0;
    const auto last = esp_http_client_get_and_clear_last_tls_error(client, &tls_error, &tls_flags);
    if (last == ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT || last == ESP_ERR_ESP_TLS_SERVER_HANDSHAKE_TIMEOUT ||
        code == -ESP_ERR_HTTP_EAGAIN || errno == ETIMEDOUT || errno == EAGAIN) return ClientStatus::Timeout;
    if (tls_flags || tls_error || (last >= ESP_ERR_MBEDTLS_CERT_PARTLY_OK)) return ClientStatus::Tls;
    return ClientStatus::Transport;
}
}
EspHttpTransport::EspHttpTransport(ClientConfig config, ExchangeObserver observer, void* context)
    : config_(std::move(config)), observer_(observer), context_(context) {}
HttpResult EspHttpTransport::post(const HttpRequest& request) {
    HttpResult result;
    for (unsigned attempt_number = 0; attempt_number <= 2; ++attempt_number) {
        const auto start = esp_timer_get_time();
        result = attempt(request);
        result.elapsed_ms = static_cast<std::uint32_t>((esp_timer_get_time() - start) / 1000);
        result.retry_count = attempt_number;
        if (observer_) observer_({config_.service_alias.c_str(), request.method.c_str(), request.request_id.c_str(),
            result.status, result.http_status, attempt_number + 1, result.elapsed_ms, result.body.size()}, context_);
        if (!retryable_exchange(result) || attempt_number == 2) break;
        vTaskDelay(pdMS_TO_TICKS(500U << attempt_number));
    }
    return result;
}
HttpResult EspHttpTransport::attempt(const HttpRequest& request) {
    HttpResult result;
    if (config_.endpoint.compare(0, 8, "https://") != 0 ||
        config_.endpoint.find_first_of("\r\n#") != std::string::npos || request.body.size() > 16384) {
        result.status = ClientStatus::Configuration; return result;
    }
    Headers headers;
    esp_http_client_config_t settings{};
    settings.url = config_.endpoint.c_str();
    settings.method = HTTP_METHOD_POST;
    settings.timeout_ms = 5000;
    settings.crt_bundle_attach = esp_crt_bundle_attach;
    settings.skip_cert_common_name_check = false;
    settings.disable_auto_redirect = true;
    settings.event_handler = on_http_event;
    settings.user_data = &headers;
    settings.buffer_size = 2048;
    settings.buffer_size_tx = 2048;
    std::unique_ptr<esp_http_client, HttpCloser> client(esp_http_client_init(&settings));
    if (!client) { result.status = ClientStatus::Capacity; return result; }
    const auto set = [&](const char* name, const char* value) {
        return esp_http_client_set_header(client.get(), name, value) == ESP_OK;
    };
    const auto encoded_tool = detail::header_value(request.tool_name);
    if (!set("Content-Type", "application/json") || !set("Accept", "application/json, text/event-stream") ||
        !set("MCP-Protocol-Version", request.protocol_version.c_str()) ||
        (!request.session_id.empty() && !set("Mcp-Session-Id", request.session_id.c_str())) ||
        (request.protocol_version == "2026-07-28" && (!set("Mcp-Method", request.method.c_str()) ||
         (!request.tool_name.empty() && !set("Mcp-Name", encoded_tool.c_str()))))) {
        result.status = ClientStatus::Transport; return result;
    }
    if (request.protocol_version == "2026-07-28") {
        for (const auto& header : request.extra_headers) {
            if (!set(header.first.c_str(), header.second.c_str())) { result.status = ClientStatus::Transport; return result; }
        }
    }
    const auto opened = esp_http_client_open(client.get(), static_cast<int>(request.body.size()));
    if (opened != ESP_OK) { result.status = transport_error(client.get(), opened); return result; }
    std::size_t sent = 0;
    while (sent < request.body.size()) {
        const auto written = esp_http_client_write(client.get(), request.body.data() + sent,
            static_cast<int>(request.body.size() - sent));
        if (written <= 0) { result.status = transport_error(client.get(), written); return result; }
        sent += static_cast<std::size_t>(written);
    }
    const auto read_start = esp_timer_get_time();
    esp_http_client_set_timeout_ms(client.get(), 15000);
    const auto length = esp_http_client_fetch_headers(client.get());
    if (length < 0) { result.status = transport_error(client.get(), static_cast<int>(length)); return result; }
    result.http_status = esp_http_client_get_status_code(client.get());
    result.status = classify_http_status(result.http_status);
    result.session_id = headers.session;
    if (!headers.valid) { result.status = ClientStatus::Protocol; return result; }
    if (length > static_cast<std::int64_t>(kResponseCapacity)) { result.status = ClientStatus::Capacity; return result; }
    if (request.notification) {
        if (result.http_status != 202 || length != 0) result.status = ClientStatus::Protocol;
        return result;
    }
    const bool success = result.status == ClientStatus::Ok;
    if (success && headers.mime.find("application/json") == std::string::npos &&
        headers.mime.find("text/event-stream") == std::string::npos) {
        result.status = ClientStatus::Protocol; return result;
    }
    RpcStream stream(headers.mime.find("text/event-stream") != std::string::npos ? Format::Sse : Format::Json,
                     request.request_id);
    char buffer[2048];
    for (;;) {
        const auto remaining = 15000 - static_cast<int>((esp_timer_get_time() - read_start) / 1000);
        if (remaining <= 0) { result.status = ClientStatus::Timeout; return result; }
        esp_http_client_set_timeout_ms(client.get(), remaining);
        const auto received = esp_http_client_read(client.get(), buffer, sizeof(buffer));
        if (received < 0) { result.status = transport_error(client.get(), received); return result; }
        if (received == 0) {
            if (!esp_http_client_is_complete_data_received(client.get())) result.status = ClientStatus::Transport;
            break;
        }
        if (success) {
            const auto parsed = stream.feed(std::string_view(buffer, static_cast<std::size_t>(received)));
            if (parsed != ParseStatus::Ok) {
                result.status = parsed == ParseStatus::Capacity ? ClientStatus::Capacity : ClientStatus::Protocol;
                return result;
            }
            if (stream.complete()) break;
        } else {
            if (result.body.size() + static_cast<std::size_t>(received) > 16384) break;
            result.body.append(buffer, static_cast<std::size_t>(received));
        }
    }
    if (success && result.status == ClientStatus::Ok) {
        const auto parsed = stream.finish();
        result.status = parsed == ParseStatus::Ok ? ClientStatus::Ok :
            parsed == ParseStatus::Capacity ? ClientStatus::Capacity : ClientStatus::Protocol;
        result.body = stream.response();
    }
    return result;
}
}
