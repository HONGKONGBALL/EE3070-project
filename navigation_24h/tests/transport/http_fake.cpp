#include "http_fake.hpp"
#include "esp_http_client.h"
#include "freertos/task.h"
#include <algorithm>
#include <cstring>
#include <stdexcept>

struct esp_http_client {
    esp_http_client_config_t config;
    std::size_t parsed_bytes = 0;
    std::size_t cached_read = 0;
};
namespace {
http_test::Response response;
http_test::Observation observed;
std::int64_t now_us = 0;
void event(esp_http_client_handle_t client, esp_http_client_event_id_t id,
           char* key = nullptr, char* value = nullptr, int length = 0) {
    if (!client->config.event_handler) return;
    esp_http_client_event_t message{id, client->config.user_data, key, value, length};
    (void)client->config.event_handler(&message);
}
}
namespace http_test {
void configure(Response value) {
    if (value.body.size() > 256 * 1024 + 1 || value.prefetched_bytes > value.body.size() || !value.later_fragment_bytes)
        throw std::invalid_argument("bounded HTTP fixture");
    response = std::move(value); observed = {}; now_us = 0;
}
Observation observation() { return observed; }
}
std::int64_t esp_timer_get_time() { return now_us += 1000; }
void vTaskDelay(TickType_t ticks) { now_us += static_cast<std::int64_t>(ticks) * 1000; }
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t* config) {
    observed.url = config->url ? config->url : "";
    observed.method = config->method;
    observed.verify_ca = config->crt_bundle_attach != nullptr;
    observed.verify_hostname = !config->skip_cert_common_name_check;
    observed.redirects_disabled = config->disable_auto_redirect;
    return new esp_http_client{*config, 0, 0};
}
esp_err_t esp_http_client_cleanup(esp_http_client_handle_t client) { delete client; return ESP_OK; }
esp_err_t esp_http_client_set_header(esp_http_client_handle_t, const char* key, const char* value) {
    observed.headers.emplace_back(key, value); return ESP_OK;
}
esp_err_t esp_http_client_open(esp_http_client_handle_t, int) { return ESP_OK; }
int esp_http_client_write(esp_http_client_handle_t, const char*, int length) { return length; }
esp_err_t esp_http_client_set_timeout_ms(esp_http_client_handle_t, int) { return ESP_OK; }
std::int64_t esp_http_client_fetch_headers(esp_http_client_handle_t client) {
    std::string mime_key = "Content-Type", session_key = "Mcp-Session-Id";
    event(client, HTTP_EVENT_ON_HEADER, mime_key.data(), response.mime.data());
    if (!response.session.empty()) event(client, HTTP_EVENT_ON_HEADER, session_key.data(), response.session.data());
    client->parsed_bytes = response.prefetched_bytes;
    observed.prefetched_bytes = response.prefetched_bytes;
    observed.unread_cached_bytes = response.prefetched_bytes;
    observed.emitted_body_bytes += response.prefetched_bytes;
    if (response.prefetched_bytes) event(client, HTTP_EVENT_ON_DATA, nullptr, nullptr,
                                        static_cast<int>(response.prefetched_bytes));
    return response.chunked ? 0 : static_cast<std::int64_t>(response.body.size());
}
int esp_http_client_get_status_code(esp_http_client_handle_t) { return response.status; }
bool esp_http_client_is_complete_data_received(esp_http_client_handle_t client) {
    return client->parsed_bytes == response.body.size();
}
int esp_http_client_read(esp_http_client_handle_t client, char* bytes, int capacity) {
    ++observed.read_calls;
    auto available = static_cast<std::size_t>(capacity);
    const auto cached = std::min(response.prefetched_bytes - client->cached_read, available);
    std::memcpy(bytes, response.body.data() + client->cached_read, cached);
    client->cached_read += cached;
    observed.unread_cached_bytes -= cached;
    available -= cached;
    const auto network = std::min({response.body.size() - client->parsed_bytes, available,
                                   response.later_fragment_bytes});
    std::memcpy(bytes + cached, response.body.data() + client->parsed_bytes, network);
    client->parsed_bytes += network;
    observed.emitted_body_bytes += network;
    if (network) event(client, HTTP_EVENT_ON_DATA, nullptr, nullptr, static_cast<int>(network));
    return static_cast<int>(cached + network);
}
esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t, int* error, int* flags) {
    *error = 0; *flags = 0; return ESP_OK;
}
