#include "mcp_client.hpp"
#include "geometry_completion.hpp"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_timer.h"
#include "esp_tls_errors.h"
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <memory>
#include <strings.h>
namespace nav::mcp {
namespace {
struct GeometryHeaders { std::string mime; };
esp_err_t geometry_header(esp_http_client_event_t* event) {
    if (event->event_id == HTTP_EVENT_ON_HEADER && event->header_key && event->header_value &&
        strcasecmp(event->header_key, "Content-Type") == 0) {
        auto& header = *static_cast<GeometryHeaders*>(event->user_data);
        header.mime.assign(event->header_value, std::min<std::size_t>(std::strlen(event->header_value), 128));
    }
    return ESP_OK;
}
struct GeometryCloser {
    void operator()(esp_http_client* value) const { esp_http_client_cleanup(value); }
};
ClientStatus geometry_error(esp_http_client_handle_t client, int code) {
    int error = 0, flags = 0;
    const auto last = esp_http_client_get_and_clear_last_tls_error(client, &error, &flags);
    if (last == ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT || last == ESP_ERR_ESP_TLS_SERVER_HANDSHAKE_TIMEOUT ||
        code == -ESP_ERR_HTTP_EAGAIN || errno == ETIMEDOUT || errno == EAGAIN) return ClientStatus::Timeout;
    if (flags || error || last >= ESP_ERR_MBEDTLS_CERT_PARTLY_OK) return ClientStatus::Tls;
    return ClientStatus::Transport;
}
HttpResult receive_geometry(std::string_view endpoint, Point origin, Point destination) {
    HttpResult result;
    for (const auto point : {origin, destination})
        if (!std::isfinite(point.lng) || !std::isfinite(point.lat) || point.lng < -180 ||
            point.lng > 180 || point.lat < -90 || point.lat > 90) {
            result.status = ClientStatus::Schema; return result;
        }
    const auto url = detail::walking_geometry_url(endpoint, origin, destination);
    if (!url) { result.status = ClientStatus::Configuration; return result; }
    GeometryHeaders headers;
    esp_http_client_config_t settings{};
    settings.url = url->c_str(); settings.method = HTTP_METHOD_GET;
    settings.timeout_ms = 5000; settings.crt_bundle_attach = esp_crt_bundle_attach;
    settings.skip_cert_common_name_check = false; settings.disable_auto_redirect = true;
    settings.event_handler = geometry_header; settings.user_data = &headers;
    settings.buffer_size = 2048; settings.buffer_size_tx = 1024;
    std::unique_ptr<esp_http_client, GeometryCloser> client(esp_http_client_init(&settings));
    if (!client) { result.status = ClientStatus::Capacity; return result; }
    if (esp_http_client_set_header(client.get(), "Accept", "application/json") != ESP_OK) {
        result.status = ClientStatus::Transport; return result;
    }
    const auto opened = esp_http_client_open(client.get(), 0);
    if (opened != ESP_OK) { result.status = geometry_error(client.get(), opened); return result; }
    const auto started = esp_timer_get_time();
    esp_http_client_set_timeout_ms(client.get(), 15000);
    const auto length = esp_http_client_fetch_headers(client.get());
    if (length < 0) { result.status = geometry_error(client.get(), static_cast<int>(length)); return result; }
    result.http_status = esp_http_client_get_status_code(client.get());
    result.status = classify_http_status(result.http_status);
    if (result.status != ClientStatus::Ok) return result;
    if (length > static_cast<std::int64_t>(kResponseCapacity)) { result.status = ClientStatus::Capacity; return result; }
    if (strncasecmp(headers.mime.c_str(), "application/json", 16) != 0 ||
        (headers.mime.size() > 16 && headers.mime[16] != ';')) { result.status = ClientStatus::Protocol; return result; }
    char bytes[2048];
    for (;;) {
        const auto remaining = 15000 - static_cast<int>((esp_timer_get_time() - started) / 1000);
        if (remaining <= 0) { result.status = ClientStatus::Timeout; return result; }
        esp_http_client_set_timeout_ms(client.get(), remaining);
        const auto count = esp_http_client_read(client.get(), bytes, sizeof(bytes));
        if (count < 0) { result.status = geometry_error(client.get(), count); return result; }
        if (count == 0) {
            if (!esp_http_client_is_complete_data_received(client.get())) result.status = ClientStatus::Transport;
            break;
        }
        if (result.body.size() + static_cast<std::size_t>(count) > kResponseCapacity) {
            result.status = ClientStatus::Capacity; return result;
        }
        result.body.append(bytes, static_cast<std::size_t>(count));
    }
    if (result.body.empty()) result.status = ClientStatus::Protocol;
    return result;
}
}
HttpResult EspHttpTransport::walking_geometry(Point origin, Point destination, std::string_view request_id) {
    const auto start = esp_timer_get_time();
    auto result = receive_geometry(config_.endpoint, origin, destination);
    result.elapsed_ms = static_cast<std::uint32_t>((esp_timer_get_time() - start) / 1000);
    const std::string id(request_id);
    if (observer_) observer_({"amap-walking-geometry", "rest/walking", id.c_str(), result.status,
        result.http_status, 1, result.elapsed_ms, result.body.size()}, context_);
    return result;
}
}
