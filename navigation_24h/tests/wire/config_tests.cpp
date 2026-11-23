#include "wire_mocks.hpp"
#include "nvs_fake.hpp"
#include "nvs.h"
#include "driver/gpio.h"
#include "test_support.hpp"
#include <cstring>
#include <iostream>

namespace {
app::Json configuration() {
    const std::string bytes = R"({"type":"configure","command_id":"usb-config-1","ssid":"fixture-network","password":"fixture-password","mcp_url":"https://fixture.invalid/mcp?key=fixture","walking_tool":"walking","protocol":"2025-11-25","coord_policy":"review_required","route_frame":"GCJ02","config_version":"fixture-v1","coord_ref":"","integration":"true"})";
    return app::parse(bytes.data(), bytes.size());
}
app::Input input(const cJSON* root, bool usb = true) {
    const auto bytes = app::encode(root);
    test::require(bytes.size() <= 512, "fixture fits one complete USB frame");
    app::Input result;
    std::memcpy(result.bytes, bytes.data(), bytes.size());
    result.length = bytes.size(); result.received_at = wire_test::now; result.usb = usb;
    return result;
}
void reset() {
    wire_test::reset(); nvs_test::reset();
    auto previous = configuration();
    cJSON_SetValuestring(cJSON_GetObjectItemCaseSensitive(previous.get(), "ssid"), "previous-network");
    nvs_test::seed("navigation", "config", app::encode(previous.get()));
    nvs_test::seed("nimble_bond", "fixture-peer", "bond-record");
}
app::Json last_output() {
    test::require(!wire_test::outputs.empty(), "wire acknowledgment emitted");
    const auto& bytes = wire_test::outputs.back();
    return app::parse(bytes.data(), bytes.size());
}
void assert_ack(bool accepted, const char* reason) {
    auto ack = last_output();
    test::require(app::string_field(ack.get(), "type") == "ack", "reply is an acknowledgment");
    test::require(app::string_field(ack.get(), "command_id") == "usb-config-1", "ACK correlates request ID");
    test::require(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(ack.get(), "accepted")) == accepted,
                  "ACK carries observed configuration outcome");
    test::require(app::string_field(ack.get(), "reason") == reason, "ACK carries expected configuration reason");
}
}
int main(int argc, char** argv) {
    const std::string requested = argc == 3 && std::string(argv[1]) == "--fixture" ? argv[2] : "";
    const std::vector<test::Case> cases{
        {"usb_idle", "idle_usb_commits_configuration_with_gpio_high", [] {
            reset(); nav::Engine engine; auto root = configuration();
            test::require(gpio_get_level(GPIO_NUM_0) == 1, "GPIO fixture is high");
            test::require(app::handle(engine, input(root.get())), "wired idle configure handled");
            assert_ack(true, "CONFIGURED_RESTART_REQUIRED");
            const auto settings = app::settings();
            test::require(settings.ssid == "fixture-network" && settings.password == "fixture-password" &&
                          settings.endpoint == "https://fixture.invalid/mcp?key=fixture" && settings.integrated,
                          "committed settings round trip through actual reader");
            test::require(nvs_test::stored("nimble_bond", "fixture-peer") == "bond-record",
                          "peer namespace retains committed record");
        }},
        {"gpio_high_ble_origin", "ble_origin_preserves_configuration", [] {
            reset(); nav::Engine engine; auto root = configuration();
            const auto previous = nvs_test::stored("navigation", "config");
            test::require(!app::handle(engine, input(root.get(), false)), "BLE configure disposition");
            test::require(nvs_test::stored("navigation", "config") == previous,
                          "committed USB configuration boundary retained");
            test::require(wire_test::outputs.empty(), "configure ACK belongs to USB entry");
        }},
        {"active_session", "active_session_preserves_configuration", [] {
            reset(); nav::Engine engine; auto root = configuration();
            test::require(engine.start("active", "phone", 1, {"entry", {114, 22}}, wire_test::now).accepted,
                          "navigation session started");
            const auto previous = nvs_test::stored("navigation", "config");
            test::require(!app::handle(engine, input(root.get())), "active configure disposition");
            test::require(nvs_test::stored("navigation", "config") == previous,
                          "active navigation retains committed configuration");
        }},
        {"invalid_fields", "invalid_configuration_preserves_committed_value", [] {
            const std::vector<std::pair<const char*, std::string>> invalid{
                {"ssid", ""}, {"ssid", std::string(33, 's')}, {"password", std::string(65, 'p')},
                {"mcp_url", "http://fixture.invalid"}, {"config_version", ""}, {"protocol", "bad"},
                {"coord_policy", "bad"}, {"route_frame", "bad"}, {"integration", "yes"}};
            for (const auto& entry : invalid) {
                reset(); nav::Engine engine; auto root = configuration();
                cJSON_SetValuestring(cJSON_GetObjectItemCaseSensitive(root.get(), entry.first), entry.second.c_str());
                const auto previous = nvs_test::stored("navigation", "config");
                test::require(app::handle(engine, input(root.get())), "invalid configuration processed");
                assert_ack(false, "CONFIG_CONFIRMATION_REQUIRED");
                test::require(nvs_test::stored("navigation", "config") == previous,
                              std::string("committed value retained for ") + entry.first);
            }
        }},
        {"nvs_commit", "fake_nvs_retains_only_committed_writes", [] {
            reset(); nvs_handle_t handle = 0;
            test::require(nvs_open("fixture", NVS_READWRITE, &handle) == ESP_OK, "namespace opened");
            test::require(nvs_set_str(handle, "key", "pending") == ESP_OK, "value staged");
            nvs_close(handle);
            test::require(nvs_test::stored("fixture", "key").empty(), "closing retains committed boundary");
            test::require(nvs_open("fixture", NVS_READWRITE, &handle) == ESP_OK, "namespace reopened");
            test::require(nvs_set_str(handle, "key", "committed") == ESP_OK && nvs_commit(handle) == ESP_OK,
                          "value committed");
            nvs_close(handle);
            test::require(nvs_test::stored("fixture", "key") == "committed", "commit persists across handles");
        }}
    };
    unsigned failed = 0, selected = 0;
    for (const auto& item : cases) {
        if (!requested.empty() && requested != item.id) continue;
        ++selected;
        try {
            item.run();
            std::cout << "{\"fixture\":\"" << item.name << "\",\"result\":\"passed\"}\n";
        } catch (const test::Failure& failure) {
            ++failed;
            std::cout << "{\"fixture\":\"" << item.name << "\",\"result\":\"failed\",\"detail\":\""
                      << failure.what() << "\"}\n";
        }
    }
    return selected == 0 ? 2 : failed == 0 ? 0 : 1;
}
