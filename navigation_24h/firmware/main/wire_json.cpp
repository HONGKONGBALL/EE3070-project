#include "app_runtime.hpp"
#include "ble_link.hpp"
#include "driver/usb_serial_jtag.h"
#include "esp_app_desc.h"
#include "esp_timer.h"
#include "freertos/semphr.h"
#include <cmath>
#include <cstring>
#include <algorithm>

namespace app {
nav::Millis clock_ms() { return esp_timer_get_time() / 1000; }
Json parse(const char* bytes, std::size_t length) {
    if (!bytes || length == 0 || length > 512) return Json(nullptr, cJSON_Delete);
    const std::string owned(bytes, length);
    const char* end = nullptr;
    Json root(cJSON_ParseWithLengthOpts(owned.c_str(), owned.size() + 1, &end, true), cJSON_Delete);
    if (!cJSON_IsObject(root.get()) || end != owned.c_str() + owned.size()) root.reset();
    return root;
}
std::string encode(const cJSON* root) {
    char* text = cJSON_PrintUnformatted(root);
    if (!text) return {};
    std::string value(text); cJSON_free(text); return value;
}
std::string string_field(const cJSON* root, const char* key) {
    const auto* value = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsString(value) && value->valuestring && std::strlen(value->valuestring) <= 96 ? value->valuestring : "";
}
bool number_field(const cJSON* root, const char* key, double& output) {
    const auto* value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(value) || !std::isfinite(value->valuedouble)) return false;
    output = value->valuedouble; return true;
}
bool integer_field(const cJSON* root, const char* key, std::uint64_t& output) {
    double value = 0;
    if (!number_field(root, key, value) || value < 0 || value > 9007199254740991.0 || std::floor(value) != value) return false;
    output = static_cast<std::uint64_t>(value); return true;
}
static void output_to(const std::string& message, bool event, bool bluetooth) {
    static SemaphoreHandle_t mutex = xSemaphoreCreateMutex();
    if (message.empty() || message.size() > 1024 || xSemaphoreTake(mutex, pdMS_TO_TICKS(50)) != pdTRUE) return;
    const auto framed = message + "\n";
    (void)usb_serial_jtag_write_bytes(framed.data(), framed.size(), pdMS_TO_TICKS(20));
    if (bluetooth && message.size() <= 512) (void)ble_link::send(message, event);
    xSemaphoreGive(mutex);
}
void output(const std::string& message, bool event) { output_to(message, event, true); }
void output_usb(const std::string& message) { output_to(message, false, false); }
void pairing_status(const ble_link::PairingStatus& value) {
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", "pairing_status");
    cJSON_AddStringToObject(root.get(), "state", ble_link::pairing::name(value.snapshot.state));
    cJSON_AddStringToObject(root.get(), "command_id", value.command_id.c_str());
    cJSON_AddStringToObject(root.get(), "window_id", value.snapshot.window_id.c_str());
    cJSON_AddNumberToObject(root.get(), "connection_epoch", value.snapshot.identity.epoch);
    if (value.snapshot.number) cJSON_AddNumberToObject(root.get(), "number", *value.snapshot.number);
    cJSON_AddNumberToObject(root.get(), "deadline_ms", value.snapshot.deadline_ms);
    cJSON_AddStringToObject(root.get(), "reason", value.reason.c_str());
    cJSON_AddBoolToObject(root.get(), "accepted", value.accepted);
    output_usb(encode(root.get()));
}
void diagnostic(const char* type, const char* reason) {
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", "diagnostic");
    cJSON_AddStringToObject(root.get(), "category", type);
    cJSON_AddStringToObject(root.get(), "reason", reason);
    cJSON_AddNumberToObject(root.get(), "monotonic_ms", clock_ms());
    cJSON_AddStringToObject(root.get(), "build_id", esp_app_get_description()->version);
    output(encode(root.get()));
}
void status(const nav::Engine& engine) {
    const auto& state = engine.snapshot();
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", "status");
    cJSON_AddStringToObject(root.get(), "session_id", state.session_id.c_str());
    cJSON_AddNumberToObject(root.get(), "generation", state.generation);
    cJSON_AddNumberToObject(root.get(), "route_version", state.route_version);
    cJSON_AddStringToObject(root.get(), "state", nav::name(state.state));
    cJSON_AddStringToObject(root.get(), "quality", nav::name(state.quality));
    cJSON_AddNumberToObject(root.get(), "fix_seq", state.fix_seq);
    cJSON_AddNumberToObject(root.get(), "fix_age_ms", std::min<std::uint64_t>(state.fix_age_ms, 9007199254740991ULL));
    cJSON_AddNumberToObject(root.get(), "event_id", state.current_event_id);
    cJSON_AddNumberToObject(root.get(), "progress_m", state.progress_m);
    cJSON_AddNumberToObject(root.get(), "lateral_m", state.lateral_m);
    cJSON_AddNumberToObject(root.get(), "segment", state.segment);
    cJSON_AddNumberToObject(root.get(), "device_monotonic_ms", clock_ms());
    cJSON_AddStringToObject(root.get(), "ble", ble_link::connected() ? "CONNECTED" : "DISCONNECTED");
    cJSON_AddStringToObject(root.get(), "wifi", wifi_state().c_str());
    cJSON_AddStringToObject(root.get(), "mcp", mcp_state().c_str());
    cJSON_AddStringToObject(root.get(), "config_version", "navigation-1");
    output(encode(root.get()));
}
void event(const nav::Event& value, const nav::Snapshot& snapshot) {
    Json root(cJSON_CreateObject(), cJSON_Delete);
    cJSON_AddStringToObject(root.get(), "type", value.type.c_str());
    cJSON_AddStringToObject(root.get(), "session_id", value.session_id.c_str());
    cJSON_AddNumberToObject(root.get(), "generation", value.generation);
    cJSON_AddNumberToObject(root.get(), "route_version", value.route_version);
    cJSON_AddNumberToObject(root.get(), "event_id", value.event_id);
    cJSON_AddNumberToObject(root.get(), "fix_seq", value.fix_seq);
    cJSON_AddNumberToObject(root.get(), "fix_age_ms", std::min<std::uint64_t>(snapshot.fix_age_ms, 9007199254740991ULL));
    cJSON_AddNumberToObject(root.get(), "distance_m", value.distance_m);
    cJSON_AddNumberToObject(root.get(), "ttl_ms", value.ttl_ms);
    cJSON_AddNumberToObject(root.get(), "issued_at_ms", value.issued_at_ms);
    cJSON_AddStringToObject(root.get(), "confirmation_source", value.confirmation_source.c_str());
    if (!value.reason.empty()) cJSON_AddStringToObject(root.get(), "reason", value.reason.c_str());
    output(encode(root.get()), true);
}
}
