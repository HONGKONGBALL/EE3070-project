#pragma once
#include <cstddef>
#include <cstdint>

using esp_err_t = int;
inline constexpr esp_err_t ESP_OK = 0;
inline constexpr int ESP_ERR_HTTP_EAGAIN = 0x7007;
struct esp_http_client;
using esp_http_client_handle_t = esp_http_client*;
enum esp_http_client_event_id_t { HTTP_EVENT_ON_HEADER, HTTP_EVENT_ON_DATA };
enum esp_http_client_method_t { HTTP_METHOD_POST, HTTP_METHOD_GET };
struct esp_http_client_event_t {
    esp_http_client_event_id_t event_id;
    void* user_data;
    char* header_key;
    char* header_value;
    int data_len;
};
struct esp_http_client_config_t {
    const char* url = nullptr;
    esp_http_client_method_t method = HTTP_METHOD_POST;
    int timeout_ms = 0;
    esp_err_t (*crt_bundle_attach)(void*) = nullptr;
    bool skip_cert_common_name_check = false;
    bool disable_auto_redirect = false;
    esp_err_t (*event_handler)(esp_http_client_event_t*) = nullptr;
    void* user_data = nullptr;
    int buffer_size = 0;
    int buffer_size_tx = 0;
};
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t* config);
esp_err_t esp_http_client_cleanup(esp_http_client_handle_t client);
esp_err_t esp_http_client_set_header(esp_http_client_handle_t client, const char* key, const char* value);
esp_err_t esp_http_client_open(esp_http_client_handle_t client, int write_length);
int esp_http_client_write(esp_http_client_handle_t client, const char* bytes, int length);
esp_err_t esp_http_client_set_timeout_ms(esp_http_client_handle_t client, int timeout);
std::int64_t esp_http_client_fetch_headers(esp_http_client_handle_t client);
int esp_http_client_get_status_code(esp_http_client_handle_t client);
bool esp_http_client_is_complete_data_received(esp_http_client_handle_t client);
int esp_http_client_read(esp_http_client_handle_t client, char* bytes, int length);
esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t client, int* error, int* flags);
