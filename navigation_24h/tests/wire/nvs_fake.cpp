#include "nvs_fake.hpp"
#include "nvs.h"
#include <cstring>
#include <map>

namespace {
struct Handle {
    std::string name;
    nvs_open_mode_t mode;
    std::map<std::string, std::string> pending;
};
std::map<std::string, std::map<std::string, std::string>> committed;
std::map<nvs_handle_t, Handle> handles;
nvs_handle_t next_handle = 1;
}
namespace nvs_test {
void reset() { committed.clear(); handles.clear(); next_handle = 1; }
void seed(const std::string& name, const std::string& key, const std::string& value) {
    committed[name][key] = value;
}
std::string stored(const std::string& name, const std::string& key) {
    const auto space = committed.find(name);
    if (space == committed.end()) return {};
    const auto value = space->second.find(key);
    return value == space->second.end() ? std::string{} : value->second;
}
}
esp_err_t nvs_open(const char* name, nvs_open_mode_t mode, nvs_handle_t* result) {
    if (mode == NVS_READONLY && committed.find(name) == committed.end()) return ESP_ERR_NVS_NOT_FOUND;
    *result = next_handle++;
    handles.emplace(*result, Handle{name, mode, {}});
    return ESP_OK;
}
esp_err_t nvs_get_str(nvs_handle_t handle, const char* key, char* value, std::size_t* size) {
    const auto found = handles.find(handle);
    if (found == handles.end()) return ESP_ERR_NVS_INVALID_HANDLE;
    const auto space = committed.find(found->second.name);
    if (space == committed.end()) return ESP_ERR_NVS_NOT_FOUND;
    const auto item = space->second.find(key);
    if (item == space->second.end()) return ESP_ERR_NVS_NOT_FOUND;
    const auto needed = item->second.size() + 1;
    if (!value) { *size = needed; return ESP_OK; }
    if (*size < needed) { *size = needed; return ESP_ERR_NVS_INVALID_LENGTH; }
    std::memcpy(value, item->second.c_str(), needed);
    *size = needed;
    return ESP_OK;
}
esp_err_t nvs_set_str(nvs_handle_t handle, const char* key, const char* value) {
    const auto found = handles.find(handle);
    if (found == handles.end()) return ESP_ERR_NVS_INVALID_HANDLE;
    if (found->second.mode != NVS_READWRITE) return ESP_ERR_NVS_READ_ONLY;
    found->second.pending[key] = value;
    return ESP_OK;
}
esp_err_t nvs_commit(nvs_handle_t handle) {
    const auto found = handles.find(handle);
    if (found == handles.end()) return ESP_ERR_NVS_INVALID_HANDLE;
    if (found->second.mode != NVS_READWRITE) return ESP_ERR_NVS_READ_ONLY;
    for (const auto& entry : found->second.pending) committed[found->second.name][entry.first] = entry.second;
    found->second.pending.clear();
    return ESP_OK;
}
void nvs_close(nvs_handle_t handle) { handles.erase(handle); }
