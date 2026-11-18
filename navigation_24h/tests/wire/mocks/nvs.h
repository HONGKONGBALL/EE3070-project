#pragma once
#include <cstddef>
#include <cstdint>

using esp_err_t = int;
using nvs_handle_t = std::uint32_t;
enum nvs_open_mode_t { NVS_READONLY, NVS_READWRITE };
inline constexpr esp_err_t ESP_OK = 0;
inline constexpr esp_err_t ESP_ERR_NVS_NOT_FOUND = 1;
inline constexpr esp_err_t ESP_ERR_NVS_READ_ONLY = 2;
inline constexpr esp_err_t ESP_ERR_NVS_INVALID_HANDLE = 3;
inline constexpr esp_err_t ESP_ERR_NVS_INVALID_LENGTH = 4;
esp_err_t nvs_open(const char* name, nvs_open_mode_t mode, nvs_handle_t* handle);
esp_err_t nvs_get_str(nvs_handle_t handle, const char* key, char* value, std::size_t* size);
esp_err_t nvs_set_str(nvs_handle_t handle, const char* key, const char* value);
esp_err_t nvs_commit(nvs_handle_t handle);
void nvs_close(nvs_handle_t handle);
