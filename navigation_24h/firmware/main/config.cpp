#include "app_runtime.hpp"
#include "nvs.h"
#include <array>
#include <cstring>

namespace app {
static std::string read(nvs_handle_t handle, const char* key) {
    std::size_t size = 0;
    if (nvs_get_str(handle, key, nullptr, &size) != ESP_OK || size > 2048) return {};
    std::string value(size, '\0');
    if (nvs_get_str(handle, key, value.data(), &size) != ESP_OK) return {};
    value.resize(size ? size - 1 : 0); return value;
}
Settings settings() {
    Settings result;
    nvs_handle_t handle;
    if (nvs_open("navigation", NVS_READONLY, &handle) != ESP_OK) return result;
    const auto content = read(handle, "config");
    nvs_close(handle);
    Json root(cJSON_Parse(content.c_str()), cJSON_Delete);
    if (!root) return result;
    auto text = [&](const char* key) -> std::string {
        const auto* item = cJSON_GetObjectItemCaseSensitive(root.get(), key);
        return cJSON_IsString(item) && item->valuestring ? item->valuestring : "";
    };
    result.ssid = text("ssid"); result.password = text("password");
    result.endpoint = text("mcp_url"); result.walking_tool = text("walking_tool");
    result.protocol = text("protocol"); result.coordinate_policy = text("coord_policy");
    result.frame = text("route_frame"); result.coordinate_control_reference = text("coord_ref");
    result.integrated = text("integration") == "true";
    return result;
}
bool provision(const cJSON* root) {
    const std::array<const char*, 10> keys{"ssid", "password", "mcp_url", "walking_tool", "protocol", "coord_policy", "route_frame", "config_version", "coord_ref", "integration"};
    for (const auto* key : keys) {
        const auto* value = cJSON_GetObjectItemCaseSensitive(root, key);
        if (!cJSON_IsString(value) || !value->valuestring || std::strlen(value->valuestring) > 1024) return false;
    }
    auto text = [&](const char* key) -> std::string { return cJSON_GetObjectItemCaseSensitive(root, key)->valuestring; };
    if (text("ssid").empty() || text("ssid").size() > 32 || text("password").size() > 64 ||
        text("mcp_url").rfind("https://", 0) != 0 || text("config_version").empty()) return false;
    const auto protocol = text("protocol"), policy = text("coord_policy"), frame = text("route_frame");
    if (protocol != "2025-03-26" && protocol != "2025-06-18" && protocol != "2025-11-25" && protocol != "2026-07-28") return false;
    if (policy != "review_required" && policy != "verified_identity" && policy != "verified_gcj02") return false;
    if ((frame != "WGS84" && frame != "GCJ02") || (text("integration") != "true" && text("integration") != "false")) return false;
    if (policy != "review_required" && text("coord_ref").empty()) return false;
    nvs_handle_t handle;
    if (nvs_open("navigation", NVS_READWRITE, &handle) != ESP_OK) return false;
    bool valid = nvs_set_str(handle, "config", encode(root).c_str()) == ESP_OK;
    if (valid) valid = nvs_commit(handle) == ESP_OK;
    nvs_close(handle); return valid;
}
}
