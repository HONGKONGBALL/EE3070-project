#pragma once
#include "nav_core.hpp"
#include "mcp_client.hpp"
#include "cJSON.h"
#include "pairing_control.hpp"
#include <memory>
#include <string>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

namespace app {
using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
struct Input { char bytes[513]{}; std::size_t length = 0; nav::Millis received_at = 0; bool usb = false; std::uint32_t ble_epoch = 0; };
struct NetworkResult {
    nav::mcp::WalkingReply reply;
    const char* failure_reason = nullptr;
};
struct Settings {
    std::string ssid, password, endpoint, walking_tool;
    std::string protocol = "2025-11-25";
    std::string coordinate_policy = "review_required";
    std::string coordinate_control_reference;
    std::string frame = "GCJ02";
    bool integrated = false;
};
Settings settings();
bool provision(const cJSON* root);
void network_task(void*);
void initialize_network(QueueHandle_t requests, QueueHandle_t results);
bool start_button_uart();
std::string wifi_state();
std::string mcp_state();
nav::Millis clock_ms();
void output(const std::string& message, bool event = false);
void output_usb(const std::string& message);
void pairing_status(const ble_link::PairingStatus& status);
Json parse(const char* data, std::size_t length);
std::string encode(const cJSON* value);
std::string string_field(const cJSON* root, const char* key);
bool number_field(const cJSON* root, const char* key, double& value);
bool integer_field(const cJSON* root, const char* key, std::uint64_t& value);
bool handle(nav::Engine& engine, const Input& input);
void status(const nav::Engine& engine);
void event(const nav::Event& event, const nav::Snapshot& snapshot);
void diagnostic(const char* type, const char* reason);
}
