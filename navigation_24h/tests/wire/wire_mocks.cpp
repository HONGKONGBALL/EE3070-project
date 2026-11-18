#include "wire_mocks.hpp"
#include "esp_app_desc.h"
#include "freertos/FreeRTOS.h"
namespace wire_test {
nav::Millis now = 1000;
std::vector<std::string> outputs;
std::vector<ble_link::PairingCommand> pairing_requests;
std::uint32_t connection_epoch = 0;
bool link_authorized = true;
unsigned ble_outputs = 0;
void reset() {
    now = 1000; outputs.clear(); pairing_requests.clear(); connection_epoch = 0;
    link_authorized = true; ble_outputs = 0;
}
}
std::int64_t esp_timer_get_time() { return static_cast<std::int64_t>(wire_test::now * 1000); }
int gpio_get_level(int) { return 1; }
int usb_serial_jtag_write_bytes(const void* bytes, std::size_t length, TickType_t) {
    wire_test::outputs.emplace_back(static_cast<const char*>(bytes), length);
    return static_cast<int>(length);
}
const esp_app_desc_t* esp_app_get_description() {
    static const esp_app_desc_t result{"wire-host-fixture"}; return &result;
}
namespace ble_link {
bool connected() { return wire_test::link_authorized; }
bool send(const std::string&, bool, std::optional<std::uint32_t> expected_epoch) {
    if (expected_epoch && !authorized_epoch(*expected_epoch)) return false;
    ++wire_test::ble_outputs;
    return true;
}
std::uint32_t epoch() { return wire_test::connection_epoch; }
bool authorized_epoch(std::uint32_t value) { return value == epoch() && connected(); }
bool submit_pairing(const PairingCommand& command) { wire_test::pairing_requests.push_back(command); return true; }
}
namespace app {
std::string wifi_state() { return "CONNECTED"; }
std::string mcp_state() { return "READY"; }
}
